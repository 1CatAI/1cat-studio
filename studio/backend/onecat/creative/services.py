# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psutil

from .. import db, engine, gpu
from ..config import state_root
from ..jobs import TERMINAL, Cancelled, Job, list_jobs, same_process
from .schema import WORKFLOWS, Service, compatible
from .store import require


class ServiceBusy(ValueError):
    """An operation was refused before changing the running service."""


def pending_lifecycle(identity: str) -> bool:
    list_jobs()
    return any(
        j.get("kind") in {"creative_load", "creative_stop"}
        and j.get("state") not in TERMINAL
        and j.get("payload", {}).get("service_id") == identity
        for j in db.all_records("jobs")
    )


def endpoint(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Provide an HTTP(S) service address without embedded credentials or query")
    return value.strip().rstrip("/").removesuffix("/v1")


def runtime_support(runtime: dict, kind: str = "h3-local") -> bool:
    source = Path(runtime.get("capabilities", {}).get("source_path", ""))
    if source.name == "__init__.py":
        source = source.parent
    if kind == "image-local":
        return bool(runtime.get("validated") and (source / "image" / "server.py").is_file()
                    and (source / "model_executor" / "models" / "z_image" / "pipeline.py").is_file())
    return (
        runtime.get("validated", False)
        and (source / "video" / "server.py").is_file()
        and (source / "video" / "protocol.py").is_file()
    )


def local_paths(record: dict):
    root = Path(record["model"]).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(
            "Download or import the local H3 model first; remote model IDs are not accepted"
        )
    if record["kind"] == "image-local":
        if record.get("checkpoint") not in {"z-image-turbo", "z-image"}:
            raise ValueError("Choose a supported native image checkpoint")
        for folder in ("transformer", "text_encoder", "tokenizer", "vae", "scheduler"):
            if not (root / folder).is_dir():
                raise ValueError(f"Missing local image component: {folder}")
        record["model"] = str(root)
        return
    for folder in ("text_encoder", "tokenizer", "processor", "video_vae", "audio_vae"):
        if not (root / "FL2VA" / folder).is_dir():
            raise ValueError(f"H3 shared component is missing: FL2VA/{folder}")
    part = "FL2VA" if record["partition"] == "fl2va" else "Ref2VA"
    if not (root / part / "transformer" / "config.json").is_file():
        raise ValueError(f"Missing {part}/transformer/config.json")
    if not record.get("transformer_path") and not list(
        (root / part / "transformer").glob("*.safetensors")
    ):
        raise ValueError("Choose a local transformer checkpoint or download the original weights")
    record["model"] = str(root)
    for key in ("transformer_path", "lora_path"):
        if record.get(key):
            path = Path(record[key]).expanduser().resolve()
            if not path.is_file() or path.suffix != ".safetensors":
                raise ValueError(f"{key} must refer to a downloaded local safetensors file")
            if key == "lora_path" and path.name == "adapter_model.safetensors":
                from .fasth3 import validate_adapter

                validate_adapter(record, path)
                record[key] = str(path)
                continue
            if key == "lora_path" and record["attention_backend"] == "FASTVIDEO_VSA":
                raise ValueError("VSA requires the official FastH3 VSA adapter and original weights")
            if key == "lora_path" and (
                "comfyui" in path.name or not path.name.startswith("minimax_h3_")
            ):
                raise ValueError("H3 requires the original LightX2V Diffusers adapter filename")
            if key == "lora_path" and ("ref2v" in path.name) != (record["partition"] == "ref2va"):
                raise ValueError("LoRA and H3 partition do not match")
            record[key] = str(path)
    if record["attention_backend"] == "FASTVIDEO_VSA" and not record.get("lora_path"):
        raise ValueError("VSA requires an explicit FastH3 VSA adapter")


def save(value: Service) -> dict:
    record = value.model_dump(exclude={"api_key"})
    record["id"] = value.id or db.uid()
    old = db.get("creative_services", record["id"])
    if old:
        if pending_lifecycle(record["id"]):
            raise ValueError(
                "Wait for the queued service operation before editing its configuration"
            )
        if old["kind"] in {"h3-local", "image-local"} and requires_stop(record["id"]):
            raise ValueError(
                "Stop this creative service before editing its loaded weights or GPU selection"
            )
        if any(
            r.get("service_id") == record["id"] and r.get("state") not in TERMINAL
            for r in db.all_records("creative_runs")
        ):
            raise ValueError("This service has pending creative tasks")
    if value.kind in {"h3-local", "image-local"}:
        runtime = require("runtimes", value.runtime_id)
        if not runtime_support(runtime, record["kind"]):
            raise ValueError(
                "This environment lacks the native H3 media API. The 1.5.0 wheel does not include H3; import a compatible development environment in Setup."
            )
        if value.kind == "image-local" and len(value.gpu_uuids) != 1:
            raise ValueError("Native Z-Image requires exactly one GPU")
        if not value.gpu_uuids or len(value.gpu_uuids) != len(set(value.gpu_uuids)):
            raise ValueError("Select an explicit set of GPUs")
        known = {g["uuid"] for g in gpu.snapshot()["gpus"]}
        if not set(value.gpu_uuids) <= known:
            raise ValueError("A selected GPU is unavailable")
        local_paths(record)
        from .fasth3 import is_service, supported

        if is_service(record) and not supported(runtime, record["attention_backend"] == "FASTVIDEO_VSA"):
            raise ValueError("This runtime does not contain the required native FastH3 kernels")
        record["base_url"] = ""
    else:
        record["base_url"] = endpoint(value.base_url)
        if not value.model.strip():
            raise ValueError("Enter the exact model identifier exposed by the generation service")
        record["gpu_uuids"] = []
    if value.api_key is not None:
        db.put("secrets", "creative-" + record["id"], {"api_key": value.api_key})
    record["updated_at"] = time.time()
    db.put("creative_services", record["id"], record)
    if old != record and instance(record["id"]).get("state") != "loading":
        db.put(
            "creative_instances",
            record["id"],
            {"state": "stopped" if value.kind in {"h3-local", "image-local"} else "unchecked"},
        )
    return public(record)


def instance(identity: str) -> dict:
    result = db.get("creative_instances", identity, {"state": "stopped"})
    if (
        result.get("pid")
        and not same_process(result["pid"], result.get("process_created"))
        and result.get("state") not in {"stopped", "failed"}
    ):
        result = db.patch(
            "creative_instances",
            identity,
            {"state": "failed", "error": "Creative service process exited; load it again"},
        )
    return result


def public(record: dict) -> dict:
    from .output_sizes import service_sizes

    current = instance(record["id"])
    # Include the queued task even before it acquires the hardware lock or has a PID.
    list_jobs()
    pending = next(
        (
            j
            for j in sorted(
                db.all_records("jobs"), key=lambda j: j.get("created_at", 0), reverse=True
            )
            if j["kind"] in {"creative_load", "creative_stop"}
            and j["state"] not in TERMINAL
            and j["payload"].get("service_id") == record["id"]
        ),
        None,
    )
    if pending and not (
        current.get("state") == "failed" and current.get("job_id") == pending["id"]
    ):
        current = {
            **current,
            "job_id": pending["id"],
            "state": "stopping" if pending["kind"] == "creative_stop" else "loading",
            "phase": pending.get("stage", "queued"),
            "detail": pending.get("detail", ""),
            "phase_progress": pending.get("phase_progress"),
            "cancel_requested": pending.get("cancel_requested"),
            "started_at": pending.get("created_at", time.time()),
        }
    current["elapsed_s"] = max(
        0,
        (time.time() if pending else current.get("ready_at") or time.time())
        - current.get("started_at", time.time()),
    )
    current["can_stop"] = record["kind"] in {"h3-local", "image-local"} and (
        bool(pending) or requires_stop(record["id"])
    )
    return {
        **record,
        "output_sizes": service_sizes(record) if record["kind"] in {"h3-local", "image-local"} else None,
        "has_key": bool(db.get("secrets", "creative-" + record["id"], {}).get("api_key")),
        "workflows": [w["id"] for w in WORKFLOWS if compatible(record, w)],
        "instance": current,
        "experimental": record["kind"].startswith("h3-"),
    }


def requires_stop(identity: str) -> bool:
    status = instance(identity)
    return (
        status.get("state") in {"ready", "loading", "stopping", "unavailable"}
        or same_process(status.get("pid"), status.get("process_created"))
        or bool(processes(status))
    )


def processes(status: dict) -> dict[int, float]:
    """Only descendants of a verified launch or previously recorded identities."""
    result = {
        int(p): created
        for p, created in status.get("owned_processes", {}).items()
        if same_process(int(p), created)
    }
    pid = status.get("pid")
    if same_process(pid, status.get("process_created")):
        try:
            proc = psutil.Process(pid)
            if proc.cmdline() == status.get("argv") and os.getpgid(pid) == pid:
                result.update(
                    {p.pid: p.create_time() for p in [proc, *proc.children(recursive=True)]}
                )
        except (psutil.Error, ProcessLookupError):
            pass
    return result


def load_progress(text, previous):
    # H3's HTTP parent announces "Waiting for application startup" before its
    # distributed workers load weights. That is not a readiness milestone.
    from ..lifecycle import log_progress

    ranks = dict(previous.get("loading_components", {}))
    component = ""
    world_size = 1
    # Multiple workers share stdout and may append their JSON records before
    # another worker writes its newline. Decode each marked object separately.
    decoder = json.JSONDecoder()
    for fragment in text.split("ONECAT_MEDIA_PROGRESS ")[1:]:
        try:
            value, _ = decoder.raw_decode(fragment.lstrip())
            done, total = value["completed"], value["total"]
            rank, world = value.get("rank", 0), value.get("world_size", 1)
            if (type(done) is int and type(total) is int and 0 <= done <= total
                    and total > 0 and type(rank) is int and type(world) is int
                    and 0 <= rank < world <= 32):
                ranks[str(rank)] = {"done":done,"total":total,"world_size":world}
                component = str(value.get("component", ""))
                world_size = world
        except (ValueError, KeyError, TypeError):
            continue
    if ranks:
        world_size = max(r["world_size"] for r in ranks.values())
        total_per_rank = max(r["total"] for r in ranks.values())
        done, total = sum(r["done"] for r in ranks.values()), total_per_rank * world_size
        names = {"transformer":"加载去噪网络 / Loading denoising network", "text_encoder":"加载文本编码器 / Loading text encoder", "vae":"加载图片解码器 / Loading image decoder", "video_vae":"加载视频解码器 / Loading video decoder", "audio_vae":"加载音频解码器 / Loading audio decoder", "ready":"等待健康检查 / Waiting for health check"}
        return {"phase":"checking_service" if done == total else "loading_weights",
                "detail":names.get(component, component) or previous.get("detail", ""),
                "loading_components":ranks,
                "phase_progress":{"done":done,"total":total,"percent":100 * done / total}}
    lines = []
    for line in re.split(r"[\r\n]", text):
        if (
            "waiting for application startup" in line.lower()
            or "auto-setting" in line.lower()
            or "[vllm.py:" in line
        ):
            continue
        lines.append(line)
    return log_progress("\n".join(lines), previous)


def owned_pids() -> set[int]:
    return {
        pid
        for s in db.all_records("creative_services")
        if s["kind"] in {"h3-local", "image-local"}
        for pid in processes(instance(s["id"]))
    }


def mark_stopping(identity, allow_waiting_preparations=False):
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM records WHERE bucket='creative_runs' AND json_extract(data,'$.service_id')=? AND json_extract(data,'$.state') NOT IN ('completed','failed','cancelled') "
            "AND (?=0 OR json_extract(data,'$.preparation_id') IS NULL OR json_extract(data,'$.upstream_id') IS NOT NULL) LIMIT 1",
            (identity, int(allow_waiting_preparations)),
        ).fetchone():
            raise ServiceBusy(
                "请先完成或取消创作任务，再切换模型 / Wait for creative tasks to finish or cancel them before switching models"
            )
        row = conn.execute(
            "SELECT data FROM records WHERE bucket='creative_instances' AND id=?", (identity,)
        ).fetchone()
        value = json.loads(row[0]) if row else {}
        value.update(state="stopping", phase="stopping")
        conn.execute(
            "INSERT OR REPLACE INTO records VALUES('creative_instances',?,?,?)",
            (identity, json.dumps(value), time.time()),
        )


