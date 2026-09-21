# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import asyncio
import copy
import hashlib
import importlib
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from onecat import catalog, db, gpu, models
from onecat.jobs import Job
from onecat.schemas import Profile


@pytest.fixture
def checkpoint(monkeypatch):
    item = copy.deepcopy(catalog.directory()["entries"][0])
    contents = {
        "config.json": json.dumps(
            {"model_type": "qwen3_5", "architectures": [item["architecture"]]}
        ).encode(),
        "model.safetensors": b"CPU-only download fixture",
    }
    item["files"] = [
        {"path": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        for name, body in contents.items()
    ]
    support = copy.deepcopy(catalog.directory()["support"][0])
    monkeypatch.setattr(
        catalog,
        "directory",
        lambda: {"entries": [item], "support": [support], "version": 2, "audited_at": "test"},
    )
    devices = [
        {
            "uuid": "GPU-" + str(i) * 36,
            "index": i,
            "compute_capability": [7, 0],
            "memory_total_mib": 32768,
        }
        for i in range(4)
    ]
    devices.append(
        {
            "uuid": "GPU-" + "a" * 36,
            "index": 4,
            "compute_capability": [6, 1],
            "memory_total_mib": 2048,
        }
    )
    monkeypatch.setattr(gpu, "snapshot", lambda: {"gpus": devices})
    monkeypatch.setattr(
        gpu, "selected_devices", lambda ids: [d for d in devices if d["uuid"] in ids]
    )

    class Hub:
        def list_repo_files(self, *args, **kwargs):
            return [
                SimpleNamespace(path=f["path"], size=f["bytes"], sha256=f["sha256"], is_dir=False)
                for f in item["files"]
            ]

        def download_repo(self, *args, **kwargs):
            folder = kwargs["local_dir"]
            folder.mkdir(parents=True, exist_ok=True)
            for name, body in contents.items():
                (folder / name).write_bytes(body)

    monkeypatch.setattr(models, "hub", lambda: Hub())
    return item


def installed(version="1.5.0", id="r"):
    runtime = {
        "id": id,
        "name": "Test runtime " + version,
        "release": {"version": version},
        "validated": True,
    }
    db.put("runtimes", id, runtime)
    return runtime


def test_unsloth_recipe_preserves_checkpoint_identity_and_capabilities(monkeypatch):
    unsloth = catalog.entry("unsloth/Qwen3.8-27B-NVFP4")
    qat = catalog.entry("QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4")
    assert unsloth["recommended"] == qat["recommended"]
    config = next(f for f in unsloth["files"] if f["path"] == "config.json")
    assert config["sha256"] == "1b3c71868d1299e52df6fc907deb202d5132b1ef0f72aae0ef6d15185dd53a5c"
    assert config not in qat["files"]
    monkeypatch.setattr(catalog, "model_entry", lambda path: unsloth)
    monkeypatch.setattr(catalog, "runtime_matches", lambda *_: True)
    capabilities = catalog.capabilities("/unsloth", "runtime")
    assert capabilities["tool_parser"] == "qwen3_coder"
    assert capabilities["accelerators"] == ["dflash"]
    # QAT's image-input acceptance does not carry over to another publisher.
    assert not capabilities["vision"]


def test_qwen_catalog_defaults_expose_256k_tools_vision_and_mtp_choices(monkeypatch):
    targets = [
        item
        for item in catalog.directory()["entries"]
        if item["id"].startswith(("QuantTrio/Qwen3.6-", "Qwen/Qwen3.6-", "Qwen/Qwen3.8-"))
    ]
    assert targets
    for item in targets:
        assert item["recommended"]["max_model_len"] == 262144
        assert item["tool_parser"] == "qwen3_coder"
        assert item["vision"] and item["max_images"] == 1

    monkeypatch.setattr(
        catalog,
        "capabilities",
        lambda *_: {
            "accelerators": ["mtp"],
            "mtp_token_options": [1, 2, 3, 4],
            "tool_parser": None,
            "vision": False,
        },
    )
    base = Profile(name="MTP", model_path="/model", runtime_id="r").model_dump()
    for count in (1, 2, 3, 4):
        profile = {**base, "speculative_config": {"method": "mtp", "num_speculative_tokens": count}}
        assert (
            catalog.validate_features(profile)["speculative_config"]["num_speculative_tokens"]
            == count
        )
    for count in (True, 0, 5):
        profile = {**base, "speculative_config": {"method": "mtp", "num_speculative_tokens": count}}
        with pytest.raises(ValueError, match="1, 2, 3 or 4"):
            catalog.validate_features(profile)


def download(item):
    id = db.uid()
    db.put(
        "jobs",
        id,
        {
            "id": id,
            "kind": "download_model",
            "state": "running",
            "payload": {"repo_id": item["id"]},
        },
    )
    result = models.download(Job(id))
    Job(id).finish(result)
    return result


def test_download_without_runtime_then_create_default_profile(checkpoint):
    before = catalog.launch_defaults(checkpoint["id"])
    assert before["profile"]["dtype"] == "half"
    assert before["profile"]["runtime_id"] == ""
    assert len(before["profile"]["gpu_uuids"]) == 4
    assert not before["can_create"]
    result = download(checkpoint)
    assert "profile_pending_reason" in result and "profile_id" not in result
    assert db.get("models", result["model_id"])["verified"]
    assert not db.all_records("profiles")
    installed()
    created = catalog.backfill_default_profiles()
    assert len(created) == 1
    profile = db.get("profiles", created[0])
    assert profile["runtime_id"] == "r"
    assert profile["tensor_parallel_size"] == 4
    assert profile["gpu_uuids"] == ["GPU-" + str(i) * 36 for i in range(4)]
    assert profile["max_model_len"] == checkpoint["recommended"]["max_model_len"]
    assert profile["kv_cache_dtype"] == "fp8_e5m2"
    assert profile["default_sampling"]["max_tokens"] is None
    assert profile["speculative_config"] is None
    assert Profile.model_validate(profile)
    catalog.validate_features(profile)


def test_known_source_overlay_inherits_its_validated_base_version():
    item = {"runtime_versions": ["1.3.0"], "runtime_snapshots": []}
    runtime = {
        "validated": True,
        "capabilities": {
            "vllm_version": "dev",
            "distribution_version": "1.3.0",
            "source_snapshot": "pr417-a09e332bcd",
        },
    }
    assert catalog.runtime_matches(item, runtime)

    runtime["capabilities"]["source_snapshot"] = "unrecognized-local-tree"
    assert not catalog.runtime_matches(item, runtime)


def test_download_creates_default_without_overwriting_user_edits(checkpoint):
    installed()
    result = download(checkpoint)
    profile = db.get("profiles", result["profile_id"])
    profile["max_num_seqs"] = 2
    db.put("profiles", profile["id"], profile)
    again = download(checkpoint)
    assert again["profile_id"] == profile["id"]
    assert len(db.all_records("profiles")) == 1
    assert db.get("profiles", profile["id"])["max_num_seqs"] == 2
    visible = catalog.supported()["items"][0]
    assert visible["model_id"] == result["model_id"]
    assert visible["job"]["state"] == "completed"
    assert visible["compatible"]


def test_launch_gate_remains_strict_after_download(checkpoint, monkeypatch):
    installed("dev", "unrelated")
    result = download(checkpoint)
    assert catalog.launch_defaults(checkpoint["id"])["profile"]["runtime_id"] == ""
    with pytest.raises(ValueError, match="Requires runtime"):
        catalog.create_default_profile(result["model_id"])
    installed()
    profile = catalog.create_default_profile(result["model_id"])
    profile["runtime_id"] = "unrelated"
    with pytest.raises(ValueError, match="Requires runtime"):
        catalog.validate_features(profile)
    # A verified topology is only a recommendation, but a machine with no GPU
    # at all still cannot host a launch profile.
    monkeypatch.setattr(gpu, "snapshot", lambda: {"gpus": []})
    assert catalog.require_download(checkpoint["id"])
    with pytest.raises(ValueError, match="No NVIDIA GPU"):
        catalog.create_default_profile(result["model_id"])


def test_repurposed_default_never_launches_another_model(checkpoint):
    installed()
    result = download(checkpoint)
    original_id = result["profile_id"]
    db.patch("profiles", original_id, {"model_path": "/another-model", "catalog_id": None})
    replacement = catalog.create_default_profile(result["model_id"])
    assert replacement["id"] != original_id
    assert replacement["model_path"] == result["path"]
    assert db.get("profiles", original_id)["model_path"] == "/another-model"
    assert catalog.create_default_profile(result["model_id"])["id"] == replacement["id"]
    assert len(db.all_records("profiles")) == 2


def test_resume_callback_offsets_are_not_counted_as_new_bytes(checkpoint, monkeypatch):
    from pathlib import Path

    manifest = sorted(checkpoint["files"], key=lambda f: f["path"])
    identity = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    folder = Path(db.settings()["model_directory"]) / (
        checkpoint["id"].replace("/", "--") + "--" + identity[:12]
    )
    folder.mkdir(parents=True)
    partial = folder / "model.safetensors.incomplete"
    partial.write_bytes(b"CPU-only")
    original = models.hub()
    observations = []
    ticks = iter([0, 1, 2, 3])
    monkeypatch.setattr(
        models, "time", SimpleNamespace(monotonic=lambda: next(ticks), time=time.time)
    )

    class ResumingHub:
        list_repo_files = original.list_repo_files

        def download_repo(self, *args, **kwargs):
            callback = kwargs["progress_callbacks"][0]("model.safetensors", 24)
            for replay in (8, 8, 3):
                if replay == 3:
                    with partial.open("ab") as file:
                        file.write(b" do")
                callback.update(replay)
                job = db.all_records("jobs")[0]
                observations.append((job["downloaded_bytes"], job["bytes_per_second"]))
            original.download_repo(*args, **kwargs)
            partial.unlink()

    monkeypatch.setattr(models, "hub", ResumingHub)
    download(checkpoint)
    assert observations == [(8, 0), (8, 0), (11, 3)]


def test_retained_bytes_counts_parallel_ranges_once_during_merge(state):
    folder = state / "models"
    manifest = [{"path": "model.safetensors", "bytes": 20}]
    (folder / "model.safetensors_0_9").write_bytes(b"a" * 10)
    (folder / "model.safetensors_10_19").write_bytes(b"b" * 5)
    (folder / "model.safetensors.parallel_tmp").write_bytes(b"a" * 10)
    (folder / "unrelated-cache").write_bytes(b"x" * 100)
    assert models.retained_bytes(folder, manifest) == 15
    (folder / "model.safetensors").write_bytes(b"a" * 20)
    assert models.retained_bytes(folder, manifest) == 20


def test_wrong_revision_gguf_and_modified_weights_are_rejected(checkpoint):
    with pytest.raises(ValueError, match="verified ModelScope"):
        catalog.require_download("random/GGUF")
    with pytest.raises(ValueError, match="Revision"):
        catalog.require_download(checkpoint["id"], "unverified-revision")
    installed()
    result = download(checkpoint)
    from pathlib import Path

    (Path(result["path"]) / "model.safetensors").write_bytes(b"modified")
    assert not catalog.launch_defaults(checkpoint["id"])["can_create"]
    with pytest.raises(ValueError, match="download"):
        catalog.create_default_profile(result["model_id"])
    checkpoint["files"] = [{"path": "model.gguf"}]
    with pytest.raises(ValueError, match="manifest"):
        catalog.require_download(checkpoint["id"])


def test_download_admission_is_deduplicated_across_runtime_selections(checkpoint, monkeypatch):
    app_module = importlib.import_module("onecat.app")
    created = []

    def create(kind, payload):
        time.sleep(0.02)
        job = {"id": db.uid(), "kind": kind, "state": "queued", "payload": payload}
        created.append(job)
        db.put("jobs", job["id"], job)
        return job

    monkeypatch.setattr(app_module, "create_job", create)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_module.create_app()), base_url="http://testserver"
        ) as client:
            assert (
                await client.post("/api/auth/setup", json={"password": "download-test-password"})
            ).is_success
            results = await asyncio.gather(
                *(
                    client.post(
                        "/api/hub/download",
                        json={"repo_id": checkpoint["id"], "runtime_id": runtime},
                    )
                    for runtime in ["missing", "different"]
                )
            )
            assert all(r.is_success for r in results)
            assert results[0].json()["id"] == results[1].json()["id"]
            assert len(created) == 1 and "runtime_id" not in created[0]["payload"]
            assert (
                await client.post("/api/hub/download", json={"repo_id": "random/GGUF"})
            ).status_code == 400

    asyncio.run(run())
