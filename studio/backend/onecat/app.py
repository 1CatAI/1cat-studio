# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import platform
import re
import shutil
import subprocess
import tarfile
import threading
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from . import __version__, auth, db, engine, gpu, models, proxy, runtimes, telemetry, updates
from .config import automatic_gpu_actions, frontend_dist, initialize_paths, state_root
from .jobs import TERMINAL, create_job, list_jobs, request_cancel
from .schemas import (
    BenchmarkRequest,
    HardwareSetting,
    Profile,
    RuntimeImport,
    SetupRequest,
    StudioSettings,
)

_download_lock = threading.Lock()
_runtime_install_lock = threading.Lock()


def public_runtime(record: dict) -> dict:
    return {k: v for k, v in record.items() if k != "environment"}


def scheduled_job(kind: str, payload: dict) -> dict:
    if updates.activating():
        raise ValueError("Studio 正在更新 / Studio is updating")
    from .model_switch import KINDS, schedule

    if kind in KINDS:
        return schedule(kind, payload)
    list_jobs()
    for existing in db.all_records("jobs"):
        same_payload = existing.get("payload") == payload
        if kind == "install_runtime" and payload.get("release_id"):
            # Release notes can change during installation; the immutable asset
            # identity is what determines whether this is already being installed.
            same_payload = existing.get("payload", {}).get("release_id") == payload["release_id"]
        if (
            existing["state"] not in TERMINAL
            and existing["kind"] == kind
            and same_payload
        ):
            return {k: v for k, v in existing.items() if k != "payload"}
    result = create_job(kind, payload)
    return {k: v for k, v in result.items() if k != "payload"}


async def lifecycle_monitor():
    await asyncio.sleep(2)
    while updates.activating():
        await asyncio.sleep(2)
    await asyncio.to_thread(runtimes.refresh_incomplete_metadata)
    from .catalog import backfill_default_profiles

    await asyncio.to_thread(backfill_default_profiles)
    from .adopt import reconcile

    if automatic_gpu_actions():
        await asyncio.to_thread(gpu.recover_interrupted)

        await asyncio.to_thread(reconcile)
        from .gpu_control import reconcile_saved

        await asyncio.to_thread(reconcile_saved)
        automatic = db.settings().get("autostart_profile")
        if (
            automatic
            and engine.status().get("state") in ("stopped", "failed")
            and not db.get("engine", "hardware_recovery")
        ):
            scheduled_job("start_model", {"profile_id": automatic})
    while True:
        await asyncio.sleep(2)
        if updates.activating():
            continue
        from .lifecycle import reconcile_owned

        if automatic_gpu_actions():
            await asyncio.to_thread(reconcile_owned)
        jobs = list_jobs()
        maintenance = db.get("engine", "maintenance")
        if maintenance:
            job = next((j for j in jobs if j["id"] == maintenance["job_id"]), None)
            if not job or job["state"] in TERMINAL:
                db.delete("engine", "maintenance")
        state = engine.status()
        if automatic_gpu_actions() and state.get("adopted_service") and state.get("state") == "stopped":
            await asyncio.to_thread(reconcile)
        idle = db.settings().get("idle_unload_minutes", 0)
        if automatic_gpu_actions() and idle and state.get("state") == "ready" and not state.get("maintenance"):
            if (
                not engine.active_requests()
                and time.time() - state.get("last_request_at", time.time()) > idle * 60
            ):
                scheduled_job("stop_model", {})


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_paths()
    from .migrations import upgrade

    upgrade()
    from . import chat_runs

    chat_runs.recover()
    from .agent import tasks as agent_tasks

    agent_tasks.recover()
    # An interrupted proxy cannot still own a request after manager restart.
    with db.connect() as conn:
        conn.execute(
            "UPDATE requests SET status=499,error='Manager restarted' WHERE status IS NULL"
        )
        conn.execute(
            "UPDATE requests SET metrics=json_set(metrics, '$.pending', json('false')) WHERE metrics IS NOT NULL"
        )
    monitor = asyncio.create_task(lifecycle_monitor())
    sampler = asyncio.create_task(telemetry.monitor())
    from .creative import services as creative_services

    creative_monitor = asyncio.create_task(creative_services.monitor())
    try:
        yield
    finally:
        monitor.cancel()
        sampler.cancel()
        creative_monitor.cancel()
        await chat_runs.shutdown()
        await agent_tasks.shutdown()
        tasks = list(proxy.pending)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(asyncio.CancelledError):
            await monitor
        with contextlib.suppress(asyncio.CancelledError):
            await sampler
        with contextlib.suppress(asyncio.CancelledError):
            await creative_monitor