def release_overlapping(job, uuids, keep=None):
    for record in db.all_records("creative_services"):
        if (
            record["id"] == keep
            or record["kind"] not in {"h3-local", "image-local"}
            or not set(uuids).intersection(record.get("gpu_uuids", []))
        ):
            continue
        if requires_stop(record["id"]):
            mark_stopping(record["id"], allow_waiting_preparations=bool(job.payload.get("generation_run_id")))
            job.update("releasing_model", detail="释放 / Unloading: " + record["name"])
            stop_owned(record["id"])


def client(record: dict, timeout=30) -> httpx.Client:
    base = (
        instance(record["id"]).get("base_url")
        if record["kind"] in {"h3-local", "image-local"}
        else record["base_url"]
    )
    if not base:
        raise ValueError("Load this creative service first")
    key = db.get("secrets", "creative-" + record["id"], {}).get("api_key")
    # Never forward a Studio login cookie; credentials belong to this configured origin only.
    return httpx.Client(
        base_url=endpoint(base),
        headers={"Authorization": "Bearer " + key} if key else {},
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )


def check(record: dict) -> dict:
    with client(record, timeout=5) as http:
        if record["kind"].startswith("h3-"):
            health = http.get("/health")
            health.raise_for_status()
            if health.json().get("partition") != record["partition"]:
                raise ValueError(
                    "The running H3 partition differs from this workflow; load the matching service"
                )
        if record["kind"] == "image-local":
            health = http.get("/health")
            health.raise_for_status()
            if health.json().get("checkpoint") != record["checkpoint"]:
                raise ValueError("The loaded image checkpoint differs from this recipe")
        response = http.get("/v1/models")
        response.raise_for_status()
        expected = record.get("checkpoint") if record["kind"] == "image-local" else record["model"]
        if expected not in [m.get("id") for m in response.json().get("data", [])]:
            raise ValueError("The selected model is not exposed by this service")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        latest = conn.execute(
            "SELECT data FROM records WHERE bucket='creative_services' AND id=?", (record["id"],)
        ).fetchone()
        if not latest or json.loads(latest[0]) != record:
            raise ValueError("Service configuration changed during the health check")
        row = conn.execute(
            "SELECT data FROM records WHERE bucket='creative_instances' AND id=?", (record["id"],)
        ).fetchone()
        value = json.loads(row[0]) if row else {}
        if record["kind"] in {"h3-local", "image-local"} and (
            value.get("state") in {"stopped", "stopping"}
            or not same_process(value.get("pid"), value.get("process_created"))
        ):
            raise ValueError("The checked model instance has stopped")
        value.update(
            state="ready",
            phase="ready",
            error=None,
            checked_at=time.time(),
            ready_at=value.get("ready_at") or time.time(),
        )
        conn.execute(
            "INSERT OR REPLACE INTO records VALUES('creative_instances',?,?,?)",
            (record["id"], json.dumps(value), time.time()),
        )
    return value


