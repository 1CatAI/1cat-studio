# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import base64
import contextlib
import fcntl
import io
import json
import re
import tempfile
import time
from pathlib import Path

import httpx

from .. import db, gpu
from ..config import state_root
from ..jobs import TERMINAL, Cancelled, Job, create_job, list_jobs
from . import assets, services
from .output_sizes import service_sizes
from .schema import WORKFLOWS, compatible
from .store import finish_result, project, require


def validate_request(document: dict, node_id: str, service_override: dict | None = None) -> dict:
    node = next((n for n in document["nodes"] if n["id"] == node_id), None)
    if not node or node["kind"] != "generate":
        raise ValueError("Select a generation node")
    workflow = next((w for w in WORKFLOWS if w["id"] == node["workflow"]), None)
    service = service_override or require("creative_services", node["service_id"])
    if not workflow or not compatible(service, workflow):
        raise ValueError(
            "Workflow and model service are incompatible; FL2VA and Ref2VA use different loaded checkpoints"
        )
    sources = {n["id"]: n for n in document["nodes"]}
    inputs = [sources[e["source"]] for e in document["edges"] if e["target"] == node_id]
    if any(n["kind"] == "generate" for n in inputs):
        raise ValueError(
            "Connect a generated result as the reference, rather than its generation node"
        )
    prompt = "\n\n".join(
        [n["text"].strip() for n in inputs if n["kind"] == "text"] + [node["text"].strip()]
    ).strip()
    if not prompt or len(prompt) > 20000:
        raise ValueError("Provide a prompt of at most 20,000 characters")
    references = [
        require("creative_assets", n["asset_id"])
        for n in inputs
        if n["kind"] in {"image", "video", "audio"}
    ]
    grouped = {
        kind: [a for a in references if a["kind"] == kind] for kind in ("image", "video", "audio")
    }
    if not workflow["min_images"] <= len(grouped["image"]) <= workflow["max_images"]:
        raise ValueError(
            f"This workflow needs {workflow['min_images']}–{workflow['max_images']} connected image references"
        )
    if workflow["id"] == "h3-reference":
        if not grouped["image"] and not grouped["video"]:
            raise ValueError("Ref2VA needs at least one image or video reference")
        if len(references) > 12 or len(grouped["video"]) > 3 or len(grouped["audio"]) > 3:
            raise ValueError(
                "Ref2VA allows 9 images, 3 videos, 3 audio files and 12 total references"
            )
        for kind in ("video", "audio"):
            items = grouped[kind]
            if (
                any(not 2 <= a.get("duration", 0) <= 15 for a in items)
                or sum(a.get("duration", 0) for a in items) > 15
            ):
                raise ValueError(
                    "Reference video/audio must be 2–15 seconds each and no more than 15 seconds total per type"
                )
    elif grouped["video"] or grouped["audio"]:
        raise ValueError("This workflow does not accept video or audio references")
    for item in references:
        assets.path_for(item["id"])
        if (
            service["kind"].startswith("h3-")
            and item["bytes"] > {"image": 30, "video": 50, "audio": 15}[item["kind"]] * 1024**2
        ):
            raise ValueError(
                "H3 reference exceeds its upload limit (image 30 MiB, video 50 MiB, audio 15 MiB)"
            )
        if service["kind"].startswith("h3-") and item["mime"] == "video/webm":
            raise ValueError(
                "H3 currently requires MP4 reference video; WebM may be stored on the canvas"
            )
    parameters = node["parameters"].copy()
    from .fasth3 import is_service

    if is_service(service) and (
        parameters.get("num_inference_steps") not in (None, 4)
        or parameters.get("lora_scale", 1) != 1
    ):
        raise ValueError("FastH3 requires its four-step fused adapter at scale 1")
    if workflow["provider"] == "h3":
        if (
            min(parameters["width"], parameters["height"])
            not in {min(size) for size in service_sizes(service)}
            or max(parameters["width"], parameters["height"]) > 2048
            or parameters["width"] % 32
            or parameters["height"] % 32
        ):
            raise ValueError(
                "Choose a resolution supported by the selected H3 workflow (32-pixel grid)"
            )
        if not 96 <= parameters["num_frames"] <= 363:
            raise ValueError("Use an H3 output duration between 4 and 15 seconds")
    return {
        "node": node,
        "workflow": workflow,
        "service": service,
        "prompt": prompt,
        "references": references,
        "parameters": parameters,
    }


