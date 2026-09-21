# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Prompt-first preparation safety, native jobs, and durable artwork browsing."""

import io
import time

import httpx
import pytest
from fastapi import HTTPException
from onecat import db, jobs
from onecat.creative import assets, runs, services
from onecat.creative import generations as g
from PIL import Image
from pydantic import ValidationError


def test_real_validation_does_not_survive_a_source_or_checkpoint_change(state):
    model = {"id": "z-image"}
    runtime = {"capabilities": {"source_fingerprints": {"python_tree_sha256": "code-v1"}}}
    installed = {"z-image": {"installed": {"manifest_sha256": "model-v1"}}}
    evidence = {
        "state": "verified",
        "source_fingerprint": "code-v1",
        "component_manifests": {"z-image": "model-v1"},
        "workflows": ["image-text"],
    }
    db.put("creative_validations", "z-image", evidence)
    assert g.validation_status(model, runtime, installed) == "verified"
    changed_runtime = {"capabilities": {"source_fingerprints": {"python_tree_sha256": "code-v2"}}}
    assert g.validation_status(model, changed_runtime, installed) == "unverified"
    installed["z-image"]["installed"]["manifest_sha256"] = "model-v2"
    assert g.validation_status(model, runtime, installed) == "unverified"
    assert g.validation_status(model, None, {}) == "unverified"


def test_native_image_can_be_stopped_but_external_service_cannot(state, monkeypatch):
    from onecat.creative import api

    calls = []
    monkeypatch.setattr(api, "scheduled", lambda kind, payload: calls.append((kind, payload)))
    db.put("creative_services", "native", {"id": "native", "kind": "image-local"})
    db.put("creative_services", "external", {"id": "external", "kind": "image-api"})
    api.stop_service("native")
    assert calls == [("creative_stop", {"service_id": "native"})]
    with pytest.raises(ValueError, match="owners"):
        api.stop_service("external")
    assert len(calls) == 1


@pytest.mark.parametrize("separator", ["\n", "", "INFO: worker is loading\n"])
def test_multirank_loading_counts_completed_components_and_waits_for_all_ranks(separator):
    import json

    def line(rank, done):
        return "ONECAT_MEDIA_PROGRESS " + json.dumps(
            {
                "rank": rank,
                "world_size": 4,
                "completed": done,
                "total": 4,
                "component": "text_encoder",
            }
        )

    first = services.load_progress(separator.join([line(0, 1), line(1, 1), line(2, 0), line(3, 0)]), {})
    assert first["phase_progress"] == {"done": 2, "total": 16, "percent": 12.5}
    assert "text encoder" in first["detail"]
    following = services.load_progress(separator.join(line(rank, 4) for rank in range(3)), first)
    assert following["phase"] == "loading_weights"
    assert following["phase_progress"]["done"] == 12
    complete = services.load_progress(line(3, 4), following)
    assert complete["phase"] == "checking_service"
    assert complete["phase_progress"]["done"] == 16


def test_stage_change_clears_previous_waiting_explanation(state):
    class Job:
        def update(self, *args, **kwargs):
            pass

    db.put(
        "creative_runs",
        "test",
        {"id": "test", "stage": "waiting_resources", "detail": "Waiting for external GPU work"},
    )
    runs.progress(Job(), "test", "encoding")
    assert db.get("creative_runs", "test")["detail"] == ""


def test_download_verification_is_exposed_as_its_actual_stage(state, monkeypatch):
    class Job:
        def check_cancelled(self):
            pass

        def update(self, *args, **kwargs):
            pass

    db.put("creative_runs", "verify", {"id": "verify", "stage": "downloading"})
    db.put("jobs", "child", {"id": "child", "state": "running", "stage": "checking_model"})
    monkeypatch.setattr(g, "list_jobs", lambda: None)
    monkeypatch.setattr(
        g.time, "sleep", lambda _: db.patch("jobs", "child", {"state": "completed"})
    )
    g.wait_child(Job(), "verify", {"id": "child"}, "downloading")
    assert db.get("creative_runs", "verify")["stage"] == "checking_model"


