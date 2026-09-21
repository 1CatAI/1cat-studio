# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import base64
import io
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from onecat import db, jobs
from onecat.creative import assets, components, runs, services, store
from onecat.creative.schema import Document, Node, Service
from PIL import Image


@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    monkeypatch.setenv("ONECAT_AUTO_GPU_ACTIONS", "0")
    monkeypatch.setattr(services.gpu, "snapshot", lambda: {"gpus": [], "available": True})


def png():
    data = io.BytesIO()
    Image.new("RGB", (32, 24), (30, 120, 90)).save(data, "PNG")
    return data.getvalue()


def node(**values):
    return Node(
        **{
            "id": "generator",
            "kind": "generate",
            "x": 0,
            "y": 0,
            "text": "A quiet forest",
            "workflow": "image-text",
            **values,
        }
    )


def image_service():
    result = services.save(
        Service(
            name="Test image",
            kind="image-api",
            model="fixture-model",
            base_url="http://127.0.0.1:9009",
        )
    )
    db.patch("creative_instances", result["id"], {"state": "ready"})
    return db.get("creative_services", result["id"])


def run_record(service, workflow="image-text", references=None):
    identity = db.uid()
    n = node(service_id=service["id"]).model_dump()
    doc = store.create(Document(nodes=[Node.model_validate(n)]))
    snapshot = runs.validate_request(doc, n["id"])
    if workflow != "image-text":
        from onecat.creative.schema import WORKFLOWS

        snapshot["workflow"] = next(w for w in WORKFLOWS if w["id"] == workflow)
    snapshot["references"] = references or []
    result = {
        "id": identity,
        "project_id": doc["id"],
        "node_id": n["id"],
        "service_id": service["id"],
        "state": "queued",
        "stage": "queued",
        "created_at": time.time(),
        "snapshot": snapshot,
        "assets": [],
    }
    db.put("creative_runs", identity, result)
    jid = db.uid()
    db.put(
        "jobs",
        jid,
        {
            "id": jid,
            "kind": "creative_generate",
            "state": "running",
            "payload": {"run_id": identity},
            "cancel_requested": False,
        },
    )
    db.patch("creative_runs", identity, {"job_id": jid})
    return result, jobs.Job(jid)


def test_project_conflict_and_dangling_edges(state):
    p = store.create(Document(nodes=[node()]))
    a = Document.model_validate({k: v for k, v in p.items() if k not in {"id", "updated_at"}})
    b = a.model_copy(deep=True)
    a.title = "First tab"
    assert store.save(p["id"], a)["revision"] == 2
    with pytest.raises(HTTPException) as error:
        store.save(p["id"], b)
    assert error.value.status_code == 409
    assert store.project(p["id"])["title"] == "First tab"
    with pytest.raises(ValueError):
        Document(nodes=[node()], edges=[{"id": "e", "source": "missing", "target": "generator"}])


def test_graph_cycle_and_media_type_rejected(state):
    with pytest.raises(ValueError, match="cycle"):
        Document(
            nodes=[node(), node(id="other")],
            edges=[
                {"id": "e1", "source": "generator", "target": "other"},
                {"id": "e2", "source": "other", "target": "generator"},
            ],
        )


def test_assets_auth_range_and_content_validation(client, state):
    result = client.post(
        "/api/creative/assets", files={"file": ("reference.png", png(), "image/png")}
    )
    assert result.status_code == 200, result.text
    item = result.json()
    assert item["width"] == 32 and "filename" not in item
    assert client.get(item["thumbnail_url"]).status_code == 200
    response = client.get(item["url"], headers={"Range": "bytes=0-7"})
    assert response.status_code == 206 and response.content == png()[:8]
    bad = client.post(
        "/api/creative/assets", files={"file": ("evil.png", b"<script>bad</script>", "image/png")}
    )
    assert bad.status_code in {400, 422}
    client.post("/api/auth/logout")
    assert client.get(item["url"]).status_code == 401


def test_default_service_has_no_keys_in_public_record(state):
    service = services.save(
        Service(
            name="Image",
            kind="image-api",
            model="x",
            base_url="http://localhost:8000/v1",
            api_key="secret-test",
        )
    )
    assert "secret-test" not in json.dumps(service)
    assert service["has_key"] and service["base_url"] == "http://localhost:8000"
    with pytest.raises(ValueError):
        services.endpoint("http://user:password@localhost:8000")


