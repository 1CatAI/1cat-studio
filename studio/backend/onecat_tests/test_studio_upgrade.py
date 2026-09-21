import io
import json
import os
import time

import httpx
import psutil
import pytest
from onecat import attachments, catalog, db, engine, gpu, lifecycle
from onecat.migrations import upgrade
from onecat.schemas import Profile
from PIL import Image


def test_ready_requires_health_and_matching_model():
    state = {"port": 1234, "profile": {"served_model_name": "wanted"}}
    for health, name, expected in [
        (200, "old", False),
        (503, "wanted", False),
        (200, "wanted", True),
    ]:
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    health if r.url.path == "/health" else 200, json={"data": [{"id": name}]}
                )
            )
        ) as client:
            assert lifecycle.healthy(state, client) is expected


def test_real_counts_and_silent_compile_do_not_regress():
    value = lifecycle.log_progress(
        "Loading safetensors checkpoint shards: 25%|xx| 3/12 [01:00<03:00]\rLoading safetensors checkpoint shards: 50%|xx| 6/12 [02:00<02:00]"
    )
    assert value["phase_progress"] == {"done": 6, "total": 12, "percent": 50}
    value = lifecycle.log_progress("torch.compile compiling graph", value)
    assert value["phase"] == "compiling" and value["phase_progress"] is None
    assert lifecycle.log_progress("unrelated heartbeat", value) == value
    value = lifecycle.log_progress(
        "Capturing CUDA graphs (mixed prefill-decode): 50%|x| 1/2 [01:00<01:00]", value
    )
    assert value["phase"] == "capturing_graphs" and value["phase_progress"]["total"] == 2


def test_orphaned_launch_recovers_without_spawning_second_engine(monkeypatch):
    pid = os.getpid()
    state = {
        "state": "loading",
        "phase": "compiling",
        "pid": pid,
        "process_created": psutil.Process(pid).create_time(),
        "profile_id": "p",
        "launch_id": "j",
        "job_id": "j",
        "started_at": time.time() - 400,
    }
    db.put("engine", "active", state)
    db.put("jobs", "j", {"id": "j", "state": "failed", "pid": 99999999, "process_created": 1})
    monkeypatch.setattr(lifecycle, "healthy", lambda data: False)
    lifecycle.reconcile_owned()
    assert engine.status()["state"] == "loading"
    monkeypatch.setattr(lifecycle, "healthy", lambda data: True)
    lifecycle.reconcile_owned()
    assert engine.status()["state"] == "ready" and db.get("jobs", "j")["state"] == "completed"
    newer = {**state, "launch_id": "new"}
    db.put("engine", "active", newer)
    assert lifecycle.patch_instance(state, {"state": "ready"}) is None
    assert engine.private_state()["state"] == "loading"


def test_output_migration_backs_up_and_preserves_custom_values(state):
    for id, limit in [("legacy", 1024), ("custom", 4096)]:
        db.put(
            "profiles", id, {"id": id, "default_sampling": {"max_tokens": limit}, "extra_args": []}
        )
    upgrade()
    migration = db.get("migrations", "studio-v2")
    assert db.get("profiles", "legacy")["default_sampling"]["max_tokens"] is None
    assert db.get("profiles", "custom")["default_sampling"]["max_tokens"] == 4096
    from pathlib import Path

    assert (Path(migration["backup"]) / "onecat.db").is_file()
    upgrade()
    assert db.get("migrations", "studio-v2") == migration
    p = Profile(name="test", model_path="/model", runtime_id="r")
    assert p.default_sampling["max_tokens"] is None
    assert (
        Profile(**{**p.model_dump(), "default_sampling": {"max_tokens": 262144}}).default_sampling[
            "max_tokens"
        ]
        == 262144
    )


def test_qwen_catalog_context_migration_is_one_time_and_preserves_custom_profiles(state):
    common = {
        "max_model_len": 32768,
        "default_sampling": {"max_tokens": None},
        "extra_args": [],
    }
    db.put(
        "profiles",
        "catalog",
        {
            "id": "catalog",
            "source": "catalog-default",
            "catalog_id": "QuantTrio/Qwen3.6-35B-A3B-AWQ",
            **common,
        },
    )
    db.put(
        "profiles",
        "custom",
        {"id": "custom", "source": "custom", "catalog_id": None, **common},
    )
    upgrade()
    assert db.get("profiles", "catalog")["max_model_len"] == 262144
    assert db.get("profiles", "custom")["max_model_len"] == 32768
    db.patch("profiles", "catalog", {"max_model_len": 32768})
    upgrade()
    assert db.get("profiles", "catalog")["max_model_len"] == 32768
    migration = db.get("migrations", "qwen-catalog-context-v1")
    assert migration["updated_profiles"] == ["catalog"]