def stop_owned(identity: str):
    status = instance(identity)
    pid = status.get("pid")
    if same_process(pid, status.get("process_created")):
        proc = psutil.Process(pid)
        command = proc.cmdline()
        if command != status.get("argv") or os.getpgid(pid) != pid:
            raise ValueError("Process identity changed; refusing to stop an unrelated service")
    tracked = processes(status)
    db.patch("creative_instances", identity, {"owned_processes": tracked})
    for child, created in tracked.items():
        if same_process(child, created):
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while any(same_process(p, c) for p, c in tracked.items()) and time.monotonic() < deadline:
        time.sleep(0.1)
    # Loading workers may not service SIGTERM promptly. Escalate only identities
    # captured from our own launch; never send broad GPU/Python kill commands.
    for child, created in tracked.items():
        if same_process(child, created):
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while any(same_process(p, c) for p, c in tracked.items()) and time.monotonic() < deadline:
        time.sleep(0.1)
    if any(same_process(p, c) for p, c in tracked.items()):
        raise ValueError(
            "模型进程尚未退出，请重试卸载 / Model processes have not exited; retry unloading"
        )
    db.patch(
        "creative_instances",
        identity,
        {"state": "stopped", "phase": "stopped", "pid": None, "owned_processes": {}, "error": None},
    )