def test_cancelling_generation_preserves_a_reused_component_download(state, monkeypatch):
    class Job:
        def check_cancelled(self):
            raise jobs.Cancelled("Cancelled generation")

    db.put("creative_runs", "waiting", {"id": "waiting", "stage": "checking_model"})
    db.put("jobs", "existing", {"id": "existing", "state": "running", "stage": "checking_model"})
    cancelled = []
    monkeypatch.setattr(g, "request_cancel", cancelled.append)
    with pytest.raises(jobs.Cancelled):
        g.wait_child(Job(), "waiting", {"id": "existing"}, "downloading", cancel_child=False)
    assert not cancelled
    assert db.get("jobs", "existing")["state"] == "running"


def test_keyframes_are_cropped_without_stretching_or_changing_saved_asset(
    state, tmp_path, monkeypatch
):
    original = Image.new("RGB", (256, 512), "red")
    original.paste((0, 255, 0), (0, 128, 256, 384))
    path = tmp_path / "portrait.png"
    original.save(path)
    asset = assets.ingest(path, "image/png", "portrait.png")
    _, saved = assets.path_for(asset["id"])
    before = saved.read_bytes()
    record = {
        "id": db.uid(),
        "snapshot": {
            "workflow": {"task": "fl2va", "keyframe_indices": [0]},
            "service": {"model": "local"},
            "prompt": "A scene",
            "parameters": {"width": 512, "height": 256},
            "references": [asset],
        },
    }
    db.put("creative_runs", record["id"], record)

    class Job:
        def check_cancelled(self):
            pass

        def update(self, *args, **kwargs):
            pass

    def native(request):
        data = request.read()
        offset = data.index(b"\x89PNG")
        with Image.open(io.BytesIO(data[offset:])) as frame:
            assert frame.size == (512, 256)
            assert frame.getpixel((0, 0)) == (0, 255, 0)
            assert frame.getpixel((511, 255)) == (0, 255, 0)
        return httpx.Response(200, json={"id": "video_crop"})

    monkeypatch.setattr(runs, "track_native", lambda *args: [])
    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(native)) as client:
        runs.video(Job(), record, client, runs.Meter([]))
    assert saved.read_bytes() == before