def test_bf16_save_and_launch_validation_share_hardware_gate(client, monkeypatch):
    uuid = "GPU-" + "a" * 36
    monkeypatch.setattr(gpu, "selected_devices", lambda ids: [{"compute_capability": [7, 0]}])
    db.put("runtimes", "r", {"id": "r", "validated": True})
    payload = Profile(
        name="test", model_path="/model", runtime_id="r", gpu_uuids=[uuid], dtype="bfloat16"
    ).model_dump()
    response = client.post("/api/profiles", json=payload)
    assert response.status_code == 400 and "BF16" in response.text
    with pytest.raises(ValueError, match="BF16"):
        catalog.validate_features(payload)
    payload["dtype"] = "auto"
    catalog.validate_features(payload)
    assert payload["dtype"] == "half"


def test_catalog_download_does_not_require_launch_compatibility(client, monkeypatch):
    import importlib

    app_module = importlib.import_module("onecat.app")
    monkeypatch.setattr(
        app_module,
        "create_job",
        lambda kind, payload: {"id": "download", "kind": kind, "payload": payload},
    )
    monkeypatch.setattr(gpu, "snapshot", lambda: {"gpus": []})
    for repo, status in [("random/GGUF", 400), ("QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4", 200)]:
        response = client.post("/api/hub/download", json={"repo_id": repo})
        assert response.status_code == status
    all_items = client.get("/api/models/search?all_verified=true").json()
    assert all_items["items"] and not all_items["items"][0]["compatible"]
    assert client.get("/api/models/search").json()["items"] == []
    assert {e["family"] for e in all_items["audit"]} >= {"Qwen3.6", "Qwen3.8", "GLM", "DeepSeek"}


def test_supported_inventory_is_visible_without_claiming_download_compatibility(
    client, monkeypatch
):
    monkeypatch.setattr(gpu, "snapshot", lambda: {"gpus": []})
    inventory = client.get("/api/models/supported").json()
    assert len(inventory["items"]) >= 16
    assert {item["family"] for item in inventory["items"]} >= {
        "Qwen3.5",
        "Qwen3.6",
        "Qwen3.8",
        "GLM",
        "DeepSeek",
    }
    assert sum(item["downloadable"] for item in inventory["items"]) == 8
    assert not any(item["compatible"] for item in inventory["items"])
    assert all(
        item["evidence"] and item["runtime"] and item["hardware"] for item in inventory["items"]
    )
    assert len({item["id"] for item in inventory["items"]}) == len(inventory["items"])
    for checkpoint in catalog.directory()["entries"]:
        assert checkpoint["files"]
        assert any(f["path"].endswith(".safetensors") for f in checkpoint["files"])
        assert not any(f["path"].endswith(".gguf") for f in checkpoint["files"])
        assert all(len(f["sha256"]) == 64 and f["bytes"] >= 0 for f in checkpoint["files"])
    response = client.post("/api/hub/download", json={"repo_id": "qwen38-flash-next-awq"})
    assert response.status_code == 400


def image_bytes():
    out = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(out, format="PNG")
    return out.getvalue()


def test_images_authenticated_persisted_exported_and_replayed(client):
    image = client.post(
        "/api/chat/attachments", files={"file": ("red.png", image_bytes(), "image/png")}
    ).json()
    assert image["width"] == 64
    part = {"type": "image", "attachment_id": image["id"]}
    thread = client.post("/api/chat/threads", json={"title": "Image"}).json()
    message = {
        "id": "m",
        "role": "user",
        "content": [{"type": "text", "text": "color?"}, part],
        "createdAt": 1,
    }
    assert (
        client.put(
            f"/api/chat/threads/{thread['id']}/messages", json={"messages": [message]}
        ).status_code
        == 200
    )
    exported = client.get(f"/api/chat/threads/{thread['id']}/export").json()
    assert exported["attachments"][image["id"]]["data_url"].startswith("data:image/png;base64,")
    imported = client.post("/api/chat/import", json=exported).json()
    restored = client.get(f"/api/chat/threads/{imported['id']}").json()["messages"]
    assert restored[0]["content"][1]["attachment_id"] != image["id"]
    body = attachments.prepare_messages(restored, {"vision_enabled": True, "max_images": 4})
    assert (
        body[0]["content"][1]["image_url"]["url"]
        == exported["attachments"][image["id"]]["data_url"]
    )
    with pytest.raises(ValueError, match="understanding"):
        attachments.prepare_messages(restored, {"vision_enabled": False})
    with pytest.raises(ValueError, match="four"):
        attachments.validate_content([part] * 5)
    client.post("/api/auth/logout")
    assert client.get("/api/chat/attachments/" + image["id"]).status_code == 401


def test_invalid_images_and_path_traversal_are_rejected(client):
    response = client.post(
        "/api/chat/attachments",
        files={"file": ("fake.png", b'<svg onload="alert(1)"/>', "image/png")},
    )
    assert response.status_code == 400
    with pytest.raises(ValueError):
        attachments.read("../../onecat.db")
    with pytest.raises(ValueError, match="10 MiB"):
        attachments.store(b"x" * (attachments.MAX_BYTES + 1))