def test_local_model_never_implicitly_downloads_hf(state):
    with pytest.raises(ValueError, match="local H3"):
        services.local_paths({"model": "MiniMaxAI/MiniMax-H3", "partition": "fl2va"})
    assert not services.runtime_support(
        {"validated": True, "capabilities": {"source_path": str(state)}}
    )


def test_idempotent_submission_and_immutable_prompt(state, monkeypatch):
    service = image_service()
    p = store.create(Document(nodes=[node(service_id=service["id"])]))
    submitted = []

    def create(kind, payload):
        submitted.append(payload)
        return {"id": db.uid()}

    monkeypatch.setattr(runs, "create_job", create)
    a = runs.queue(p["id"], "generator", 1, "same-request-key-1234")
    b = runs.queue(p["id"], "generator", 1, "same-request-key-1234")
    assert a["id"] == b["id"] and len(submitted) == 1
    changed = Document.model_validate({k: v for k, v in p.items() if k not in {"id", "updated_at"}})
    changed.nodes[0].text = "Edited later"
    store.save(p["id"], changed)
    assert db.get("creative_runs", a["id"])["snapshot"]["prompt"] == "A quiet forest"


def test_image_generation_saves_immutable_asset_without_rewriting_canvas(state, monkeypatch):
    service = image_service()
    run, job = run_record(service)
    calls = []

    def transport(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": service["model"]}]})
        assert json.loads(request.content)["response_format"] == "b64_json"
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})

    monkeypatch.setattr(
        services,
        "client",
        lambda *a, **kw: httpx.Client(
            base_url="http://localhost", transport=httpx.MockTransport(transport)
        ),
    )
    before = store.project(run["project_id"])
    result = runs.execute(job)
    assert len(result["assets"]) == 1
    assert db.get("creative_runs", run["id"])["state"] == "completed"
    assert store.project(run["project_id"]) == before
    assert assets.path_for(result["assets"][0])[1].is_file()
    assert calls.count("/v1/images/generations") == 1


def test_video_progress_does_not_confuse_output_count_with_denoising(state, monkeypatch):
    service = image_service()
    run, job = run_record(service)
    run["snapshot"]["workflow"] = {"task": "t2va"}
    run["snapshot"]["parameters"] = {"width": 1344, "height": 768, "num_frames": 107}
    run["upstream_id"] = "video_existing"
    polled = []

    def transport(request):
        assert request.method == "GET", "Resuming must never submit another generation"
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"video")
        if not polled:
            polled.append(True)
            return httpx.Response(200, json={"status": "in_progress", "progress": 50})
        observed = db.get("creative_runs", run["id"])
        assert observed["denoise_progress"] is None and observed["stage"] == "generating"
        return httpx.Response(
            200, json={"status": "completed", "content_url": "http://evil.invalid/credential"}
        )

    monkeypatch.setattr(runs.time, "sleep", lambda *a: None)
    monkeypatch.setattr(runs, "capture_response", lambda response, *args: {"id": "asset"})
    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(transport)
    ) as http:
        assert runs.video(job, run, http, runs.Meter([])) == [{"id": "asset"}]


def test_late_cancel_keeps_already_completed_result(state, monkeypatch):
    service = image_service()
    run, job = run_record(service)
    run["upstream_id"] = "video_existing"
    db.patch("jobs", job.id, {"cancel_requested": True})

    def transport(request):
        if request.method == "DELETE":
            return httpx.Response(409)
        return httpx.Response(200, json={"status": "completed"})

    monkeypatch.setattr(runs, "capture_response", lambda *args: {"id": "asset"})
    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(transport)
    ) as http:
        runs.video(job, run, http, runs.Meter([]))
    assert not db.get("creative_runs", run["id"]).get("cancel_note")


def test_invalid_backend_id_and_foreign_asset_rejected(state):
    for value in ["../../secrets", "foo?token=x", "http://foreign/x", None]:
        with pytest.raises(ValueError):
            runs.safe_video_id(value)
    with pytest.raises(HTTPException):
        store.create(Document(nodes=[Node(id="n", kind="image", x=0, y=0, asset_id="a" * 32)]))