def queue(project_id: str, node_id: str, revision: int, request_key: str) -> dict:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", request_key):
        raise ValueError("A stable request key is required")
    for old in db.all_records("creative_runs"):
        if old["project_id"] == project_id and (
            old.get("request_key") == request_key
            or (old["node_id"] == node_id and old["state"] not in TERMINAL)
        ):
            return old
    document = project(project_id)
    if document["revision"] != revision:
        raise ValueError("Save the current canvas before generating")
    snapshot = validate_request(document, node_id)
    service = snapshot["service"]
    if services.instance(service["id"]).get("state") != "ready":
        raise ValueError("Connect or load the matching model service before generating")
    identity = db.uid()
    run = {
        "id": identity,
        "project_id": project_id,
        "node_id": node_id,
        "service_id": service["id"],
        "request_key": request_key,
        "state": "queued",
        "stage": "queued",
        "created_at": time.time(),
        "snapshot": snapshot,
        "assets": [],
    }
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        duplicate = conn.execute(
            "SELECT data FROM records WHERE bucket='creative_runs' AND json_extract(data,'$.project_id')=? AND (json_extract(data,'$.request_key')=? OR (json_extract(data,'$.node_id')=? AND json_extract(data,'$.state') NOT IN ('completed','failed','cancelled'))) LIMIT 1",
            (project_id, request_key, node_id),
        ).fetchone()
        if duplicate:
            return json.loads(duplicate[0])
        current_service = conn.execute(
            "SELECT data FROM records WHERE bucket='creative_services' AND id=?", (service["id"],)
        ).fetchone()
        current_instance = conn.execute(
            "SELECT data FROM records WHERE bucket='creative_instances' AND id=?", (service["id"],)
        ).fetchone()
        current_project = conn.execute(
            "SELECT data FROM records WHERE bucket='canvases' AND id=?", (project_id,)
        ).fetchone()
        if not current_project or json.loads(current_project[0])["revision"] != revision:
            raise ValueError("Canvas changed before submission; save it again")
        if (
            not current_service
            or json.loads(current_service[0]) != service
            or not current_instance
            or json.loads(current_instance[0]).get("state") != "ready"
        ):
            raise ValueError("The model service is stopping or changed before submission")
        conn.execute(
            "INSERT INTO records VALUES('creative_runs',?,?,?)",
            (identity, json.dumps(run, ensure_ascii=False), time.time()),
        )
    try:
        job = create_job("creative_generate", {"run_id": identity})
        db.patch("creative_runs", identity, {"job_id": job["id"]})
    except Exception as error:
        db.patch("creative_runs", identity, {"state": "failed", "error": str(error)})
        raise
    return require("creative_runs", identity)


def reconcile():
    jobs = {j["id"]: j for j in list_jobs()}
    for run in db.all_records("creative_runs"):
        if run["state"] in TERMINAL:
            continue
        job = jobs.get(run.get("job_id"))
        if job and job["state"] in {"failed", "cancelled"}:
            db.patch(
                "creative_runs",
                run["id"],
                {
                    "state": job["state"],
                    "error": job.get("error", "Worker stopped"),
                    "finished_at": job.get("finished_at", time.time()),
                },
            )
        elif not job and time.time() - run["created_at"] > 30:
            db.patch(
                "creative_runs",
                run["id"],
                {
                    "state": "failed",
                    "error": "Task was interrupted before worker registration",
                    "finished_at": time.time(),
                },
            )


def public(run: dict) -> dict:
    from . import presets, timing

    snap = run["snapshot"]
    return {
        **{k: v for k, v in run.items() if k not in {"snapshot", "request_key"}},
        "workflow": snap["workflow"]["id"],
        "model": snap["service"]["name"],
        "model_id": (run.get("intent") or {}).get("model") or presets.model_id(snap["service"]),
        "timing": timing.summary(run),
        "prompt": snap["prompt"],
        "parameters": snap["parameters"],
        "reference_assets": [assets.public(a) for a in snap.get("references", [])],
        "origin": {"x": snap["node"]["x"], "y": snap["node"]["y"]},
        "assets": [assets.public(require("creative_assets", a)) for a in run.get("assets", [])],
        "resumable": bool(
            run.get("upstream_id") and not run.get("upstream_lost") and run["state"] == "failed"
        ),
    }