def test_pagination_keeps_tasks_created_at_the_same_time(state, monkeypatch):
    from onecat.creative import api

    monkeypatch.setattr(runs, "reconcile", lambda: None)
    monkeypatch.setattr(runs, "public", lambda value: value)
    expected = set()
    for i in range(7):
        identity = f"{i:032x}"
        expected.add(identity)
        db.put(
            "creative_runs", identity, {"id": identity, "created_at": 123.25, "state": "completed"}
        )
    seen = []
    cursor = None
    while True:
        page = api.list_generations(before=cursor, limit=2)
        seen.extend(r["id"] for r in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 7 and set(seen) == expected
    with pytest.raises(HTTPException):
        api.list_generations(before="nan")


@pytest.fixture
def world(state, monkeypatch, tmp_path):
    devices = [
        {"uuid": f"GPU-{i}", "index": i, "name": "V100", "memory_total_mib": 32768, "processes": []}
        for i in range(4)
    ]
    monkeypatch.setattr(g.gpu, "snapshot", lambda: {"gpus": devices})
    monkeypatch.setattr(
        g.gpu, "selected_devices", lambda ids: [d for d in devices if d["uuid"] in ids]
    )
    monkeypatch.setattr(g, "workbench_runtime", lambda r, kind: bool(r.get("validated")))
    monkeypatch.setattr(
        services, "runtime_support", lambda r, kind="h3-local": bool(r.get("validated"))
    )
    monkeypatch.setattr(
        services, "requires_stop", lambda sid: services.instance(sid).get("state") == "ready"
    )
    monkeypatch.setattr(services, "owned_pids", lambda: set())
    monkeypatch.setattr(g.engine, "owned_pids", lambda: set())
    monkeypatch.setattr(g.engine, "private_state", dict)
    runtime = {"id": "runtime", "validated": True}
    db.put("runtimes", runtime["id"], runtime)
    spawned = []

    def create(kind, payload):
        record = {
            "id": db.uid(),
            "kind": kind,
            "payload": payload,
            "state": "queued",
            "created_at": time.time(),
        }
        db.put("jobs", record["id"], record)
        spawned.append(record)
        return record

    monkeypatch.setattr(g, "create_job", create)
    monkeypatch.setattr(runs, "create_job", create)
    return devices, spawned


def media(tmp_path, kind="image"):
    path = tmp_path / (db.uid() + ".png")
    Image.new("RGB", (256, 256), "orange").save(path)
    record = assets.ingest(path, "image/png", "frame.png")
    if kind != "image":
        record.update(kind=kind, duration=3)
        db.put("creative_assets", record["id"], record)
    return record


def test_text_video_never_selects_reference_partition(world):
    prepared = g.prepare(g.Intent(prompt="A bird flying"))
    assert prepared["selection"]["recipe_id"] == "h3-fl2va-turbo4"
    assert prepared["missing_components"]
    assert not world[1]


@pytest.mark.parametrize("model", ["h3", "h3-turbo8", "h3-int8-20step", "z-image-turbo"])
def test_vsa_cannot_be_silently_applied_to_other_weights(model):
    with pytest.raises(ValueError, match="FastH3"):
        g.Intent(model=model, prompt="A boat", fast=True)


@pytest.mark.parametrize("fast", [False, True])
def test_fasth3_binds_original_weights_exact_adapter_and_four_intervals(world, fast):
    from onecat.creative import fasth3

    db.patch("runtimes", "runtime", {"capabilities": {"h3_fastpath": {
        "profile": "sm70-dense-v1", "available": True,
        "fasth3": {"available": True, "vsa_available": True},
    }}})
    intent = g.Intent(model=fasth3.MODEL_ID, prompt="A boat", width=1280, height=736,
                      num_frames=120, fast=fast)
    prepared = g.prepare(intent)
    assert prepared["selection"]["recipe_id"] == fasth3.RECIPES[fast]
    assert set(prepared["missing_components"]) == {
        "h3-shared", "h3-original-fl2va", fasth3.RECIPES[fast],
    }
    assert g.document(intent, "service")["nodes"][0]["parameters"]["num_inference_steps"] == 4
    run = g.submit(g.Submit(preparation_id=prepared["id"], request_key="fasth3-saved-intent"))
    assert run["intent"]["fast"] is fast
    assert run["parameters"]["num_inference_steps"] == 4


def test_fasth3_is_not_available_on_old_runtime_and_rejects_references(world):
    from onecat.creative import fasth3

    with pytest.raises(ValueError, match="runtime"):
        g.prepare(g.Intent(model=fasth3.MODEL_ID, prompt="A boat", fast=True))
    with pytest.raises(ValueError, match="text to video"):
        g.Intent(model=fasth3.MODEL_ID, prompt="A boat", references=[
            g.Reference(asset_id="a" * 32, role="first"),
        ])
    model = next(m for m in g.catalog() if m["id"] == fasth3.MODEL_ID)
    assert not model["runtime_available"]
    assert not model["fast_available"]
    assert model["validation"] == "unverified"


def test_fast_selection_chooses_capable_runtime_instead_of_existing_old_one(world):
    from onecat.creative import fasth3

    db.put("creative_services", "legacy", {"id": "legacy", "kind": "h3-local", "runtime_id": "runtime"})
    db.put("runtimes", "new", {"id": "new", "validated": True, "capabilities": {"h3_fastpath": {
        "profile": "sm70-dense-v1", "available": True,
        "fasth3": {"available": True, "vsa_available": True},
    }}})
    chosen = g.selection(g.Intent(model=fasth3.MODEL_ID, prompt="A boat", fast=True))
    assert chosen["runtime_id"] == "new"


def test_fast_flag_is_strict_and_old_intent_remains_standard():
    assert not g.Intent(prompt="A boat").fast
    with pytest.raises(ValidationError):
        g.Intent(model="h3-fasth3", prompt="A boat", fast="false")


@pytest.mark.parametrize(
    "model,suffix,nfe",
    [("h3", "turbo4", 4), ("h3-turbo8", "turbo8", 8), ("h3-int8-20step", "base", 20)],
)
@pytest.mark.parametrize(
    "role,partition", [(None, "fl2va"), ("first", "fl2va"), ("reference", "ref2va")]
)
def test_actual_h3_variants_bind_partition_adapter_and_sampler(
    world, tmp_path, model, suffix, nfe, role, partition
):
    refs = [g.Reference(asset_id=media(tmp_path)["id"], role=role)] if role else []
    intent = g.Intent(model=model, prompt="A moving paper boat", references=refs)
    prepared = g.prepare(intent)
    assert prepared["selection"]["recipe_id"] == f"h3-{partition}-{suffix}"
    components = g.required_components(prepared["selection"]["recipe_id"])
    assert f"h3-{partition}-int8" in components
    assert (f"h3-{partition}-{suffix}" in components) == (suffix != "base")
    doc = g.document(intent, prepared["selection"]["service_id"])
    assert doc["nodes"][0]["parameters"]["num_inference_steps"] == nfe + 1


def test_model_names_and_files_use_the_actual_partition_release(world):
    installed = {c["id"]: c for c in g.components.listed()}
    four = g.variant_metadata("h3", installed)["variants"]
    assert "4step v1.2 768p" in four["fl2va"]["name"]
    assert "4step v0.1" in four["ref2va"]["name"]
    eight = g.variant_metadata("h3-turbo8", installed)["variants"]
    for variant in eight.values():
        assert "8step v1.0 768p" in variant["name"]
        assert "_8step_v1.0_768p_bf16.safetensors" in variant["artifacts"][1]["filename"]


def test_original_weights_remain_selectable_without_int8_or_a_forced_step_count(world, monkeypatch):
    from onecat.creative import presets

    service = {
        "id": "original",
        "name": "MiniMax-H3 original weights",
        "kind": "h3-local",
        "partition": "fl2va",
        "model": "/models/original-h3",
        "transformer_path": "",
        "lora_path": "",
        "runtime_id": "runtime",
        "gpu_uuids": [f"GPU-{i}" for i in range(4)],
    }
    db.put("creative_services", "original", service)
    monkeypatch.setattr(services, "local_paths", lambda value: None)
    assert presets.model_id(service) is None
    model = next(m for m in g.catalog() if m["id"] == "native:original")
    assert "INT8" not in model["name"]
    assert model["variants"]["fl2va"]["denoise_steps"] is None
    intent = g.Intent(model="native:original", prompt="A scene")
    prepared = g.prepare(intent)
    assert prepared["missing_components"] == []
    assert prepared["selection"]["service_id"] == "original"
    assert g.document(intent, "original")["nodes"][0]["parameters"]["num_inference_steps"] is None
    with pytest.raises(ValidationError, match="different input workflow"):
        g.Intent(
            model="native:original",
            prompt="A scene",
            references=[g.Reference(asset_id="a" * 32, role="reference")],
        )
    db.patch("creative_services", "original", {"lora_path": "/models/another-adapter.safetensors"})
    with pytest.raises(ValueError, match="configuration changed"):
        g.validate_switch(prepared["selection"], prepared["impacts"])
    db.patch(
        "creative_services",
        "original",
        {
            "lora_path": "/models/minimax_h3_fl2v_turbo_8step_v1.0_bf16.safetensors",
        },
    )
    lower = g.Intent(model="native:original", prompt="A scene", width=960, height=544)
    assert g.prepare(lower)["selection"]["service_id"] == "original"
    assert [960, 544] in g.model_definition("native:original")["sizes"]


def test_workbench_never_reuses_a_different_lora_or_overwrites_user_preset(world, monkeypatch):
    selected = g.selection(g.Intent(prompt="A cat"))
    service = {
        "id": selected["service_id"],
        "name": "My custom recipe",
        "kind": "h3-local",
        "partition": "fl2va",
        "model": "/models/h3-shared",
        "runtime_id": "runtime",
        "gpu_uuids": [f"GPU-{i}" for i in range(4)],
        "transformer_path": "/models/h3-fl2va-int8",
        "lora_path": "/models/custom-lora",
    }
    db.put("creative_services", service["id"], service)
    db.put("creative_instances", service["id"], {"state": "ready"})
    monkeypatch.setattr(
        g.components,
        "listed",
        lambda: [
            {"id": identity, "installed": {"path": "/models/" + identity}}
            for identity in ("h3-shared", "h3-fl2va-int8", "h3-fl2va-turbo4")
        ],
    )
    chosen = g.selection(g.Intent(prompt="A cat"))
    assert chosen["service_id"] != service["id"]
    assert db.get("creative_services", service["id"]) == service
    service["lora_path"] = "/models/h3-fl2va-turbo4"
    db.put("creative_services", service["id"], service)
    assert g.selection(g.Intent(prompt="A cat"))["service_id"] == service["id"]


def test_keyframes_are_ordered_by_role_and_validated_before_download(world, tmp_path):
    first, last = media(tmp_path), media(tmp_path)
    intent = g.Intent(
        prompt="A bird",
        references=[
            g.Reference(asset_id=last["id"], role="last"),
            g.Reference(asset_id=first["id"], role="first"),
        ],
    )
    assert g.workflow(intent) == "h3-frames"
    doc = g.document(intent, "s")
    assert [n["asset_id"] for n in doc["nodes"][1:]] == [first["id"], last["id"]]
    assert g.prepare(intent)["selection"]["recipe_id"] == "h3-fl2va-turbo4"
    audio = media(tmp_path, "audio")
    with pytest.raises(ValueError, match="images"):
        g.prepare(
            g.Intent(prompt="A bird", references=[g.Reference(asset_id=audio["id"], role="first")])
        )
    with pytest.raises(ValueError, match="at least"):
        g.prepare(
            g.Intent(
                prompt="A bird", references=[g.Reference(asset_id=audio["id"], role="reference")]
            )
        )


def test_refs_switch_partition_but_image_edit_and_mixed_roles_rejected(world, tmp_path):
    asset = media(tmp_path)
    reference = g.Reference(asset_id=asset["id"], role="reference")
    assert (
        g.prepare(g.Intent(prompt="Bird", references=[reference]))["selection"]["recipe_id"]
        == "h3-ref2va-turbo4"
    )
    with pytest.raises(ValidationError):
        g.Intent(
            model="z-image-turbo", prompt="cat", width=1024, height=1024, references=[reference]
        )
    with pytest.raises(ValidationError):
        g.Intent(
            prompt="cat", references=[reference, g.Reference(asset_id=asset["id"], role="first")]
        )


def test_confirmation_is_bound_to_running_instance_and_idempotency(world, monkeypatch):
    effect = {"kind": "chat", "id": "chat", "name": "Chat model", "instance": 1}
    monkeypatch.setattr(g, "impacts", lambda chosen: [effect.copy()])
    prepared = g.prepare(g.Intent(prompt="A bird"))
    payload = g.Submit(preparation_id=prepared["id"], request_key="a" * 32)
    with pytest.raises(HTTPException, match="Confirm"):
        g.submit(payload)
    assert not world[1]
    confirmed = payload.model_copy(update={"confirm_switch": True})
    first = g.submit(confirmed)
    assert g.submit(confirmed)["id"] == first["id"]
    assert len(world[1]) == 1
    another = g.prepare(g.Intent(prompt="Another bird"))
    with pytest.raises(HTTPException, match="different"):
        g.submit(confirmed.model_copy(update={"preparation_id": another["id"]}))
    effect["instance"] = 2
    with pytest.raises(ValueError, match="changed"):
        g.submit(g.Submit(preparation_id=another["id"], request_key="b" * 32, confirm_switch=True))
    assert len(world[1]) == 1


def test_expired_preparation_and_missing_asset_never_start_worker(world):
    prepared = g.prepare(g.Intent(prompt="Bird"))
    db.patch("creative_preparations", prepared["id"], {"expires_at": 0})
    with pytest.raises(HTTPException, match="expired"):
        g.submit(g.Submit(preparation_id=prepared["id"], request_key="c" * 32))
    with pytest.raises(HTTPException):
        g.prepare(
            g.Intent(prompt="Bird", references=[g.Reference(asset_id="d" * 32, role="first")])
        )
    assert not world[1]


def test_external_gpu_work_keeps_task_waiting_and_cancel_never_stops_it(world, monkeypatch):
    devices, spawned = world
    prepared = g.prepare(g.Intent(prompt="Bird"))
    run = g.submit(g.Submit(preparation_id=prepared["id"], request_key="e" * 32))
    service = {
        "id": run["service_id"],
        "kind": "h3-local",
        "name": "H3",
        "model": "local",
        "partition": "fl2va",
        "gpu_uuids": [d["uuid"] for d in devices],
    }
    db.put("creative_services", service["id"], service)
    devices[0]["processes"] = [{"pid": 12345}]
    monkeypatch.setattr(
        g.components,
        "listed",
        lambda: [
            {"id": i, "installed": {"path": "local"}}
            for i in g.required_components(prepared["selection"]["recipe_id"])
        ],
    )
    monkeypatch.setattr(services, "pending_lifecycle", lambda *a: False)
    monkeypatch.setattr(
        g.time, "sleep", lambda *a: db.patch("jobs", run["job_id"], {"cancel_requested": True})
    )
    with pytest.raises(jobs.Cancelled):
        g.execute(jobs.Job(run["job_id"]))
    value = db.get("creative_runs", run["id"])
    assert value["state"] == "cancelled"
    assert value["external_processes"] == [{"gpu": 0, "pid": 12345}]
    assert [j["kind"] for j in spawned] == ["creative_prepare_generate"]


def test_native_image_tracks_original_job_and_only_saves_validated_asset(state, monkeypatch):
    identity = db.uid()
    service = {"kind": "image-local", "checkpoint": "z-image-turbo"}
    record = {
        "id": identity,
        "state": "running",
        "stage": "submitting",
        "created_at": time.time(),
        "upstream_id": "image_existing",
        "snapshot": {"service": service},
    }
    db.put("creative_runs", identity, record)
    jid = db.uid()
    db.put("jobs", jid, {"id": jid, "payload": {}, "kind": "creative_generate"})
    body = io.BytesIO()
    Image.new("RGB", (256, 256), "orange").save(body, "PNG")
    statuses = iter(
        [
            {
                "status": "in_progress",
                "stage": "denoising",
                "denoise_progress": {"completed": 1, "total": 8},
            },
            {"status": "completed", "result": {"width": 256, "height": 256}},
        ]
    )

    def transport(request):
        assert request.method == "GET"
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=body.getvalue())
        return httpx.Response(200, json=next(statuses))

    monkeypatch.setattr(runs.time, "sleep", lambda *a: None)
    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(transport)
    ) as client:
        output = runs.native_image(jobs.Job(jid), record, client, runs.Meter([]))
    assert output[0]["kind"] == "image" and output[0]["width"] == 256
    assert db.get("creative_runs", identity)["native_result"]["width"] == 256
    assert db.get("creative_runs", identity)["state"] != "completed", (
        "Completion must wait for final result storage"
    )