def test_component_catalog_is_pinned_and_only_modelscope(state):
    catalog = components.catalog()
    assert {c["id"] for c in catalog} >= {"h3-shared", "z-image-turbo", "z-image"}
    for entry in catalog:
        assert entry["repo_id"] in {
            "MiniMax/MiniMax-H3",
            "Tongyi-MAI/Z-Image-Turbo",
            "Tongyi-MAI/Z-Image",
            "Comfy-Org/MiniMax-H3",
            "lightx2v/Minimax-h3-Turbo",
            "FastVideo/FastVideo-FastH3-4-step-Preview-v1-LoRA",
        }
        assert entry["files"] and all(len(f["sha256"]) == 64 for f in entry["files"])
        assert all(".." not in Path(f["path"]).parts for f in entry["files"])
        if entry["role"] == "lora":
            assert not any("comfyui" in f["path"] for f in entry["files"])


def test_telemetry_does_not_claim_missing_or_remote_power(state, monkeypatch):
    assert runs.Meter([]).sample()["source"] == "unavailable"
    monkeypatch.setattr(
        runs.gpu,
        "snapshot",
        lambda: {"gpus": [{"uuid": "a", "power_w": 50, "memory_used_mib": 20}]},
    )
    assert runs.Meter(["a", "b"]).sample()["source"] == "unavailable"
    item = runs.Meter(["a"]).sample()
    assert item["power_w"] == 50 and item["average_w"] is None and not item["exclusive"]


def test_model_match_is_required_for_ready(state, monkeypatch):
    service = image_service()
    monkeypatch.setattr(
        services,
        "client",
        lambda *a, **k: httpx.Client(
            base_url="http://localhost",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"data": [{"id": "different-model"}]})
            ),
        ),
    )
    with pytest.raises(ValueError, match="not exposed"):
        services.check(service)


def test_load_failure_is_visible_without_a_process(state, monkeypatch):
    service = image_service()
    jid = db.uid()
    db.put(
        "jobs",
        jid,
        {
            "id": jid,
            "kind": "creative_load",
            "state": "running",
            "payload": {"service_id": service["id"]},
            "cancel_requested": False,
        },
    )

    def failed(*a):
        raise ValueError("wrong model service")

    monkeypatch.setattr(services, "check", failed)
    with pytest.raises(ValueError):
        services.lifecycle(jobs.Job(jid))
    assert services.public(service)["instance"]["error"] == "wrong model service"
    assert services.public(service)["instance"]["state"] == "failed"


def test_occupied_gpu_refusal_never_starts_or_stops_process(state, monkeypatch):
    service = image_service()
    service.update(kind="h3-local", runtime_id="test-runtime", gpu_uuids=["GPU-test"])
    db.put("creative_services", service["id"], service)
    db.put("creative_instances", service["id"], {"state": "stopped"})
    db.put("runtimes", "test-runtime", {"id": "test-runtime"})
    jid = db.uid()
    db.put(
        "jobs",
        jid,
        {
            "id": jid,
            "kind": "creative_load",
            "state": "running",
            "payload": {"service_id": service["id"]},
            "cancel_requested": False,
        },
    )
    monkeypatch.setattr(services, "runtime_support", lambda r, kind="h3-local": True)
    monkeypatch.setattr(services, "local_paths", lambda r: None)
    monkeypatch.setattr(
        services.gpu,
        "selected_devices",
        lambda u: [{"uuid": "GPU-test", "processes": [{"pid": 123}]}],
    )

    def forbidden(*a, **k):
        raise AssertionError("Hardware/process mutation attempted")

    monkeypatch.setattr(services.subprocess, "Popen", forbidden)
    monkeypatch.setattr(services.os, "killpg", forbidden)
    with pytest.raises(ValueError, match="in use"):
        services.lifecycle(jobs.Job(jid))


def test_service_change_is_refused_while_its_request_is_pending(state):
    service = image_service()
    run_record(service)
    value = Service.model_validate({k: v for k, v in service.items() if k != "updated_at"})
    value.name = "Edited"
    with pytest.raises(ValueError, match="pending"):
        services.save(value)


def test_real_video_container_inspection_and_false_mime(state):
    path = state / "sample.mp4"
    subprocess = __import__("subprocess")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=32x24:r=24:d=2",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        check=True,
    )
    raw = path.read_bytes()
    value = assets.ingest(path, "video/mp4", "sample.mp4")
    assert value["duration"] == 2 and value["width"] == 32
    wrong = state / "wrong.webm"
    wrong.write_bytes(raw)
    with pytest.raises(ValueError, match="container"):
        assets.ingest(wrong, "video/webm", "wrong.webm")
