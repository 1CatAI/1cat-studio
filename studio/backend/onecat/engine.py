# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx
import psutil

from . import db
from .config import state_root
from .jobs import Cancelled, Job, same_process
from .schemas import Profile


_ENGINE_FAILURE_MARKERS = re.compile(
    r"(?i)(cuda out of memory|outofmemoryerror|cuda error|nccl|illegal memory access|"
    r"bus error|segmentation fault|sig(?:abrt|bus|kill|segv)|killed process|"
    r"(?:assertion|attribute|index|key|memory|module|name|os|runtime|type|value)error:)"
)
_ENGINE_WRAPPER_ERRORS = (
    "engine core initialization failed",
    "engine startup failed",
    "resource_tracker:",
)


def identity(profile: dict) -> str:
    values = {
        k: v for k, v in profile.items() if k not in {"id", "name", "hardware_profile", "source"}
    }
    runtime = db.get("runtimes", profile.get("runtime_id", ""), {})
    values["runtime_fingerprint"] = {
        k: runtime.get(k) for k in ("python_path", "environment", "capabilities")
    }
    path = Path(profile.get("model_path", ""))
    values["model_files"] = [
        (p.name, p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(path.glob("*"))
        if p.is_file() and p.suffix in {".json", ".safetensors", ".bin"}
    ]
    if (path / "config.json").is_file():
        values["model_config_sha256"] = hashlib.sha256(
            (path / "config.json").read_bytes()
        ).hexdigest()
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def redact_log(text: str, *secret_values: str | None) -> str:
    """Remove launch credentials before logs enter API or task state."""
    for value in secret_values:
        if value:
            text = text.replace(value, "[redacted]")
    text = re.sub(
        r"(?i)(--api-key(?:=|\s+))(?:'[^']*'|\"[^\"]*\"|[^\s,]+)",
        lambda match: match.group(1) + "[redacted]",
        text,
    )
    return re.sub(
        r"(?i)((?:['\"]?api_key['\"]?)\s*[:=]\s*)"
        r"(?:None|'[^']*'|\"[^\"]*\"|[^,\s)}]+)",
        lambda match: match.group(1) + "'[redacted]'",
        text,
    )


def engine_failure_message(path: Path, api_key: str | None = None) -> str:
    """Keep the causal exception instead of only the final vLLM wrapper."""
    text = redact_log(read_log_tail(path, 4 * 1024 * 1024), api_key)
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    candidates = [
        index
        for index, line in enumerate(lines)
        if _ENGINE_FAILURE_MARKERS.search(line)
        and not any(marker in line.lower() for marker in _ENGINE_WRAPPER_ERRORS)
    ]
    if candidates:
        cause = candidates[-1]
        start = max(0, cause - 24)
        for index in range(cause, max(-1, cause - 60), -1):
            if "traceback (most recent call last)" in lines[index].lower():
                start = index
                break
        excerpt = "\n".join(lines[start : min(len(lines), cause + 3)])
    else:
        excerpt = "\n".join(lines[-45:])
    # Job state keeps 3000 characters. Preserve the causal exception at the end.
    excerpt = excerpt[-2700:]
    return "Engine startup failed. Most relevant engine log:\n" + (
        excerpt or "The engine process exited without writing a diagnostic message."
    )


def archive_engine_log(path: Path, launch_id: str | None, api_key: str | None = None):
    """Preserve one redacted log per launch before the shared live log is reused."""
    if not launch_id or not path.is_file() or not path.stat().st_size:
        return None
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", launch_id)
    archived = path.with_name(f"engine-{safe_id}.log")
    archived.write_text(redact_log(path.read_text(errors="replace"), api_key))
    archived.chmod(0o600)
    return archived


def private_state() -> dict:
    return db.get("engine", "active", {"state": "stopped"})


def status() -> dict:
    data = private_state()
    if data.get("pid") and not same_process(data["pid"], data.get("process_created")):
        from .lifecycle import patch_instance

        data = (
            patch_instance(
                data,
                {
                    "state": "failed",
                    "phase": "failed",
                    "pid": None,
                    "error": "Engine process has exited",
                },
            )
            or private_state()
        )
    result = {k: v for k, v in data.items() if k not in {"api_key", "environment"}}
    result["maintenance"] = db.get("engine", "maintenance")
    result["hardware_recovery"] = db.get("engine", "hardware_recovery")
    job = db.get("jobs", data.get("job_id", ""), {})
    result["startup_job"] = {
        k: job[k] for k in ("id", "state", "stage", "cancel_requested", "error") if k in job
    } or None
    from .jobs import TERMINAL, list_jobs

    list_jobs()
    maintenance_job = next(
        (
            j
            for j in sorted(
                db.all_records("jobs"), key=lambda j: j.get("created_at", 0), reverse=True
            )
            if j.get("kind") == "start_model"
            and j["state"] not in TERMINAL
            and not j.get("cancel_requested")
        ),
        {},
    )
    if (
        maintenance_job.get("kind") == "start_model"
        and maintenance_job.get("state") not in {"failed", "cancelled", "completed"}
        and data.get("job_id") != maintenance_job.get("id")
    ):
        # Preparing a launch can take time before a new PID exists. Expose the
        # task without overwriting the still-running instance's process identity.
        target_id = maintenance_job.get("payload", {}).get("profile_id")
        result.update(
            {
                "state": "loading",
                "phase": maintenance_job.get("stage", "checking"),
                "job_id": maintenance_job["id"],
                "profile_id": target_id,
                "profile": db.get("profiles", target_id, {}),
                "detail": maintenance_job.get("detail", ""),
                "phase_progress": None,
                "started_at": maintenance_job.get("created_at", time.time()),
            }
        )
        job = maintenance_job
        result["startup_job"] = {
            k: job[k] for k in ("id", "state", "stage", "cancel_requested", "error") if k in job
        }
    result["elapsed_s"] = max(
        0,
        ((data.get("ready_at") or time.time()) if result["state"] == "ready" else time.time())
        - result.get("started_at", time.time()),
    )
    result["actions"] = {
        "cancel": bool(
            job and job.get("state") not in TERMINAL and result.get("state") == "loading"
        ),
        "stop": bool(data.get("pid") and not result["maintenance"]),
        "retry": data.get("state") in {"failed", "stopped"} and not result["maintenance"],
    }
    return result


def owned_pids() -> set[int]:
    data = private_state()
    if not same_process(data.get("pid"), data.get("process_created")):
        return set()
    try:
        proc = psutil.Process(data["pid"])
        return {proc.pid, *(child.pid for child in proc.children(recursive=True))}
    except psutil.Error:
        return set()


@contextlib.contextmanager
def operation_lock(job: Job):
    with (state_root() / "engine.lock").open("a") as lock:
        while True:
            job.check_cancelled()
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                owner = db.get("engine", "operation", {})
                names = {
                    "creative_load": "画布模型加载 / creative model loading",
                    "creative_stop": "画布模型卸载 / creative model unloading",
                    "start_model": "文本模型加载 / text model loading",
                    "stop_model": "文本模型卸载 / text model unloading",
                    "benchmark": "校准 / calibration",
                    "apply_gpu_settings": "GPU 调节 / GPU adjustment",
                }
                detail = "等待 / Waiting: " + names.get(
                    owner.get("kind"), "其他硬件操作 / another hardware operation"
                )
                job.update("waiting_for_engine", detail=detail, blocking_job_id=owner.get("job_id"))
                time.sleep(0.5)
        db.put("engine", "operation", {"job_id": job.id, "kind": job.record["kind"]})
        try:
            job.update("checking", detail="", blocking_job_id=None)
            yield
        finally:
            if db.get("engine", "operation", {}).get("job_id") == job.id:
                db.delete("engine", "operation")


@contextlib.contextmanager
def maintenance(job: Job):
    with operation_lock(job):
        db.put("engine", "maintenance", {"job_id": job.id, "kind": job.record["kind"]})
        try:
            yield
        finally:
            db.delete("engine", "maintenance")


def active_requests() -> int:
    with db.connect() as conn:
        return conn.execute("SELECT count(*) FROM requests WHERE status IS NULL").fetchone()[0]


def _driver_supports_disable_perf_boost(version: str | None) -> bool:
    """CUDA_DISABLE_PERF_BOOST was added in Linux driver 580.105.08."""
    try:
        parts = tuple(int(part) for part in str(version).split(".")[:3])
    except ValueError:
        return False
    return len(parts) == 3 and parts >= (580, 105, 8)


def drain(job: Job, timeout: float = 1800):
    deadline = time.monotonic() + timeout
    while active_requests():
        job.update("waiting_for_requests", active_requests=active_requests())
        if time.monotonic() > deadline:
            raise RuntimeError(
                "Requests are still running; stop generation before switching models"
            )
        time.sleep(0.3)


def runtime_env(runtime: dict, profile: dict) -> dict:
    from . import gpu

    env = {**os.environ, **runtime.get("environment", {})}
    env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    # PR417 parses CUDA_VISIBLE_DEVICES as integers. Preserve UUIDs in profiles
    # and resolve the current driver numbering immediately before execution.
    devices = gpu.selected_devices(profile["gpu_uuids"]) if profile["gpu_uuids"] else []
    env["CUDA_VISIBLE_DEVICES"] = ",".join(str(d["index"]) for d in devices)
    # A CUDA context otherwise pins V100 at a high performance clock while it
    # has no work. Driver 580's supported heuristic mode lets the resident
    # model fall to the board's 135 MHz idle floor and still boost normally on
    # the next request. A runtime may explicitly opt out for comparison work.
    if (
        "CUDA_DISABLE_PERF_BOOST" not in runtime.get("environment", {})
        and devices
        and all(
            device.get("compute_capability") == [7, 0] and "V100" in str(device.get("name", ""))
            for device in devices
        )
        and _driver_supports_disable_perf_boost(gpu.snapshot().get("driver"))
    ):
        env["CUDA_DISABLE_PERF_BOOST"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    if runtime.get("python_path"):
        python = Path(runtime["python_path"]).expanduser()
        # Imported runtimes can point to a venv interpreter symlink while Ninja
        # and compiler helpers live beside the underlying interpreter. systemd
        # does not activate either environment's bin directory for us.
        paths = [str(python.parent), str(python.resolve().parent)]
        paths.extend(env.get("PATH", os.defpath).split(os.pathsep))
        env["PATH"] = os.pathsep.join(dict.fromkeys(paths))
    # Keep Studio's import path out of the independent inference environment.
    if "PYTHONPATH" not in runtime.get("environment", {}):
        env.pop("PYTHONPATH", None)
    cache = state_root() / "cache" / runtime["id"]
    for key, folder in (
        ("TORCHINDUCTOR_CACHE_DIR", "inductor"),
        ("TRITON_CACHE_DIR", "triton"),
        ("TORCH_EXTENSIONS_DIR", "extensions"),
    ):
        if key not in runtime.get("environment", {}):
            env[key] = str(cache / folder)
    return env


def build_argv(profile: dict, runtime: dict, port: int, api_key: str | None) -> list[str]:
    p = Profile.model_validate(profile)
    command = [
        runtime["python_path"],
        "-m",
        "vllm.entrypoints.cli.main",
        "serve",
        p.model_path,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--served-model-name",
        p.served_model_name,
        "--tensor-parallel-size",
        str(p.tensor_parallel_size),
        "--dtype",
        p.dtype,
        "--kv-cache-dtype",
        p.kv_cache_dtype,
        "--max-model-len",
        str(p.max_model_len),
        "--max-num-batched-tokens",
        str(p.max_num_batched_tokens),
        "--max-num-seqs",
        str(p.max_num_seqs),
        "--gpu-memory-utilization",
        str(p.gpu_memory_utilization),
    ]
    if api_key:
        # URL-safe random keys may begin with '-'; keep them attached to the
        # option so argparse never interprets the key as another CLI flag.
        command.append("--api-key=" + api_key)
    if p.quantization:
        command += ["--quantization", p.quantization]
    if p.attention_backend:
        command += ["--attention-backend", p.attention_backend]
    if p.enforce_eager:
        command.append("--enforce-eager")
    command.append(
        "--enable-prefix-caching" if p.enable_prefix_caching else "--no-enable-prefix-caching"
    )
    if p.speculative_config:
        command += ["--speculative-config", json.dumps(p.speculative_config)]
    capabilities = runtime.get("capabilities", {})
    if (
        capabilities.get("prompt_token_details")
        or capabilities.get("source_snapshot") == "pr417-a09e332bcd"
        or runtime.get("release", {}).get("version") in {"1.3.0", "1.5.0"}
    ):
        command.append("--enable-prompt-tokens-details")
    if p.tool_calling:
        command += ["--enable-auto-tool-choice", "--tool-call-parser", p.tool_parser]
    command += [
        "--limit-mm-per-prompt",
        json.dumps({"image": p.max_images if p.vision_enabled else 0, "video": 0, "audio": 0}),
    ]
    if p.vision_enabled and p.vision_processor_kwargs:
        command += ["--mm-processor-kwargs", json.dumps(p.vision_processor_kwargs)]
    return command + p.extra_args


def command_preview(profile: dict) -> str:
    runtime = db.get("runtimes", profile["runtime_id"])
    if not runtime:
        raise ValueError("Runtime not found")
    return shlex.join(build_argv(profile, runtime, 8000, None))


def stop(job: Job, force: bool = False):
    data = private_state()
    if not force:
        drain(job)
    if data.get("pid"):
        from .lifecycle import patch_instance

        patch_instance(data, {"state": "stopping", "phase": "stopping"})
    if data.get("systemd_unit"):
        job.update("stopping", ignore_cancel=True)
        if data.get("adopted_service"):
            enabled = subprocess.run(
                ["systemctl", "--user", "is-enabled", data["systemd_unit"]],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if enabled == "enabled":
                db.put("adopted_services", data["systemd_unit"], {"was_enabled": True})
                subprocess.run(
                    ["systemctl", "--user", "disable", data["systemd_unit"]],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
        stopped = subprocess.run(
            ["systemctl", "--user", "stop", data["systemd_unit"]],
            capture_output=True,
            timeout=120,
            check=False,
        )
        if stopped.returncode and not (
            stopped.returncode == 5
            and not same_process(data.get("pid"), data.get("process_created"))
        ):
            raise RuntimeError(stopped.stderr.decode(errors="replace"))
    elif same_process(data.get("pid"), data.get("process_created")):
        job.update("stopping", ignore_cancel=True)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(data["pid"], signal.SIGINT)
        deadline = time.monotonic() + 90
        while same_process(data.get("pid"), data.get("process_created")):
            # Once teardown begins it must complete even when the UI cancels.
            if time.monotonic() > deadline:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(data["pid"], signal.SIGKILL)
                break
            time.sleep(0.3)
    db.put(
        "engine",
        "active",
        {
            "state": "stopped",
            "phase": "stopped",
            "last_profile_id": data.get("profile_id"),
            "profile_id": data.get("profile_id"),
            "profile": data.get("profile"),
        },
    )


def validate_profile(profile: dict) -> tuple[dict, dict]:
    from . import gpu

    p = Profile.model_validate(profile).model_dump()
    runtime = db.get("runtimes", p["runtime_id"])
    if not runtime or not runtime.get("validated"):
        raise ValueError("Import or install a validated runtime first")
    path = Path(p["model_path"]).expanduser().resolve()
    if not (path / "config.json").is_file():
        raise ValueError("Model directory must contain config.json")
    json.loads((path / "config.json").read_text())
    if not (list(path.glob("*.safetensors")) or list(path.glob("*.bin"))):
        raise ValueError("Model weights are not present; finish the download first")
    if not p["gpu_uuids"]:
        raise ValueError("Select GPUs before starting")
    from .catalog import validate_features

    validate_features(p)
    devices = gpu.selected_devices(p["gpu_uuids"])
    from .creative.services import owned_pids as creative_pids

    ours = owned_pids() | creative_pids()
    for device in devices:
        if device.get("compute_capability") == [7, 0] and p["dtype"] == "bfloat16":
            raise ValueError("V100 requires half precision, not bfloat16")
        if any(proc["pid"] not in ours for proc in device["processes"]):
            raise ValueError(f"GPU {device['index']} is occupied by another service")
    p["model_path"] = str(path)
    return p, runtime


def launch(command: list[str], runtime: dict, profile: dict, logfile: Path) -> dict:
    """Use a separate systemd cgroup in installed Linux sessions; process groups in development."""
    probe = (
        subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if shutil_which_systemctl()
        else None
    )
    if probe and probe.returncode == 0:
        unit = "onecat-studio-engine-" + db.uid() + ".service"
        launch_dir = state_root() / "jobs" / unit.removesuffix(".service")
        launch_dir.mkdir(mode=0o700)
        payload = launch_dir / "launch.json"
        payload.write_text(
            json.dumps(
                {
                    "argv": command,
                    "env": runtime_env(runtime, profile),
                    "cwd": runtime.get("working_directory"),
                }
            )
        )
        payload.chmod(0o600)
        script = launch_dir / "launch.py"
        script.write_text(
            "import json,os,pathlib\nd=json.loads(pathlib.Path(__file__).with_name('launch.json').read_text())\n"
            "os.chdir(d['cwd'] or '/')\nos.execve(d['argv'][0], d['argv'], d['env'])\n"
        )
        subprocess.run(
            [
                "systemd-run",
                "--user",
                "--quiet",
                "--collect",
                "--unit=" + unit,
                "--property=Type=exec",
                "--property=KillMode=control-group",
                "--property=TimeoutStopSec=90",
                "--property=KillSignal=SIGINT",
                "--property=StandardOutput=append:" + str(logfile),
                "--property=StandardError=append:" + str(logfile),
                runtime["python_path"],
                str(script),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        result = subprocess.run(
            ["systemctl", "--user", "show", unit, "--property=MainPID", "--value"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        pid = int(result.stdout.strip() or 0)
        if not pid:
            raise RuntimeError(engine_failure_message(logfile))
        return {
            "pid": pid,
            "process_created": psutil.Process(pid).create_time(),
            "systemd_unit": unit,
            "supervisor": "systemd_user",
        }
    with logfile.open("wb") as output:
        process = subprocess.Popen(
            command,
            cwd=runtime.get("working_directory") or None,
            env=runtime_env(runtime, profile),
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return {
        "pid": process.pid,
        "process_created": psutil.Process(process.pid).create_time(),
        "supervisor": "process_group",
    }


def shutil_which_systemctl():
    import shutil

    return shutil.which("systemctl") and shutil.which("systemd-run")


def start(job: Job, profile_id: str):
    from . import gpu
    from .lifecycle import healthy, log_progress, patch_instance

    profile = db.get("profiles", profile_id)
    if not profile:
        raise ValueError("Profile not found")
    job.update("checking", phase_progress=None)
    if db.get("engine", "hardware_recovery"):
        gpu.recover_interrupted()
        if db.get("engine", "hardware_recovery"):
            raise ValueError("Hardware recovery is pending; inspect Service status before starting")
    profile, runtime = validate_profile(profile)
    from .creative.services import release_overlapping

    release_overlapping(job, profile["gpu_uuids"])
    current = private_state()
    if current.get("state") == "ready" and current.get("identity") == identity(profile):
        return {"already_running": True, "profile_id": profile_id}
    logfile = state_root() / "logs" / "engine.log"
    stop(job)
    archive_engine_log(
        logfile,
        current.get("launch_id") or current.get("job_id"),
        current.get("api_key"),
    )
    job.check_cancelled()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    key = secrets.token_urlsafe(32)
    command = build_argv(profile, runtime, port, key)
    from .gpu_control import ensure_saved

    ensure_saved(profile["gpu_uuids"])
    try:
        logfile.write_text("")
        ownership = launch(command, runtime, profile, logfile)
        data = {
            "state": "loading",
            "phase": "loading_weights",
            "launch_id": job.id,
            "job_id": job.id,
            **ownership,
            "port": port,
            "api_key": key,
            "profile_id": profile_id,
            "profile": profile,
            "identity": identity(profile),
            "started_at": time.time(),
            "last_request_at": time.time(),
            "runtime_id": runtime["id"],
        }
        db.put("engine", "active", data)
        deadline = time.monotonic() + 1800
        with httpx.Client(timeout=2, trust_env=False) as client:
            while time.monotonic() < deadline:
                job.check_cancelled()
                if not same_process(data["pid"], data["process_created"]):
                    raise RuntimeError(engine_failure_message(logfile, key))
                tail = read_log_tail(logfile)
                current_progress = private_state()
                progress = log_progress(tail, current_progress)
                if any(current_progress.get(k) != v for k, v in progress.items()):
                    patch_instance(data, {**progress, "progress_updated_at": time.time()})
                job.update(
                    progress["phase"],
                    phase_progress=progress["phase_progress"],
                    detail=progress["detail"],
                )
                if healthy(data, client):
                    job.check_cancelled()
                    patch_instance(
                        data,
                        {
                            "state": "ready",
                            "phase": "ready",
                            "ready_at": time.time(),
                            "error": None,
                            "health_failures": 0,
                        },
                    )
                    job.update("ready", 100)
                    return {"profile_id": profile_id, "port": port}
                time.sleep(1)
        raise RuntimeError("Engine did not become ready within 30 minutes")
    except BaseException as error:
        stop(job, force=True)
        archive_engine_log(logfile, job.id, key)
        db.patch(
            "engine",
            "active",
            {
                "state": "stopped" if isinstance(error, Cancelled) else "failed",
                "phase": "stopped" if isinstance(error, Cancelled) else "failed",
                "profile_id": profile_id,
                "profile": profile,
                "job_id": job.id,
                "error": None
                if isinstance(error, Cancelled)
                else redact_log(str(error), key)[-2900:],
            },
        )
        raise


def read_log_tail(path: Path, max_bytes: int = 16000) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - max_bytes))
        return stream.read().decode(errors="replace")
