# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field

from .. import auth, db, engine
from ..jobs import TERMINAL, create_job, list_jobs, request_cancel
from . import assets, components, generations, presets, runs, services, store
from .schema import WORKFLOWS, Document, Service, Strict

router = APIRouter(prefix="/api/creative", dependencies=[Depends(auth.require_admin)])


class RunRequest(Strict):
    node_id: str = Field(max_length=80)
    revision: int = Field(ge=1)
    request_key: str = Field(min_length=16, max_length=80)


def scheduled(kind: str, payload: dict):
    if kind in {"creative_load", "creative_stop"}:
        from ..model_switch import schedule

        return schedule(kind, payload)
    for record in list_jobs():
        if record["state"] not in TERMINAL and record["kind"] == kind:
            original = db.get("jobs", record["id"])
            if original["payload"] == payload:
                return record
    return {k: v for k, v in create_job(kind, payload).items() if k != "payload"}


@router.get("/catalog")
def catalog():
    return {
        "models": generations.catalog(),
        "workflows": WORKFLOWS,
        "presets": presets.listed(),
        "media_tools_available": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
        "components": components.listed(),
        "runtimes": [
            {
                "id": r["id"],
                "name": r.get("name", r["id"]),
                "h3_supported": services.runtime_support(r),
                "image_supported": services.runtime_support(r, "image-local"),
            }
            for r in db.all_records("runtimes")
        ],
        "h3_evidence": "https://github.com/1CatAI/1Cat-vLLM/pull/565",
        "h3_status": "experimental",
        "image_status": "native_z_image",
    }


class PresetRequest(Strict):
    runtime_id: str = Field(default="", max_length=80)
    gpu_uuids: list[str] | None = Field(default=None, max_length=32)


@router.post("/presets/{identity}")
def create_preset(identity: str, payload: PresetRequest):
    return presets.create(identity, payload.runtime_id, payload.gpu_uuids)


@router.post("/components/{identity}/download")
def download(identity: str):
    if identity not in {c["id"] for c in components.catalog()}:
        raise HTTPException(404, "Component not in verified source catalog")
    return components.schedule_download(identity)[0]


@router.get("/projects")
def projects():
    return {
        "items": [
            {
                "id": p["id"],
                "title": p["title"],
                "updated_at": p["updated_at"],
                "node_count": len(p["nodes"]),
            }
            for p in db.all_records("canvases")
        ]
    }


@router.post("/projects")
def create_project(payload: Document):
    return store.create(payload)


@router.get("/projects/{identity}")
def get_project(identity: str):
    value = store.project(identity)
    return {
        **value,
        "assets": [
            assets.public(store.require("creative_assets", a))
            for a in {n["asset_id"] for n in value["nodes"] if n.get("asset_id")}
        ],
    }


@router.put("/projects/{identity}")
def save_project(identity: str, payload: Document):
    return store.save(identity, payload)


@router.delete("/projects/{identity}")
def delete_project(identity: str):
    store.project(identity)
    runs.reconcile()
    if any(
        r["project_id"] == identity and r["state"] not in TERMINAL
        for r in db.all_records("creative_runs")
    ):
        raise ValueError("Cancel or finish this canvas's tasks before deleting it")
    db.delete("canvases", identity)
    return {"ok": True}


@router.post("/assets")
async def upload_asset(file: UploadFile):
    path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=assets.root(), suffix=".upload", delete=False
        ) as output:
            path = Path(output.name)
            count = 0
            while chunk := await file.read(1024**2):
                count += len(chunk)
                if count > assets.MAX_BYTES:
                    raise ValueError("Asset exceeds 256 MiB")
                output.write(chunk)
        result = await asyncio.to_thread(
            assets.ingest, path, file.content_type or "", file.filename or "asset"
        )
        return assets.public(result)
    finally:
        await file.close()
        if path:
            path.unlink(missing_ok=True)