def lifecycle(job: Job):
    try:
        result = _lifecycle(job)
        db.patch("creative_instances", job.payload["service_id"], {"operation_error": None})
        return result
    except ServiceBusy as error:
        db.patch("creative_instances", job.payload["service_id"], {"operation_error": str(error)})
        raise
    except Cancelled:
        # Cancelling a queued operation has not changed the running service.
        # A cancelled load that owns a new instance performs its own teardown.
        raise
    except BaseException as error:
        identity = job.payload["service_id"]
        db.patch(
            "creative_instances",
            identity,
            {"state": "failed", "error": str(error) or type(error).__name__, "job_id": job.id},
        )
        raise


async def monitor():
    """Reconcile process and HTTP health only; never start models or change GPUs."""
    while True:
        for record in db.all_records("creative_services"):
            current = instance(record["id"])
            if current.get("state") not in {"ready", "loading", "unavailable"}:
                continue
            try:
                await asyncio.to_thread(check, record)
            except (httpx.HTTPError, ValueError, OSError) as error:
                if current["state"] in {"ready", "unavailable"}:
                    # A delayed check must not overwrite a newer stop, edit,
                    # deletion or successful health check from another worker.
                    with db.connect() as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        latest = conn.execute(
                            "SELECT data FROM records WHERE bucket='creative_services' AND id=?",
                            (record["id"],),
                        ).fetchone()
                        row = conn.execute(
                            "SELECT data FROM records WHERE bucket='creative_instances' AND id=?",
                            (record["id"],),
                        ).fetchone()
                        if (
                            latest
                            and row
                            and json.loads(latest[0]) == record
                            and json.loads(row[0]) == current
                        ):
                            current.update(
                                state="unavailable",
                                error=str(error) or "Creative service health check failed",
                            )
                            conn.execute(
                                "UPDATE records SET data=?,updated=? WHERE bucket='creative_instances' AND id=?",
                                (json.dumps(current), time.time(), record["id"]),
                            )
        await asyncio.sleep(10)


