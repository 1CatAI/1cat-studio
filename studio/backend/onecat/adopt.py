# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Adopt a user-owned systemd vLLM service by inspecting its live argv and environment."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx
import psutil

from . import db, engine, gpu, models, runtimes
from .schemas import Profile


def adopt_service(unit: str) -> dict:
    if not re.fullmatch(r"(?:1cat-vllm|onecat-studio)-[a-zA-Z0-9_.@-]+\.service", unit):
        raise ValueError("Only 1Cat systemd user services can be adopted")
    result = subprocess.run(
        ["systemctl", "--user", "show", unit, "--property=MainPID", "--value"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    pid = int(result.stdout.strip() or 0)
    if not pid:
        raise ValueError("The service must be running before it can be adopted")
    process = psutil.Process(pid)
    if process.uids().real != os.getuid():
        raise ValueError("Service is not owned by the Studio user")
    argv = process.cmdline()
    if "serve" not in argv or not any("vllm" in value for value in argv):
        raise ValueError("The service's main process is not a supported vLLM server")
    current = engine.status()
    if current.get("state") == "ready" and current.get("pid") != pid:
        raise ValueError("Stop the currently managed instance before adopting another service")
    parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    parser.add_argument("model_path")
    options = {
        "--host": (str, "127.0.0.1"),
        "--port": (int, 8000),
        "--api-key": (str, None),
        "--served-model-name": (str, "onecat-model"),
        "--tensor-parallel-size": (int, 1),
        "--dtype": (str, "half"),
        "--quantization": (str, None),
        "--kv-cache-dtype": (str, "auto"),
        "--max-model-len": (int, 32768),
        "--max-num-batched-tokens": (int, 4096),
        "--max-num-seqs": (int, 1),
        "--gpu-memory-utilization": (float, 0.8),
        "--attention-backend": (str, None),
        "--speculative-config": (str, None),
    }
    for flag, (type_, default) in options.items():
        parser.add_argument(flag, type=type_, default=default)
    parser.add_argument("--enforce-eager", action="store_true")
    parser.add_argument("--enable-prefix-caching", action="store_true", default=True)
    parser.add_argument(
        "--no-enable-prefix-caching", dest="enable_prefix_caching", action="store_false"
    )
    args, extra = parser.parse_known_args(argv[argv.index("serve") + 1 :])
    if args.host not in {"127.0.0.1", "0.0.0.0", "localhost"}:
        raise ValueError("Service does not expose a supported local endpoint")
    env = process.environ()
    allowed = {
        "PATH",
        "PYTHONPATH",
        "PYTHONNOUSERSITE",
        "CUDA_HOME",
        "CUDA_PATH",
        "CUDA_DEVICE_ORDER",
        "OMP_NUM_THREADS",
        "TORCHINDUCTOR_COMPILE_THREADS",
        "TRITON_CACHE_AUTOTUNING",
        "HF_HOME",
        "HF_DATASETS_CACHE",
        "LD_LIBRARY_PATH",
    }
    imported_env = {
        key: value
        for key, value in env.items()
        if (key in allowed or key.startswith("VLLM_")) and key not in {"VLLM_API_KEY", "HF_TOKEN"}
    }
    key = args.api_key or env.get("VLLM_API_KEY")
    with httpx.Client(timeout=10, trust_env=False) as client:
        response = client.get(
            f"http://127.0.0.1:{args.port}/v1/models",
            headers={"Authorization": f"Bearer {key}"} if key else {},
        )
        response.raise_for_status()
        reported = response.json().get("data", [])
        if not any(m["id"] == args.served_model_name for m in reported):
            raise ValueError("Live API model does not match the service command")
    devices = gpu.snapshot()["gpus"]
    visible = env.get("CUDA_VISIBLE_DEVICES", "").split(",")
    selected = []
    for item in visible:
        item = item.strip()
        if item.startswith("GPU-"):
            selected.append(item)
        elif item.isdigit():
            match = next((d for d in devices if d["index"] == int(item)), None)
            if match:
                selected.append(match["uuid"])
    if len(selected) != args.tensor_parallel_size:
        raise ValueError("Cannot determine the GPU UUID topology from this service")
    existing_runtime = next(
        (r for r in db.all_records("runtimes") if r.get("adopted_service") == unit), {}
    )
    runtime = runtimes.inspect_runtime(
        {
            "id": existing_runtime.get("id", "adopted-" + unit.removesuffix(".service")),
            "name": unit.removesuffix(".service"),
            "python_path": str(Path(argv[0]).absolute()),
            "working_directory": process.cwd(),
            "environment": imported_env,
            "adopted_service": unit,
        }
    )
    model = models.inspect_model(args.model_path)
    profile = Profile(
        name=model["name"],
        runtime_id=runtime["id"],
        source="adopted",
        model_path=args.model_path,
        served_model_name=args.served_model_name,
        gpu_uuids=selected,
        tensor_parallel_size=args.tensor_parallel_size,
        dtype=args.dtype,
        quantization=args.quantization,
        kv_cache_dtype=args.kv_cache_dtype,
        max_model_len=args.max_model_len,
        max_num_batched_tokens=args.max_num_batched_tokens,
        max_num_seqs=args.max_num_seqs,
        gpu_memory_utilization=args.gpu_memory_utilization,
        attention_backend=args.attention_backend,
        enforce_eager=args.enforce_eager,
        enable_prefix_caching=args.enable_prefix_caching,
        speculative_config=json.loads(args.speculative_config) if args.speculative_config else None,
        extra_args=extra,
    ).model_dump()
    profile["id"] = "adopted-" + unit.removesuffix(".service")
    db.put("profiles", profile["id"], profile)
    db.put(
        "engine",
        "active",
        {
            "state": "ready",
            "pid": pid,
            "process_created": process.create_time(),
            "port": args.port,
            "api_key": key,
            "profile_id": profile["id"],
            "profile": profile,
            "identity": engine.identity(profile),
            "runtime_id": runtime["id"],
            "started_at": process.create_time(),
            "last_request_at": time.time(),
            "systemd_unit": unit,
            "supervisor": "systemd_user",
            "adopted_service": True,
        },
    )
    if profile["speculative_config"] and not any(
        p.get("runtime_id") == runtime["id"] and p.get("served_model_name") == "onecat-target-only"
        for p in db.all_records("profiles")
    ):
        base = {
            **profile,
            "id": db.uid(),
            "name": profile["name"] + " · 基础推理",
            "served_model_name": "onecat-target-only",
            "speculative_config": None,
        }
        db.put("profiles", base["id"], base)
    return {"runtime_id": runtime["id"], "profile_id": profile["id"], "model_id": model["id"]}


def reconcile():
    state = engine.status()
    if (
        state.get("adopted_service")
        and state.get("state") == "stopped"
        and not state.get("maintenance")
    ):
        try:
            adopt_service(state["systemd_unit"])
        except (ValueError, OSError, subprocess.SubprocessError, httpx.HTTPError, psutil.Error):
            # A boot-enabled original service may still be loading. Retry after
            # it publishes a healthy API, without starting a competing process.
            pass