class Meter:
    """Selected-board measurements, never presented as exclusive task energy."""

    def __init__(self, uuids: list[str]):
        self.uuids = set(uuids)
        self.previous = None
        self.joules = self.seconds = 0.0
        self.peak_mib = 0
        self.samples = 0

    def sample(self) -> dict:
        if not self.uuids:
            return {
                "source": "unavailable",
                "reason": "Remote service does not expose GPU telemetry",
            }
        now = time.monotonic()
        selected = [g for g in gpu.snapshot()["gpus"] if g["uuid"] in self.uuids]
        if len(selected) != len(self.uuids) or any(g.get("power_w") is None for g in selected):
            self.previous = None
            return {"source": "unavailable", "reason": "Incomplete GPU samples"}
        power = sum(g["power_w"] for g in selected)
        memory = sum(g.get("memory_used_mib") or 0 for g in selected)
        self.peak_mib = max(self.peak_mib, memory)
        if self.previous and now - self.previous[0] <= 5:
            delta = now - self.previous[0]
            self.joules += delta * (power + self.previous[1]) / 2
            self.seconds += delta
        self.previous = (now, power)
        self.samples += 1
        return {
            "source": "selected_gpu_boards",
            "power_w": power,
            "average_w": self.joules / self.seconds if self.seconds else None,
            "sampled_seconds": self.seconds,
            "memory_mib": memory,
            "peak_memory_mib": self.peak_mib,
            "samples": self.samples,
            "exclusive": False,
        }


def progress(job: Job, run_id: str, stage: str, **fields):
    job.update(stage, ignore_cancel=True)
    previous = db.get("creative_runs", run_id, {})
    if previous.get("stage") != stage or not previous.get("stage_started_at"):
        fields.setdefault("stage_started_at", time.time())
        fields.setdefault("detail", "")
    db.patch(
        "creative_runs",
        run_id,
        {"state": "running", "stage": stage, "updated_at": time.time(), **fields},
    )


def safe_video_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,160}", value):
        raise ValueError("Generation server returned an invalid task ID")
    return value


def capture_response(response, run_id: str, mime: str, name: str) -> dict:
    response.raise_for_status()
    with tempfile.NamedTemporaryFile(dir=assets.root(), suffix=".upload", delete=False) as tmp:
        path = Path(tmp.name)
        try:
            count = 0
            for chunk in response.iter_bytes():
                count += len(chunk)
                if count > assets.MAX_BYTES:
                    raise ValueError("Generated media exceeds the 256 MiB asset limit")
                tmp.write(chunk)
            tmp.close()
            return assets.ingest(path, mime, name, run_id=run_id)
        finally:
            path.unlink(missing_ok=True)


def video(job: Job, run: dict, http: httpx.Client, meter: Meter) -> list[dict]:
    snap = run["snapshot"]
    workflow = snap["workflow"]
    identity = run.get("upstream_id")
    if not identity:
        data = {
            **{k: v for k, v in snap["parameters"].items() if v is not None},
            "model": snap["service"]["model"],
            "prompt": snap["prompt"],
            "task": workflow["task"],
        }
        if "keyframe_indices" in workflow:
            data["keyframe_indices"] = json.dumps(workflow["keyframe_indices"])
        job.check_cancelled()
        progress(job, run["id"], "submitting")
        # A submission with no returned ID is ambiguous. Never retry it automatically.
        with contextlib.ExitStack() as stack:
            files = []
            for ref in snap["references"]:
                _, path = assets.path_for(ref["id"])
                if ref["kind"] == "image" and workflow.get("task") == "fl2va":
                    # Preserve the reference's proportions. The native legacy
                    # API resizes its input to the requested canvas, so supply
                    # the recipe's centered crop without modifying the asset.
                    from PIL import Image, ImageOps

                    prepared = stack.enter_context(io.BytesIO())
                    with Image.open(path) as source:
                        frame = ImageOps.fit(
                            ImageOps.exif_transpose(source).convert("RGB"),
                            (snap["parameters"]["width"], snap["parameters"]["height"]),
                            method=Image.Resampling.LANCZOS,
                        )
                        frame.save(prepared, format="PNG")
                    prepared.seek(0)
                    files.append(("image_reference", ("keyframe.png", prepared, "image/png")))
                    continue
                files.append(
                    (
                        ref["kind"] + "_reference",
                        (ref["name"], stack.enter_context(path.open("rb")), ref["mime"]),
                    )
                )
            response = (
                http.post("/v1/videos", data={k: str(v) for k, v in data.items()}, files=files)
                if files
                else http.post("/v1/videos", json=data)
            )
        response.raise_for_status()
        identity = safe_video_id(response.json().get("id"))
        db.patch("creative_runs", run["id"], {"upstream_id": identity})
    return track_native(
        job, run, http, meter, "/v1/videos", identity, "video/mp4", "generation.mp4"
    )