def test_checking_phase_is_visible_before_process_spawn():
    db.put("engine", "active", {"state": "stopped"})
    db.put("profiles", "next", {"id": "next", "name": "Next model"})
    db.put(
        "jobs",
        "new",
        {
            "id": "new",
            "kind": "start_model",
            "state": "running",
            "created_at": time.time() - 5,
            "payload": {"profile_id": "next"},
        },
    )
    db.put("engine", "maintenance", {"job_id": "new", "kind": "start_model"})
    status = engine.status()
    assert (
        status["phase"] == "checking" and status["actions"]["cancel"] and status["elapsed_s"] >= 5
    )
    assert engine.private_state()["state"] == "stopped"


def test_late_ready_probe_does_not_resurrect_stopping_instance():
    old = {"state": "ready", "pid": 123, "launch_id": "l", "profile_id": "p", "process_created": 1}
    db.put("engine", "active", {**old, "state": "stopping"})
    assert lifecycle.patch_instance(old, {"state": "ready"}) is None
    assert engine.private_state()["state"] == "stopping"


def test_omitting_catalog_id_still_adopts_the_verified_recipe(monkeypatch):
    """The checkpoint identity stays enforced; only its topology is advisory."""
    model = {
        "id": "m",
        "path": "/downloaded-model",
        "catalog_id": "QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4",
    }
    db.put("models", "m", model)
    db.put("runtimes", "r", {"id": "r", "validated": True, "release": {"version": "1.5.0"}})
    uuid = "GPU-" + "b" * 36
    monkeypatch.setattr(
        gpu,
        "selected_devices",
        lambda ids: [{"compute_capability": [7, 0], "memory_total_mib": 32768}],
    )
    monkeypatch.setattr(catalog, "model_entry", lambda path: catalog.entry(model["catalog_id"]))
    payload = Profile(
        name="smaller machine", model_path=model["path"], runtime_id="r", gpu_uuids=[uuid]
    ).model_dump()
    profile = catalog.validate_features(payload)
    assert profile["catalog_id"] == model["catalog_id"]
    item = catalog.entry(model["catalog_id"])
    board = {
        "uuid": uuid,
        "index": 0,
        "compute_capability": [7, 0],
        "memory_total_mib": 32768,
    }
    assert catalog.compatibility(item, "r", [board]) == []
    notes = catalog.recommendations(item, [board])
    assert any("Verified with 4 GPUs" in note for note in notes)
    assert any("TP4" in note for note in notes)
    # With no accelerator at all the profile is genuinely unlaunchable.
    assert catalog.compatibility(item, "r", [])


def test_initial_config_dump_is_not_compile_progress():
    progress = lifecycle.log_progress(
        "Initializing a V1 engine with compilation_config={'backend':'inductor', 'torch.compile':True}"
    )
    assert progress["phase"] == "loading_weights" and not progress["detail"]


def test_completed_metrics_backfill_and_survive_a_late_client_save(client):
    from onecat.chat_metrics import commit

    thread = client.post("/api/chat/threads", json={"title": "Timing"}).json()
    message = {
        "id": "answer",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello"}],
        "createdAt": 1,
        "metadata": {"request_id": "request", "timing": {"pending": True}},
    }
    client.put(f"/api/chat/threads/{thread['id']}/messages", json={"messages": [message]})
    final = {
        "pending": False,
        "decode_tokens_s": 50,
        "decode_source": "isolated_engine_metrics",
        "chat_message_id": "answer",
        "chat_thread_id": thread["id"],
    }
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO requests(id,started,status,prompt_tokens,completion_tokens,metrics) VALUES(?,0,200,20,10,?)",
            ("request", json.dumps(final)),
        )
    commit("request", final)
    # An older SSE result arriving after enrichment must not downgrade the record.
    client.put(f"/api/chat/threads/{thread['id']}/messages", json={"messages": [message]})
    exported = client.get(f"/api/chat/threads/{thread['id']}/export").json()
    metadata = exported["messages"][0]["metadata"]
    assert metadata["timing"] == final and metadata["usage"]["completion_tokens"] == 10
    from onecat import chat_store as studio_db

    assert studio_db.list_chat_messages(thread["id"])[0]["metadata"]["timing"] == final


def test_runtime_configuration_and_http_wait_do_not_advance_load_stage():
    text = "INFO [vllm.py:2961] Extending SM70 compile range endpoint for CUDA graph capture.\nWaiting for application startup."
    progress = lifecycle.log_progress(text)
    assert progress["phase"] == "loading_weights"
    assert progress["phase_progress"] is None
    progress = lifecycle.log_progress("Loading checkpoint shards: 50%|### | 2/4 [00:01<00:01]", progress)
    assert progress["phase_progress"]["done"] == 2