def test_missing_native_job_is_interrupted_without_duplicate_submit(state):
    record = {"id": db.uid(), "state": "running", "snapshot": {"service": {"kind": "image-local"}}}
    db.put("creative_runs", record["id"], record)
    jid = db.uid()
    db.put("jobs", jid, {"id": jid, "payload": {}, "kind": "creative_generate"})
    with (
        httpx.Client(
            base_url="http://localhost",
            transport=httpx.MockTransport(lambda request: httpx.Response(404)),
        ) as client,
        pytest.raises(ValueError, match="Native task is missing"),
    ):
        runs.track_native(
            jobs.Job(jid),
            record,
            client,
            runs.Meter([]),
            "/v1/images/jobs",
            "image_lost",
            "image/png",
            "image.png",
        )
    assert db.get("creative_runs", record["id"])["upstream_lost"]


def test_load_progress_uses_component_count_not_log_line_count():
    progress = services.load_progress(
        'Loading 400 lines\nONECAT_MEDIA_PROGRESS {"stage":"loading_weights","component":"text_encoder","completed":1,"total":3}',
        {},
    )
    assert progress["phase_progress"] == {"done": 1, "total": 3, "percent": 100 / 3}
    assert progress["phase"] == "loading_weights"


@pytest.mark.parametrize("model,suffix", [("h3", "turbo4-544p"), ("h3-turbo8", "turbo8-544p")])
def test_resolution_binds_matching_adapter_before_download(world, model, suffix):
    from onecat.creative.output_sizes import H3_SMALL_SIZES

    for width, height in H3_SMALL_SIZES:
        intent = g.Intent(model=model, prompt="A paper boat", width=width, height=height)
        prepared = g.prepare(intent)
        assert prepared["selection"]["recipe_id"] == f"h3-fl2va-{suffix}"
        assert f"h3-fl2va-{suffix}" in prepared["missing_components"]
        assert g.document(intent, "service")["nodes"][0]["parameters"]["width"] == width
    installed = {c["id"]: c for c in g.components.listed()}
    metadata = g.variant_metadata(model, installed)["variants"]["fl2va"]["resolutions"]["544"]
    assert "_768p" not in metadata["artifacts"][1]["filename"]
    assert metadata["download_bytes"] > 0