def _lifecycle(job: Job):
    record = require("creative_services", job.payload["service_id"])
    if record["kind"] not in {"h3-local", "image-local"}:
        job.update("checking_service")
        return check(record)
    with engine.operation_lock(job):
        if job.record["kind"] == "creative_stop":
            job.update(
                "stopping", detail="卸载模型并释放显存 / Unloading model and releasing GPU memory"
            )
            mark_stopping(record["id"])
            stop_owned(record["id"])
            return {"state": "stopped"}
        current = instance(record["id"])
        if current.get("state") == "ready":
            return check(record)
        if same_process(current.get("pid"), current.get("process_created")):
            raise ServiceBusy(
                "An owned service is still starting or stopping; inspect its existing task"
            )
        runtime = require("runtimes", record["runtime_id"])
        if not runtime_support(runtime, record["kind"]):
            raise ValueError("The runtime no longer provides the H3 media API")
        from .fasth3 import is_service, supported

        if is_service(record) and not supported(runtime, record["attention_backend"] == "FASTVIDEO_VSA"):
            raise ValueError("The runtime no longer provides the required FastH3 kernels")
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise ValueError("H3 needs FFmpeg and FFprobe on the server before loading")
        local_paths(record)
        if job.payload.get("generation_run_id"):
            from .generations import validate_switch
            validate_switch({"gpu_uuids": record["gpu_uuids"], "service_id": record["id"]},
                            job.payload.get("allowed_impacts", []))
        devices = gpu.selected_devices(record["gpu_uuids"])
        ours = owned_pids() | engine.owned_pids()
        if any(p["pid"] not in ours for g in devices for p in g.get("processes", [])):
            raise ValueError(
                "所选 GPU 被外部程序占用，请释放后重试 / Selected GPUs are in use by an external program; release them first."
            )
        release_overlapping(job, record["gpu_uuids"], keep=record["id"])
        text = engine.private_state()
        if set(record["gpu_uuids"]).intersection((text.get("profile") or {}).get("gpu_uuids", [])):
            job.update("releasing_model", detail="释放文本模型 / Unloading text model")
            db.put("engine", "maintenance", {"job_id": job.id, "kind": "creative_load"})
            try:
                engine.stop(job)
            finally:
                if db.get("engine", "maintenance", {}).get("job_id") == job.id:
                    db.delete("engine", "maintenance")
        job.check_cancelled()
        from ..gpu_control import ensure_saved

        ensure_saved(record["gpu_uuids"])
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        output = state_root() / "jobs" / "creative-output" / record["id"]
        output.mkdir(parents=True, exist_ok=True)
        argv = [
            runtime["python_path"],
            "-m",
            "vllm.entrypoints.cli.video",
            "video",
            "serve",
            "--model",
            record["model"],
            "--partition",
            record["partition"],
            "--tensor-parallel-size",
            str(len(devices)),
            "--attention-backend",
            record["attention_backend"],
            "--output-dir",
            str(output),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        if record["kind"] == "image-local":
            argv = [runtime["python_path"], "-m", "vllm.entrypoints.cli.image", "image",
                    "--model", record["model"], "--checkpoint", record["checkpoint"],
                    "--output-dir", str(output), "--host", "127.0.0.1", "--port", str(port)]
        for key in ("transformer_path", "lora_path"):
            if record.get(key):
                argv += ["--" + key.replace("_", "-"), record[key]]
        from .fastpath import launch_options

        fast_options = launch_options(runtime, record, devices)
        argv.extend(fast_options)
        job.update("loading_weights", detail="正在启动模型进程 / Starting model process", phase_progress=None)
        logfile = state_root() / "logs" / f"creative-{record['id']}.log"
        engine.archive_engine_log(logfile, current.get("job_id"))
        with logfile.open("wb") as log:
            proc = subprocess.Popen(
                argv,
                env=engine.runtime_env(runtime, record),
                cwd=runtime.get("working_directory") or None,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        db.put(
            "creative_instances",
            record["id"],
            {
                "state": "loading",
                "phase": "loading_weights",
                "pid": proc.pid,
                "process_created": psutil.Process(proc.pid).create_time(),
                "argv": argv,
                "execution_profile": "fasth3-vsa-experimental"
                if record.get("attention_backend") == "FASTVIDEO_VSA"
                else "sm70-dense-v1" if fast_options else "standard",
                "base_url": f"http://127.0.0.1:{port}",
                "job_id": job.id,
                "started_at": time.time(),
            },
        )
        try:
            # Long compile/load has no silence timeout; exit and health are authoritative.
            while proc.poll() is None:
                job.check_cancelled()
                status = instance(record["id"])
                progress = load_progress(engine.read_log_tail(logfile), status)
                tracked = processes(status)
                # A long load is valid while its workers live. An observed rank
                # disappearing is a failure even if the HTTP parent keeps waiting.
                ranks = status.get("loading_workers", {})
                for pid in tracked:
                    with contextlib.suppress(psutil.Error):
                        if "multiprocessing.spawn" in " ".join(psutil.Process(pid).cmdline()):
                            ranks[str(pid)] = tracked[pid]
                if any(not same_process(int(p), c) for p, c in ranks.items()):
                    raise ValueError(
                        "H3 加载工作进程已退出 / H3 loading worker exited.\n"
                        + engine.engine_failure_message(logfile)
                    )
                if not progress["detail"]:
                    progress["detail"] = (
                        "后台进程运行中，正在加载模型 / Background processes are running; loading model"
                    )
                db.patch(
                    "creative_instances",
                    record["id"],
                    {**progress, "owned_processes": tracked, "loading_workers": ranks},
                )
                job.update(
                    progress["phase"],
                    detail=progress["detail"],
                    phase_progress=progress["phase_progress"],
                )
                try:
                    result = check(record)
                    job.check_cancelled()
                    job.update("ready", 100)
                    return result
                except (httpx.HTTPError, ValueError):
                    time.sleep(1)
            raise ValueError(
                engine.engine_failure_message(logfile).replace(
                    "Engine startup failed", "H3 startup failed"
                )
            )
        except BaseException as error:
            stop_owned(record["id"])
            db.patch(
                "creative_instances",
                record["id"],
                {
                    "state": "stopped" if isinstance(error, Cancelled) else "failed",
                    "phase": "stopped" if isinstance(error, Cancelled) else "failed",
                    "error": None if isinstance(error, Cancelled) else str(error),
                },
            )
            raise