def create_app() -> FastAPI:
    app = FastAPI(
        title="1Cat Studio", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None
    )
    from .agent.api import router as agent_router

    app.include_router(agent_router)
    from .creative.api import router as creative_router

    app.include_router(creative_router)
    management_write = asyncio.Lock()

    @app.middleware("http")
    async def serialize_management(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            with (updates.updates_root() / "admission.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    if updates.activating():
                        raise BlockingIOError
                except BlockingIOError:
                    return JSONResponse(
                        {"detail": "Studio 正在更新，请稍后重试 / Studio is updating"},
                        status_code=503, headers={"Retry-After": "5"},
                    )
                return await management(request, call_next)
        return await call_next(request)

    async def management(request, call_next):
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.url.path.startswith("/api/")
            and request.url.path not in {"/api/inference/generate/stream", "/api/creative/assets"}
            # A repository import owns a fresh project UUID. It must not hold
            # the management lock while downloading and block task cancellation.
            and not (request.method == "POST" and request.url.path == "/api/agent/projects")
        ):
            async with management_write:
                return await call_next(request)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def bad_value(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        return JSONResponse(
            {
                "detail": [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in error.errors()
                ]
            },
            status_code=422,
        )

    @app.get("/api/health")
    def health():
        return {"status": "ok", "product": "1Cat Studio", "version": __version__,
                "release": updates.installed_version()}

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        authenticated = False
        with contextlib.suppress(HTTPException):
            authenticated = auth.require_admin(request)["scope"] == "admin"
        return {"initialized": db.get("auth", "admin") is not None, "authenticated": authenticated}

    @app.post("/api/auth/setup")
    def setup(payload: SetupRequest, request: Request, response: Response):
        if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(
                403, "Create the first administrator through localhost or an SSH port forward"
            )
        if request.url.hostname not in {"127.0.0.1", "::1", "localhost", "testserver"}:
            raise HTTPException(403, "Initialize Studio through its localhost address")
        if request.headers.get("origin") and request.headers["origin"].rstrip("/") != str(
            request.base_url
        ).rstrip("/"):
            raise HTTPException(403, "Cross-origin setup rejected")
        auth.initialize(payload.password)
        token = auth.login(payload.password, request.client.host if request.client else "local")
        response.set_cookie(
            auth.COOKIE,
            token,
            httponly=True,
            samesite="strict",
            max_age=30 * 86400,
            secure=request.url.scheme == "https",
        )
        return {"ok": True}

    @app.post("/api/auth/login")
    def login(payload: dict, request: Request, response: Response):
        if request.headers.get("origin") and request.headers["origin"].rstrip("/") != str(
            request.base_url
        ).rstrip("/"):
            raise HTTPException(403, "Cross-origin login rejected")
        token = auth.login(
            str(payload.get("password", "")), request.client.host if request.client else "local"
        )
        response.set_cookie(
            auth.COOKIE,
            token,
            httponly=True,
            samesite="strict",
            max_age=30 * 86400,
            secure=request.url.scheme == "https",
        )
        return {"ok": True}

    @app.post("/api/auth/logout")
    def logout(response: Response, credential=Depends(auth.require_admin)):
        with db.connect() as conn:
            conn.execute("DELETE FROM credentials WHERE digest=?", (credential["digest"],))
        response.delete_cookie(auth.COOKIE)
        return {"ok": True}

    admin = APIRouter(prefix="/api", dependencies=[Depends(auth.require_admin)])

    @admin.get("/studio/capabilities")
    def capabilities():
        return {
            "product": "1Cat Studio",
            "version": __version__,
            "platform": platform.system(),
            "features": {
                "chat": True,
                "models": True,
                "downloads": True,
                "efficiency": True,
                "api": True,
                "training": False,
                "rag": False,
                "mcp": False,
                "images": True,
                "audio": False,
                "video": False,
            },
            "single_active_model": True,
            "gpu_control": gpu.control_available(),
            "automatic_gpu_actions": automatic_gpu_actions(),
            "upstream_commit": "afeb2778f4a7e43dc3a0bdf2d2323b8dba6fd13d",
        }

    @admin.get("/system")
    def system():
        root = state_root()
        disk = shutil.disk_usage(root)
        return {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "disk_free_bytes": disk.free,
            "disk_total_bytes": disk.total,
            "state_directory": str(root),
            "gpu": gpu.snapshot(),
            "uv_available": shutil.which("uv") is not None,
        }

    @admin.get("/gpu")
    def devices():
        return gpu.snapshot()

    @admin.get("/gpu/live")
    def live_devices(since: float = 0):
        state = engine.private_state()
        uuids = (
            (state.get("profile") or {}).get("gpu_uuids", [])
            if state.get("state") in {"loading", "ready", "stopping", "unavailable"}
            else []
        )
        return telemetry.live(uuids, since)

    @admin.get("/inference/power-modes")
    def get_power_modes():
        from .gpu_control import status

        return status((engine.status().get("profile") or {}).get("gpu_uuids", []))

    @admin.get("/gpu/power-modes")
    def gpu_power_modes(gpu_uuids: Annotated[list[str] | None, Query()] = None):
        from .gpu_control import status

        return status(gpu_uuids)

    @admin.post("/gpu/power-mode")
    def gpu_power_mode(payload: dict):
        from .gpu_control import queue

        if payload.keys() - {"id", "gpu_uuids"} or not isinstance(payload.get("id"), str):
            raise ValueError("Provide a power mode ID and GPU UUIDs")
        return queue(payload.get("gpu_uuids"), mode=payload["id"])

    @admin.post("/gpu/settings")
    def gpu_settings(payload: dict):
        from .gpu_control import queue

        if payload.keys() - {"setting", "gpu_uuids"}:
            raise ValueError("Unknown GPU setting field")
        return queue(payload.get("gpu_uuids"), setting=payload.get("setting"))

    @admin.post("/inference/power-mode")
    def set_power_mode(payload: dict):
        from .gpu_control import queue

        state = engine.status()
        return queue(payload.get("gpu_uuids", (state.get("profile") or {}).get("gpu_uuids", [])), mode=str(payload.get("id", "")))

    @admin.post("/gpu/activate")
    def activate_machine_gpus():
        # Re-bind presets that were delivered bound to another machine's cards.
        from .activation import rebind_profiles

        return rebind_profiles()

    @admin.get("/gpu/control")
    def gpu_control_status():
        from .gpu_setup import status

        return status()

    @admin.post("/gpu/control/setup")
    def setup_gpu_control(payload: dict | None = None):
        from .gpu_setup import binding_request, status

        current = status()
        if current["available"]:
            return {"available": True}
        if not current["bundled"] or not current["gpu_count"]:
            raise ValueError("GPU 控制组件或 NVIDIA GPU 不可用 / GPU component or NVIDIA GPUs unavailable")
        return scheduled_job("configure_gpu_helper", binding_request(payload or {}))

    @admin.get("/settings")
    def get_settings():
        return {
            **db.settings(),
            "modelscope_token_set": bool(db.get("secrets", "modelscope", {}).get("token")),
        }

    @admin.put("/settings")
    def set_settings(payload: dict):
        allowed = {
            "model_directory",
            "modelscope_endpoint",
            "idle_unload_minutes",
            "autostart_profile",
            "host",
            "port",
            "locale",
            "theme",
        }
        if payload.keys() - allowed:
            raise ValueError("Unknown settings field")
        before = db.settings()
        values = StudioSettings.model_validate({**before, **payload}).model_dump()
        if values["autostart_profile"] and not db.get("profiles", values["autostart_profile"]):
            raise ValueError("Autostart profile does not exist")
        directory = Path(values["model_directory"]).expanduser().resolve()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ValueError("Model directory is not available: " + str(error)) from error
        values["model_directory"] = str(directory)
        db.put("settings", "main", values)
        return {**values, "restart_required": any(values[k] != before[k] for k in ("host", "port"))}

    @admin.put("/settings/modelscope-token")
    def modelscope_token(payload: dict):
        db.put("secrets", "modelscope", {"token": str(payload.get("token", "")) or None})
        return {"configured": bool(payload.get("token"))}

    @admin.get("/updates/progress")
    def update_progress():
        return {"current": updates.installed_version(), "status": updates.read_status(),
                "blocked_reason": updates.busy_reason()}

    @admin.get("/updates")
    def update_status(force: bool = False):
        from .updates import check

        return check(force=force)

    @admin.post("/updates/install")
    def update_install(payload: dict | None = None):
        from . import updates

        try:
            manifest = updates.fetch(force=True)
        except Exception as error:  # noqa: BLE001 - the channel is operator input
            raise HTTPException(409, "更新通道不可用 / Update channel failed: " + str(error)[:200])
        payload = payload or {}
        if payload.get("release_id") != updates.protocol.release_id(manifest):
            raise HTTPException(409, "版本信息已变化，请重新检查更新 / Check the release again")
        port = int(db.settings().get("port") or 8888)
        try:
            started = updates.start(manifest, port=port,
                                    switch_to_release=payload.get("switch_to_release") is True)
        except ValueError as error:
            raise HTTPException(409, str(error))
        return {"started": started, "check": updates.check()}

    @admin.get("/runtimes")
    def runtime_list():
        cached = db.get("runtime_release_catalog", "github", {})
        return {
            "items": [public_runtime(r) for r in db.all_records("runtimes")],
            "releases": cached.get("items", runtimes.RELEASES),
        }

    @admin.post("/runtimes/import")
    def runtime_import(payload: RuntimeImport):
        return scheduled_job("import_runtime", payload.model_dump())

    @admin.get("/runtimes/releases")
    def runtime_releases(refresh: bool = False):
        from . import runtime_releases as releases

        catalog = releases.catalog(force=refresh)
        devices = gpu.snapshot()["gpus"]
        for item in catalog["items"]:
            problems = releases.availability(item, devices)
            installed = releases.installed_runtime(item)
            item.update(available=not problems, reasons=problems,
                        installed_runtime_id=installed["id"] if installed else None)
        return catalog

    @admin.post("/runtimes/install")
    def runtime_install(payload: dict | None = None):
        from . import runtime_releases as releases

        release_id = (payload or {}).get("release_id", runtimes.RELEASES[0]["id"])
        release = releases.resolve(release_id, gpu.snapshot()["gpus"])
        # Capture only the server's recipe, including exact companion assets and hashes.
        with _runtime_install_lock:
            return scheduled_job("install_runtime", {"release_id": release["id"], "release": release})

    @admin.post("/runtimes/offline")
    def runtime_offline(payload: dict):
        return scheduled_job("import_offline", {"path": str(payload.get("path", ""))})

    @admin.post("/runtimes/{id}/export")
    def runtime_export(id: str):
        if not db.get("runtimes", id):
            raise HTTPException(404, "Runtime not found")
        return scheduled_job("export_runtime", {"runtime_id": id})

    @admin.get("/models/list")
    def model_list():
        from .catalog import capabilities, model_entry

        items = db.all_records("models")
        installed = db.all_records("runtimes")
        for item in items:
            if item.get("catalog_id"):
                item["verified"] = bool(model_entry(item["path"]))
            item["local_verified"] = not item.get("catalog_id") and any(
                capabilities(item["path"], r["id"]).get("verified") for r in installed
            )
        return {"items": items}

    @admin.get("/models/supported")
    def supported_models():
        from .catalog import supported

        return supported()

    @admin.get("/models/search")
    def model_search(q: str = "", runtime_id: str | None = None, all_verified: bool = False):
        try:
            from .catalog import directory

            return {
                "items": models.search(q, runtime_id, all_verified),
                "audit": directory()["audit"],
                "version": directory()["version"],
            }
        except Exception as error:
            raise HTTPException(
                502, "Model registry could not be reached: " + str(error)[:300]
            ) from error

    @admin.get("/models/card")
    def model_card(catalog_id: str, refresh: bool = False):
        from .model_cards import read

        return read(catalog_id, refresh)

    @admin.post("/models/import")
    def model_import(payload: dict):
        return models.inspect_model(str(payload.get("path", "")))

    @admin.post("/hub/download")
    def download(payload: dict):
        repo = str(payload.get("repo_id", ""))
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", repo):
            raise ValueError("Enter an owner/model repository ID")
        from .catalog import require_download

        item = require_download(repo, payload.get("revision"), payload.get("runtime_id"))
        with _download_lock:
            # Runtime selection does not identify a weight download. Legacy jobs
            # may still carry runtime_id, so compare only checkpoint + revision.
            for job in db.all_records("jobs"):
                if (
                    job["kind"] == "download_model"
                    and job["state"] not in TERMINAL
                    and job.get("payload", {}).get("repo_id") == repo
                    and job["payload"].get("revision", "master") == item["revision"]
                ):
                    return {k: v for k, v in job.items() if k != "payload"}
            return scheduled_job("download_model", {"repo_id": repo, "revision": item["revision"]})

    @admin.get("/models/launch-defaults")
    def model_launch_defaults(catalog_id: str, model_id: str | None = None):
        from .catalog import launch_defaults

        return launch_defaults(catalog_id, model_id)

    @admin.post("/models/{id}/default-profile")
    def model_default_profile(id: str):
        from .catalog import create_default_profile

        return create_default_profile(id)

    @admin.delete("/models/{id}")
    def model_remove(id: str, remove_files: bool = False):
        models.remove(id, remove_files)
        return {"ok": True}

    @admin.put("/models/{id}/default-profile")
    def set_model_default_profile(id: str, payload: dict):
        from .model_controls import choices

        model = next((item for item in choices()["items"] if item["id"] == id), None)
        if not model:
            raise HTTPException(404, "Model not found")
        profile_id = payload.get("profile_id")
        if not any(p["id"] == profile_id for p in model["profiles"]):
            raise HTTPException(409, "Profile does not match this model")
        db.patch("models", id, {"default_profile_id": profile_id})
        return {"ok": True}

    @admin.get("/profiles")
    def profiles():
        return {"items": db.all_records("profiles")}

    @admin.post("/profiles")
    def save_profile(payload: Profile):
        data = payload.model_dump()
        data["id"] = data["id"] or db.uid()
        if not db.get("runtimes", data["runtime_id"]):
            raise ValueError("Runtime not found")
        from .catalog import validate_features

        validate_features(data)
        db.put("profiles", data["id"], data)
        return data

    @admin.post("/profiles/capabilities")
    def profile_capabilities(payload: dict):
        from .catalog import capabilities

        return capabilities(str(payload.get("model_path", "")), str(payload.get("runtime_id", "")))

    @admin.get("/profiles/{id}/command")
    def profile_command(id: str):
        data = db.get("profiles", id)
        if not data:
            raise HTTPException(404, "Profile not found")
        return {"command": engine.command_preview(data)}

    @admin.delete("/profiles/{id}")
    def delete_profile(id: str):
        if not db.get("profiles", id):
            raise HTTPException(404, "Profile not found")
        if engine.status().get("profile_id") == id and engine.status().get("pid"):
            raise ValueError("Stop the active model before deleting its profile")
        list_jobs()
        if any(j["state"] not in TERMINAL and j.get("payload", {}).get("profile_id") == id
               for j in db.all_records("jobs")):
            raise HTTPException(409, "Finish or cancel tasks using this profile before deleting it")
        db.delete("profiles", id)
        settings = db.settings()
        if settings.get("autostart_profile") == id:
            db.put("settings", "main", {**settings, "autostart_profile": None})
        return {"ok": True}

    @admin.get("/inference/status")
    def inference_status():
        return engine.status()

    @admin.post("/inference/load")
    def load(payload: dict):
        if not db.get("profiles", str(payload.get("profile_id", ""))):
            raise HTTPException(404, "Profile not found")
        return scheduled_job("start_model", {"profile_id": payload["profile_id"]})

    @admin.get("/inference/options")
    def inference_options():
        from .model_controls import thinking_support
        return {"thinking": thinking_support(engine.private_state().get("profile") or {})}

    @admin.get("/inference/models")
    def inference_models(agent: bool = False):
        from .model_controls import choices

        return choices(agent)

    @admin.post("/inference/load-model")
    def load_model(payload: dict):
        from .model_controls import resolve_profile
        from .agent.tasks import ACTIVE

        # An Agent can be between model requests while its tools still run.
        if any(t.get("state") in ACTIVE or t.get("settled") is False for t in db.all_records("agent_tasks")):
            raise HTTPException(409, "Agent 正在执行，请先完成或停止任务 / Finish or stop the Agent task before switching models")
        profile = resolve_profile(str(payload.get("model_id", "")), payload.get("profile_id"), payload.get("agent") is True)
        return load({"profile_id": profile["id"]})

    @admin.post("/inference/unload")
    def unload(payload: dict | None = None):
        return scheduled_job("stop_model", {"force": bool((payload or {}).get("force", False))})

    @admin.get("/inference/load-progress")
    def load_progress():
        return {"items": [j for j in list_jobs() if j["kind"] in {"start_model", "stop_model"}]}

    @admin.post("/inference/generate/stream")
    async def chat(request: Request):
        return await proxy.forward(request, "/v1/chat/completions", "studio")

    @admin.post("/inference/hardware")
    def hardware(payload: HardwareSetting):
        from .gpu_control import queue

        uuids = (engine.status().get("profile") or {}).get("gpu_uuids", [])
        return queue(uuids, setting=payload.model_dump())

    @admin.get("/jobs")
    def jobs():
        return {"items": list_jobs()}

    @admin.post("/jobs/{id}/cancel")
    def cancel_job(id: str):
        record = db.get("jobs", id)
        if not record:
            raise HTTPException(404, "Task not found")
        state = engine.status()
        if state.get("job_id") == id and state.get("state") == "loading":
            from .jobs import same_process

            if not same_process(record.get("pid"), record.get("process_created")):
                db.patch("jobs", id, {"cancel_requested": True, "state": "cancelled"})
                return scheduled_job("stop_model", {"force": True})
        if record["state"] not in TERMINAL:
            request_cancel(id)
        return {"ok": True}

    @admin.post("/jobs/{id}/retry")
    def retry_job(id: str):
        record = db.get("jobs", id)
        if not record or record["state"] not in {"failed", "cancelled"}:
            raise ValueError("Only failed or cancelled tasks can be retried")
        if record["kind"] == "apply_gpu_settings":
            from .gpu_control import queue

            return queue(record["payload"]["uuids"], settings=record["payload"]["settings"], mode=record["payload"].get("mode"), recover=record["payload"].get("recover", False), recover_only=not record["payload"]["settings"])
        if record["kind"] == "install_runtime":
            with _runtime_install_lock:
                return scheduled_job(record["kind"], record["payload"])
        return scheduled_job(record["kind"], record["payload"])

    @admin.get("/logs/{name}")
    def logs(name: str):
        if name != "engine" and not re.fullmatch(r"job-[a-f0-9]{32}", name):
            raise HTTPException(404, "Log not found")
        text = engine.read_log_tail(state_root() / "logs" / f"{name}.log", 50000)
        state = engine.private_state()
        if name == "engine" and state.get("adopted_service"):
            text = subprocess.run(
                ["journalctl", "--user", "-u", state["systemd_unit"], "-n", "200", "--no-pager"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        key = engine.private_state().get("api_key")
        text = engine.redact_log(text, key)
        return {"text": text}

    @admin.get("/jobs/{id}/download")
    def task_download(id: str):
        record = db.get("jobs", id, {})
        value = record.get("result", {}).get("download_path")
        if not value or record.get("state") != "completed":
            raise HTTPException(404, "Export not ready")
        path = Path(value).resolve()
        if not path.is_relative_to(state_root() / "backups") or not path.is_file():
            raise HTTPException(404, "Export not found")
        return FileResponse(path, filename=path.name)

    @admin.get("/keys")
    def keys():
        with db.connect() as conn:
            return {
                "items": [
                    dict(r)
                    for r in conn.execute(
                        "SELECT digest AS id,name,scope,created,prefix FROM credentials WHERE scope='inference'"
                    )
                ]
            }

    @admin.post("/keys")
    def new_key(payload: dict):
        name = str(payload.get("name", "API key"))[:100]
        return {"key": auth.issue(name), "name": name}

    @admin.delete("/keys/{id}")
    def revoke_key(id: str):
        with db.connect() as conn:
            conn.execute("DELETE FROM credentials WHERE digest=? AND scope='inference'", (id,))
        return {"ok": True}

    @admin.get("/requests")
    def requests_list(days: int | None = None, model: str = "", source: str = ""):
        if days is not None and days not in {1, 3, 7}:
            raise ValueError("Choose 1, 3 or 7 days")
        if source not in {"", "studio", "api"}:
            raise ValueError("Unknown request source")
        with db.connect() as conn:
            rows = [
                {**dict(r), "metrics": json.loads(r["metrics"] or "{}")}
                for r in conn.execute(
                    """SELECT * FROM requests WHERE (? IS NULL OR started>=?)
                    AND (?='' OR model=?) AND (?='' OR source=?) ORDER BY started DESC LIMIT 200""",
                    (days, time.time() - (days or 0) * 86400, model, model, source, source),
                )
            ]
            in_flight = conn.execute(
                "SELECT count(*) FROM requests WHERE status IS NULL AND (?='' OR model=?) AND (?='' OR source=?)",
                (model, model, source, source),
            ).fetchone()[0]
        return {
            "items": rows,
            "active": in_flight,
            "scope": {"days": days, "model": model, "source": source},
        }

    @admin.get("/requests/usage")
    def request_usage(days: int = 1, model: str = "", source: str = ""):
        from .token_usage import summary

        return summary(days, model=model, source=source)

    @admin.get("/requests/{id}")
    def request_details(id: str):
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM requests WHERE id=?", (id,)).fetchone()
        if not row:
            raise HTTPException(404, "Request not found")
        return {**dict(row), "metrics": json.loads(row["metrics"] or "{}")}

    @admin.get("/efficiency")
    def efficiency_records():
        from .efficiency import list_results

        return list_results()

    @admin.post("/efficiency/run")
    def efficiency_run(payload: BenchmarkRequest):
        if not db.get("profiles", payload.profile_id):
            raise ValueError("Profile not found")
        return scheduled_job("benchmark", payload.model_dump())

    @admin.post("/efficiency/recommend")
    def efficiency_recommend(payload: dict):
        from .efficiency import recommend

        return recommend(payload)

    @admin.post("/chat/attachments")
    async def upload_attachment(file: UploadFile):
        from .attachments import MAX_BYTES, store

        data = await file.read(MAX_BYTES + 1)
        await file.close()
        return await asyncio.to_thread(store, data, file.filename or "image")

    @admin.get("/chat/attachments/{id}")
    def get_attachment(id: str):
        from .attachments import read

        record, path = read(id)
        return FileResponse(
            path,
            media_type=record["mime"],
            headers={
                "Cache-Control": "private, max-age=86400",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @admin.get("/chat/threads")
    def chat_threads(q: str = ""):
        from onecat import chat_store as studio_db

        items = studio_db.list_chat_threads(model_type="text")
        if q:
            query = q.casefold()
            items = [
                t
                for t in items
                if query in t["title"].casefold()
                or query
                in json.dumps(studio_db.list_chat_messages(t["id"]), ensure_ascii=False).casefold()
            ]
        from . import chat_runs

        running = {r["thread_id"]: r for r in chat_runs.active()}
        pinned = {r["id"] for r in db.all_records("chat_pins")}
        items = [
            {**item, "pinned": item["id"] in pinned, "generation": running.get(item["id"])}
            for item in items
        ]
        items.sort(key=lambda item: (item["pinned"], item.get("updatedAt") or 0), reverse=True)
        return {"items": items}

    @admin.get("/chat/runs")
    def active_chat_runs():
        from .chat_runs import active

        return {"items": active()}

    @admin.post("/chat/threads/{id}/generate")
    async def start_chat_run(id: str, payload: dict):
        from .chat_runs import start

        return start(id, payload)

    @admin.get("/chat/threads/{id}/events")
    async def chat_run_events(id: str):
        from .chat_runs import subscribe

        return subscribe(id)

    @admin.get("/chat/threads/{id}/generation")
    def chat_run_status(id: str):
        from .chat_runs import status

        return JSONResponse(status(id), headers={"Cache-Control": "no-store"})

    @admin.post("/chat/threads/{id}/cancel")
    async def cancel_chat_run(id: str):
        from .chat_runs import cancel

        return await cancel(id)

    @admin.post("/chat/threads")
    def create_thread(payload: dict):
        from onecat import chat_store as studio_db

        from .chat_settings import normalize

        now = int(time.time() * 1000)
        return studio_db.upsert_chat_thread(
            {
                "id": db.uid(),
                "title": str(payload.get("title", "新对话"))[:200],
                "modelType": "text",
                "modelId": payload.get("modelId", ""),
                "createdAt": now,
                "updatedAt": now,
                "settings": normalize(payload.get("settings")),
            }
        )

    @admin.get("/chat/threads/{id}")
    def get_thread(id: str):
        from onecat import chat_store as studio_db

        thread = studio_db.get_chat_thread(id)
        if not thread:
            raise HTTPException(404, "Conversation not found")
        from .chat_metrics import hydrate
        from .chat_settings import normalize

        thread["settings"] = normalize(thread.get("settings"))

        return {
            "thread": thread,
            "messages": [hydrate(m) for m in studio_db.list_chat_messages(id)],
            "generation": db.get("chat_runs", id),
        }

    @admin.get("/chat/threads/{id}/export")
    def export_thread(id: str):
        from .attachments import export_images

        data = get_thread(id)
        return {
            "title": data["thread"]["title"],
            "settings": data["thread"].get("settings"),
            "messages": data["messages"],
            "attachments": export_images(data["messages"]),
            "format": "onecat-chat-v2",
        }

    @admin.patch("/chat/threads/{id}")
    def update_thread(id: str, payload: dict):
        from onecat import chat_store as studio_db

        if not studio_db.get_chat_thread(id):
            raise HTTPException(404, "Conversation not found")
        allowed = {"title", "archived", "settings", "pinned"}
        if payload.keys() - allowed:
            raise ValueError("Unknown conversation field")
        if "settings" in payload:
            from .chat_settings import normalize

            payload["settings"] = normalize(payload["settings"])
        if "pinned" in payload:
            if not isinstance(payload["pinned"], bool):
                raise ValueError("pinned must be a boolean")
            if payload.pop("pinned"):
                db.put("chat_pins", id, {"id": id})
            else:
                db.delete("chat_pins", id)
        return studio_db.update_chat_thread(id, payload)

    @admin.put("/chat/threads/{id}/messages")
    def save_messages(id: str, payload: dict):
        from onecat import chat_store as studio_db

        from .chat_runs import require_idle

        require_idle(id)

        if not studio_db.get_chat_thread(id):
            raise HTTPException(404, "Conversation not found")
        messages = payload.get("messages", [])
        if not isinstance(messages, list) or len(messages) > 10000:
            raise ValueError("Invalid message list")
        seen = set()
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in {
                "user",
                "assistant",
                "system",
            }:
                raise ValueError("Unsupported message role")
            if (
                not isinstance(message.get("id"), str)
                or not message["id"]
                or not isinstance(message.get("content"), list)
            ):
                raise ValueError("Each message requires an ID and a content array")
            if message["id"] in seen:
                raise ValueError("Message IDs must be unique")
            seen.add(message["id"])
            from .attachments import validate_content

            validate_content(message["content"])
            message["threadId"] = id
            from .chat_metrics import hydrate

            hydrate(message)
        saved = studio_db.sync_chat_messages(
            id, messages, prune_missing=bool(payload.get("replace", False))
        )
        studio_db.update_chat_thread(id, {"updatedAt": int(time.time() * 1000)})
        return {"messages": saved}

    @admin.delete("/chat/threads/{id}")
    def delete_thread(id: str):
        from onecat import chat_store as studio_db

        from .chat_runs import require_idle

        require_idle(id)

        studio_db.delete_chat_threads([id])
        db.delete("chat_runs", id)
        db.delete("chat_pins", id)
        return {"ok": True}

    @admin.post("/chat/import")
    def import_chat(payload: dict):
        from onecat import chat_store as studio_db

        messages = payload.get("messages", [])
        if not isinstance(messages, list) or not messages or len(messages) > 10000:
            raise ValueError("Import requires 1–10000 messages")
        from .attachments import discard_images, import_images, validate_content
        from .chat_settings import normalize

        settings = normalize(payload.get("settings"))
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in {"user", "assistant", "system"}:
                raise ValueError("Unsupported message role")
            content = message.get("content", "")
            message["content"] = [{"type": "text", "text": content}] if isinstance(content, str) else content
            validate_content(message["content"], check_images=False)
            if message.get("metadata") is not None and not isinstance(message["metadata"], dict):
                raise ValueError("Message metadata must be an object")
        imported = import_images(messages, payload.get("attachments", {}))
        thread = None
        now = int(time.time() * 1000)
        records = []
        parent = None
        for index, message in enumerate(messages):
            id = db.uid()
            content = message.get("content", "")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            records.append(
                {
                    "id": id,
                    "parentId": parent,
                    "role": message["role"],
                    "content": content,
                    "createdAt": now + index,
                    "metadata": message.get("metadata"),
                }
            )
            parent = id
        try:
            thread = create_thread({"title": payload.get("title", "导入对话"), "settings": settings})
            for record in records:
                record["threadId"] = thread["id"]
            studio_db.sync_chat_messages(thread["id"], records)
        except Exception:
            if thread:
                studio_db.delete_chat_threads([thread["id"]])
            discard_images(imported)
            raise
        return thread

    @admin.post("/backup")
    def backup():
        name = f"onecat-backup-{int(time.time())}-{db.uid()[:12]}"
        folder = state_root() / "backups" / name
        folder.mkdir()
        for filename in ("onecat.db", "studio.db"):
            db.backup_database(state_root() / filename, folder / filename)
        if (state_root() / "attachments").is_dir():
            shutil.copytree(state_root() / "attachments", folder / "attachments")
        (folder / "manifest.json").write_text(
            json.dumps({"format": "onecat-backup-v1", "version": __version__})
        )
        target = folder.with_suffix(".tar.gz")
        with tarfile.open(target, "w:gz") as archive:
            archive.add(folder, arcname=".")
        shutil.rmtree(folder)
        return {"filename": target.name}

    @admin.get("/backup/{name}")
    def backup_download(name: str):
        if not re.fullmatch(r"onecat-backup-[0-9]+(?:-[a-f0-9]{12})?\.tar\.gz", name):
            raise HTTPException(404)
        path = state_root() / "backups" / name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, filename=name)

    @admin.post("/inference/adopt")
    def adopt(payload: dict):
        unit = str(payload.get("unit", ""))
        if not re.fullmatch(r"(?:1cat-vllm|onecat-studio)-[a-zA-Z0-9_.@-]+\.service", unit):
            raise ValueError("Enter a 1Cat systemd user service name")
        return scheduled_job("adopt_service", {"unit": unit})

    @admin.post("/auth/password")
    def change_password(payload: dict):
        from .passwords import hash_password, verify_password

        admin_record = db.get("auth", "admin")
        if not admin_record or not verify_password(
            str(payload.get("current_password", "")), admin_record["salt"], admin_record["hash"]
        ):
            raise HTTPException(401, "Current password is incorrect")
        new_password = str(payload.get("new_password", ""))
        if not 8 <= len(new_password) <= 256:
            raise ValueError("New password must contain 8–256 characters")
        salt, hashed = hash_password(new_password)
        db.put("auth", "admin", {"salt": salt, "hash": hashed})
        with db.connect() as conn:
            conn.execute("DELETE FROM credentials WHERE scope='admin'")
        return {"ok": True, "login_required": True}

    @admin.post("/backup/restore")
    def restore_backup(payload: dict):
        from .maintenance import restore_backup as restore

        return restore(str(payload.get("path", "")))

    app.include_router(admin)

    @app.get("/v1/models", dependencies=[Depends(auth.require_inference)])
    async def api_models():
        state = engine.status()
        data = []
        if state.get("state") == "ready":
            data = [
                {"id": state["profile"]["served_model_name"], "object": "model", "owned_by": "1cat"}
            ]
        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions", dependencies=[Depends(auth.require_inference)])
    async def api_chat(request: Request):
        return await proxy.forward(request, "/v1/chat/completions")

    @app.post("/v1/completions", dependencies=[Depends(auth.require_inference)])
    async def api_completion(request: Request):
        return await proxy.forward(request, "/v1/completions")

    @app.get("/{path:path}")
    def frontend(path: str, request: Request):
        if path.startswith(("api/", "v1/")):
            raise HTTPException(404, "Unsupported endpoint")
        dist = frontend_dist().resolve()
        # The embedding document must restrict frame navigation too; a child
        # CSP alone does not restrict location.href to an external origin.
        document_headers = {
            "Cache-Control": "no-store",
            "Content-Security-Policy": "frame-src 'self'; object-src 'none'; base-uri 'self'",
            "X-DNS-Prefetch-Control": "off",
            "Referrer-Policy": "no-referrer",
        }
        requested = (dist / path).resolve()
        # Check the resolved file too, so encoded dot/parent paths cannot bypass
        # authentication for the private corresponding-source archive.
        if requested == (dist / "source.tar.gz").resolve():
            auth.require_admin(request)
            if not requested.is_relative_to(dist) or not requested.is_file():
                raise HTTPException(404, "Source archive is not available in this installation")
            return FileResponse(
                requested,
                filename="source.tar.gz",
                headers={
                    "Cache-Control": "private, no-store",
                    "Vary": "Cookie, Authorization",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        if requested.is_relative_to(dist) and requested.is_file():
            return FileResponse(
                requested, headers=document_headers if requested.suffix == ".html" else None
            )
        if path.startswith(("assets/", "preview/")) or requested.suffix in {".js", ".css", ".wasm"}:
            from .frontend_assets import retained_asset

            previous = retained_asset(dist, path)
            if previous:
                return FileResponse(previous, headers={
                    "Cache-Control": "public, max-age=31536000, immutable",
                    "X-Content-Type-Options": "nosniff",
                })
            raise HTTPException(404, "Frontend asset is unavailable", headers={"Cache-Control": "no-store"})
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html", headers=document_headers)
        return JSONResponse(
            {"detail": "Frontend has not been built. Run the frontend build first."},
            status_code=503,
        )

    return app