def native_image(job: Job, run: dict, http: httpx.Client, meter: Meter) -> list[dict]:
    snap = run["snapshot"]
    identity = run.get("upstream_id")
    if not identity:
        job.check_cancelled()
        progress(job, run["id"], "submitting")
        response = http.post(
            "/v1/images/jobs",
            json={
                "model": snap["service"]["checkpoint"],
                "prompt": snap["prompt"],
                **{k: snap["parameters"][k] for k in ("width", "height", "seed")},
            },
            headers={"Idempotency-Key": run["id"]},
        )
        response.raise_for_status()
        identity = safe_video_id(response.json().get("id"))
        db.patch("creative_runs", run["id"], {"upstream_id": identity})
    return track_native(
        job, run, http, meter, "/v1/images/jobs", identity, "image/png", "generation.png"
    )


def track_native(job, run, http, meter, prefix, identity, mime, filename):
    identity = safe_video_id(identity)
    endpoint = prefix + "/" + identity
    cancel_attempted, failures = False, 0
    while True:
        try:
            response = http.get(endpoint)
            if response.status_code == 404:
                db.patch("creative_runs", run["id"], {"upstream_lost": True})
                raise ValueError(
                    "原生任务已不存在，服务可能已重启。请重新生成 / Native task is missing after service restart; create a new generation"
                )
            response.raise_for_status()
            data = response.json()
            failures = 0
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            failures += 1
            if failures >= 60 or (
                isinstance(error, httpx.HTTPStatusError) and error.response.status_code < 500
            ):
                raise
            progress(
                job,
                run["id"],
                require("creative_runs", run["id"]).get("stage", "generating"),
                connection_lost=True,
                detail="正在重新连接原生任务 / Reconnecting to native task",
            )
            time.sleep(2)
            continue
        count = data.get("denoise_progress")
        real_count = None
        if isinstance(count, dict):
            done, total = count.get("completed"), count.get("total")
            if type(done) is int and type(total) is int and 0 <= done <= total and total > 0:
                real_count = {"completed": done, "total": total, "source": "backend_denoise_steps"}
        status = data.get("status")
        if status == "completed":
            db.patch("creative_runs", run["id"], {"denoise_progress": real_count})
            db.patch(
                "creative_runs",
                run["id"],
                {"native_result": data.get("result"), "connection_lost": False},
            )
            break
        if status in {"failed", "cancelled", "canceled"}:
            if status != "failed":
                raise Cancelled("Generation cancelled by the media service")
            error = data.get("error", {})
            raise ValueError(
                error.get("message", "Media generation failed")
                if isinstance(error, dict)
                else str(error)
            )
        if db.get("jobs", job.id, {}).get("cancel_requested") and not cancel_attempted:
            cancel_attempted = True
            if prefix == "/v1/images/jobs":
                cancelled = http.delete(endpoint)
            elif run["snapshot"]["service"]["kind"] == "h3-local":
                cancelled = http.post(endpoint + "/cancel")
            else:
                cancelled = http.delete(endpoint)
            if cancelled.is_success:
                answer = cancelled.json()
                if answer.get("status") in {"cancelled", "canceled"} or answer.get("deleted"):
                    raise Cancelled("Generation cancelled by the media service")
            db.patch(
                "creative_runs",
                run["id"],
                {
                    "cancel_note": "等待当前生成结束，结果将保留 / Waiting for the current generation; output will be retained"
                },
            )
        stage = data.get("stage")
        if stage == "loading_weights":
            stage = "staging_model"
        if stage not in {
            "encoding",
            "denoising",
            "decoding",
            "staging_model",
            "packaging",
            "saving",
        }:
            stage = "queued" if status == "queued" else "generating"
        progress(
            job,
            run["id"],
            stage,
            denoise_progress=real_count,
            connection_lost=False,
            native_updated_at=data.get("updated_at"),
            telemetry=meter.sample(),
        )
        time.sleep(1)
    progress(job, run["id"], "saving", telemetry=meter.sample())
    with http.stream("GET", endpoint + "/content") as response:
        return [capture_response(response, run["id"], mime, filename)]