@router.get("/assets/{identity}/content")
def read_asset(identity: str, download: bool = False):
    asset, path = assets.path_for(identity)
    return FileResponse(
        path,
        media_type=asset["mime"],
        filename=asset["name"] if download else None,
        headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/assets/{identity}/thumbnail")
def thumbnail(identity: str):
    asset, path = assets.path_for(identity, thumbnail=True)
    return FileResponse(
        path,
        media_type="image/jpeg" if asset.get("thumbnail") else asset["mime"],
        headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/services")
def list_services():
    return {"items": [services.public(s) for s in db.all_records("creative_services")]}


@router.post("/services")
def save_service(payload: Service):
    return services.save(payload)


@router.post("/services/{identity}/load")
def load_service(identity: str):
    store.require("creative_services", identity)
    return scheduled("creative_load", {"service_id": identity})


@router.post("/services/{identity}/stop")
def stop_service(identity: str):
    service = store.require("creative_services", identity)
    if service["kind"] not in {"h3-local", "image-local"}:
        raise ValueError("External services are managed by their owners")
    return scheduled("creative_stop", {"service_id": identity})


@router.get("/services/{identity}/log")
def service_log(identity: str):
    store.require("creative_services", identity)
    from ..config import state_root

    return {"text": engine.read_log_tail(state_root() / "logs" / f"creative-{identity}.log", 40000)}


@router.delete("/services/{identity}")
def delete_service(identity: str):
    store.require("creative_services", identity)
    if services.pending_lifecycle(identity):
        raise ValueError("Wait for the queued service operation before deleting its configuration")
    if (
        services.requires_stop(identity)
        and db.get("creative_services", identity)["kind"] in {"h3-local", "image-local"}
    ):
        raise ValueError("Stop the owned creative service before removing it")
    if any(
        r["service_id"] == identity and r["state"] not in TERMINAL
        for r in db.all_records("creative_runs")
    ):
        raise ValueError("Wait for pending tasks before removing this service")
    db.delete("creative_services", identity)
    db.delete("creative_instances", identity)
    db.delete("secrets", "creative-" + identity)
    return {"ok": True}


@router.get("/projects/{identity}/runs")
def list_runs(identity: str):
    store.project(identity)
    runs.reconcile()
    return {
        "items": [
            runs.public(r) for r in db.all_records("creative_runs") if r["project_id"] == identity
        ][:200]
    }


@router.post("/projects/{identity}/runs")
def create_run(identity: str, payload: RunRequest):
    return runs.public(runs.queue(identity, payload.node_id, payload.revision, payload.request_key))


@router.post("/runs/{identity}/cancel")
def cancel_run(identity: str):
    run = store.require("creative_runs", identity)
    if run["state"] not in TERMINAL and run.get("job_id"):
        db.patch("creative_runs", identity, {"cancel_requested": True})
        request_cancel(run["job_id"])
    return {"ok": True}


@router.post("/runs/{identity}/resume")
def resume_run(identity: str):
    runs.reconcile()
    run = store.require("creative_runs", identity)
    if run["state"] != "failed" or not run.get("upstream_id"):
        raise ValueError("Only an interrupted task with an existing backend ID can be resumed")
    # Monitoring resumes the original backend task; it never resubmits generation.
    db.patch(
        "creative_runs",
        identity,
        {"state": "queued", "stage": "queued", "error": None, "cancel_requested": False},
    )
    try:
        job = create_job("creative_generate", {"run_id": identity})
    except Exception:
        db.patch("creative_runs", identity, {"state": "failed"})
        raise
    db.patch("creative_runs", identity, {"job_id": job["id"]})
    return runs.public(store.require("creative_runs", identity))


@router.post("/generations/prepare")
def prepare_generation(payload: generations.Intent):
    return generations.prepare(payload)


@router.post("/generations", status_code=202)
def create_generation(payload: generations.Submit):
    return generations.submit(payload)


@router.get("/generations")
def list_generations(before: str | None = None, limit: int = 24):
    runs.reconcile()
    limit = min(60, max(1, limit))
    timestamp, identity = None, ""
    if before:
        try:
            value, _, identity = before.partition(":")
            timestamp = float(value)
            import math
            if not math.isfinite(timestamp):
                raise ValueError()
        except ValueError as error:
            raise HTTPException(422, "Invalid creation cursor") from error
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT data FROM records WHERE bucket='creative_runs' "
            "AND (? IS NULL OR json_extract(data,'$.created_at') < ? "
            "OR (json_extract(data,'$.created_at') = ? AND id < ?)) "
            "ORDER BY json_extract(data,'$.created_at') DESC, id DESC LIMIT ?",
            (timestamp, timestamp, timestamp, identity, limit+1)
        ).fetchall()
    import json
    records = [json.loads(row[0]) for row in rows]
    last = records[limit-1] if len(records) > limit else None
    return {"items": [runs.public(r) for r in records[:limit]],
            "next_cursor": f"{last['created_at']}:{last['id']}" if last else None}



@router.get("/runs/{identity}")
def get_run(identity: str):
    runs.reconcile()
    return runs.public(store.require("creative_runs", identity))


@router.post("/projects/{identity}/runs/prepare")
def prepare_canvas_generation(identity: str, payload: RunRequest):
    return generations.prepare_canvas(identity, payload.node_id, payload.revision)