def test_reference_resolution_rejects_missing_eight_step_recipe(world, tmp_path):
    refs = [g.Reference(asset_id=media(tmp_path)["id"], role="reference")]
    four = g.Intent(prompt="A reference scene", width=960, height=544, references=refs)
    assert g.prepare(four)["selection"]["recipe_id"] == "h3-ref2va-turbo4"
    with pytest.raises(ValidationError, match="output size"):
        g.Intent(
            model="h3-turbo8", prompt="A reference scene", width=960, height=544, references=refs
        )


def test_resolution_rejects_wrong_loaded_adapter_and_unsupported_dimensions(world):
    small = g.Intent(prompt="A scene", width=960, height=544)
    service = {
        "kind": "h3-local",
        "partition": "fl2va",
        "lora_path": "minimax_h3_fl2v_turbo_4step_v1.2_768p_bf16.safetensors",
    }
    with pytest.raises(ValueError, match="resolution supported"):
        runs.validate_request(g.document(small, "service"), "generation", service)
    for width, height in [(1920, 1080), (961, 544), (544, 9999)]:
        with pytest.raises(ValidationError, match="output size"):
            g.Intent(prompt="A scene", width=width, height=height)


def test_images_accept_both_resolution_tiers_without_changing_recipe(world):
    from onecat.creative.output_sizes import IMAGE_SIZES

    for width, height in IMAGE_SIZES:
        for model in ("z-image-turbo", "z-image"):
            intent = g.Intent(model=model, prompt="A cat", width=width, height=height)
            assert g.recipe_id(intent) == model
            assert g.document(intent, "service")["nodes"][0]["parameters"]["height"] == height