def image(job: Job, run: dict, http: httpx.Client) -> list[dict]:
    snap = run["snapshot"]
    params = snap["parameters"]
    data = {
        "model": snap["service"]["model"],
        "prompt": snap["prompt"],
        "n": 1,
        "size": f"{params['width']}x{params['height']}",
        "response_format": "b64_json",
    }
    progress(job, run["id"], "generating")
    job.check_cancelled()
    if snap["workflow"]["id"] == "image-edit":
        ref = snap["references"][0]
        _, path = assets.path_for(ref["id"])
        with path.open("rb") as file:
            response = http.post(
                "/v1/images/edits",
                data=data,
                files={"image": (ref["name"], file, ref["mime"])},
                timeout=600,
            )
    else:
        response = http.post("/v1/images/generations", json=data, timeout=600)
    response.raise_for_status()
    items = response.json().get("data", [])
    if not items or len(items) != 1:
        raise ValueError("Image service did not return exactly one generated image")
    progress(job, run["id"], "saving")
    encoded = items[0].get("b64_json")
    if not encoded:
        raise ValueError(
            "Image service must return b64_json; external result URLs are not fetched automatically"
        )
    if len(encoded) > assets.IMAGE_BYTES * 4 // 3 + 8:
        raise ValueError("Generated image exceeds 30 MiB")
    raw = base64.b64decode(encoded, validate=True)
    mime = (
        "image/png"
        if raw.startswith(b"\x89PNG")
        else "image/webp"
        if raw[:4] == b"RIFF"
        else "image/jpeg"
    )
    with tempfile.NamedTemporaryFile(dir=assets.root(), delete=False) as tmp:
        path = Path(tmp.name)
        tmp.write(raw)
    try:
        return [
            assets.ingest(path, mime, "generation" + assets.MIME_SUFFIX[mime], run_id=run["id"])
        ]
    finally:
        path.unlink(missing_ok=True)


def execute(job: Job):
    run = require("creative_runs", job.payload["run_id"])
    if run["state"] == "completed":
        return {"run_id": run["id"]}
    service = run["snapshot"]["service"]
    try:
        lockpath = state_root() / "jobs" / ("creative-" + service["id"] + ".lock")
        with lockpath.open("a") as lock:
            while True:
                job.check_cancelled()
                reconcile()
                older = [
                    r
                    for r in db.all_records("creative_runs")
                    if r["service_id"] == service["id"]
                    and r["created_at"] < run["created_at"]
                    and r["state"] not in TERMINAL
                ]
                if not older:
                    try:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        pass
                progress(job, run["id"], "queued")
                time.sleep(0.5)
            # Refuse an edited service so queued tasks keep their original configuration.
            if require("creative_services", service["id"]) != service:
                raise ValueError("Service configuration changed while this task was queued")
            services.check(service)
            meter = Meter(service.get("gpu_uuids", []))
            db.patch(
                "creative_runs",
                run["id"],
                {
                    "started_at": run.get("started_at") or time.time(),
                    "finished_at": None,
                    "telemetry": meter.sample(),
                },
            )
            with services.client(service) as http:
                output = (
                    native_image(job, run, http, meter)
                    if service["kind"] == "image-local"
                    else image(job, run, http)
                    if service["kind"] == "image-api"
                    else video(job, run, http, meter)
                )
            finish_result(run, output)
            # Remove only this managed service's finished staging output, after
            # the permanent Studio asset and completed run have been committed.
            latest = require("creative_runs", run["id"])
            if service["kind"] == "h3-local" and latest.get("upstream_id"):
                with (
                    contextlib.suppress(httpx.HTTPError, ValueError),
                    services.client(service) as http,
                ):
                    http.delete("/v1/videos/" + safe_video_id(latest["upstream_id"]))
            return {"run_id": run["id"], "assets": [a["id"] for a in output]}
    except BaseException as error:
        db.patch(
            "creative_runs",
            run["id"],
            {
                "state": "cancelled" if isinstance(error, Cancelled) else "failed",
                "error": (str(error) or type(error).__name__)[:2000],
                "finished_at": time.time(),
            },
        )
        raise
