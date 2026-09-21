# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import pytest
from onecat import db
from onecat.creative import presets, services


def test_download_and_verification_are_shared_across_concurrent_callers(state, monkeypatch):
    import time
    from concurrent.futures import ThreadPoolExecutor

    from onecat import jobs
    from onecat.creative import components

    monkeypatch.setattr(components, "catalog", lambda: [{"id": "original-h3"}])
    created = []

    def create(kind, payload):
        time.sleep(0.02)
        record = {"id": db.uid(), "kind": kind, "component_id": payload["component_id"],
                  "state": "running", "stage": "checking_model", "created_at": time.time()}
        db.put("jobs", record["id"], record)
        created.append(record)
        return record

    monkeypatch.setattr(jobs, "create_job", create)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: components.schedule_download("original-h3"), range(4)))
    assert len(created) == 1
    assert {value[0]["id"] for value in values} == {created[0]["id"]}
    assert sum(value[1] for value in values) == 1
    db.patch("jobs", created[0]["id"], {"state": "failed"})
    replacement, owned = components.schedule_download("original-h3")
    assert owned and replacement["id"] != created[0]["id"]


def test_component_repair_reuses_verified_folder_and_reports_only_missing_bytes(
    state, tmp_path, monkeypatch
):
    import hashlib
    import json

    from onecat.creative import components

    folder = tmp_path / "custom-models"
    folder.mkdir()
    (folder / "weights.bin").write_bytes(b"weights")
    manifest = [
        {"path": "weights.bin", "bytes": 7, "sha256": hashlib.sha256(b"weights").hexdigest()},
        {"path": "config.json", "bytes": 2, "sha256": hashlib.sha256(b"{}").hexdigest()},
    ]
    model = {"id": "image", "role": "base", "bytes": 9, "files": manifest}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    db.put(
        "creative_components",
        "image",
        {"id": "image", "path": str(folder), "manifest_sha256": digest},
    )
    monkeypatch.setattr(components, "catalog", lambda: [model])
    assert components.destination(model)[0] == folder
    listed = components.listed()[0]
    assert listed["needs_repair"]
    assert listed["download_bytes"] == 2
    assert listed["installed"] is None
    (folder / "config.json").write_text("{}")
    assert components.listed()[0]["download_bytes"] == 0
    assert components.listed()[0]["installed"] is not None


@pytest.fixture
def ready(monkeypatch, tmp_path):
    ids = {i for r in presets.RECIPES for i in presets.required(r)}
    installed = [{"id": i, "installed": {"path": str(tmp_path / i)}} for i in ids]
    monkeypatch.setattr(presets.components, "listed", lambda: installed)
    monkeypatch.setattr(
        presets,
        "candidates",
        lambda: ([{"id": "source-main"}], [{"uuid": f"GPU-{i}", "index": i} for i in range(4)]),
    )
    saved = []

    def save(value):
        record = value.model_dump(exclude={"api_key"})
        saved.append(record)
        db.put("creative_services", record["id"], record)
        return record

    monkeypatch.setattr(services, "save", save)
    monkeypatch.setattr(services, "public", lambda record: record)
    return installed, saved


def test_base_recipe_does_not_silently_enable_turbo(ready):
    record = presets.create("h3-fl2va-base")
    assert record["partition"] == "fl2va"
    assert record["lora_path"] == ""
    assert record["transformer_path"].endswith("h3-fl2va-int8")
    assert len(record["gpu_uuids"]) == 4


def test_reference_recipe_matches_weights_and_adapter(ready):
    record = presets.create("h3-ref2va-turbo4")
    assert record["partition"] == "ref2va"
    assert record["lora_path"].endswith("h3-ref2va-turbo4")
    assert record["transformer_path"].endswith("h3-ref2va-int8")


def test_repeated_creation_preserves_edits(ready):
    record = presets.create("h3-fl2va-base")
    db.patch("creative_services", record["id"], {"name": "User title"})
    repeat = presets.create("h3-fl2va-base", gpu_uuids=["GPU-3", "GPU-2", "GPU-1", "GPU-0"])
    assert repeat["id"] == record["id"]
    assert repeat["name"] == "User title"
    assert len(ready[1]) == 1


@pytest.mark.parametrize("selection", [[], ["GPU-0"] * 4, ["GPU-0", "GPU-1", "GPU-2", "GPU-9"]])
def test_invalid_explicit_gpu_selection_is_rejected(ready, selection):
    with pytest.raises(ValueError, match="four available"):
        presets.create("h3-fl2va-base", gpu_uuids=selection)
    assert not ready[1]


def test_missing_component_and_wrong_runtime_are_rejected(ready):
    with pytest.raises(ValueError, match="runtime"):
        presets.create("h3-fl2va-base", runtime_id="old-wheel")
    ready[0][:] = [c for c in ready[0] if c["id"] != "h3-shared"]
    assert "h3-shared" in presets.listed()[0]["missing_components"]
    with pytest.raises(ValueError, match="ModelScope"):
        presets.create("h3-fl2va-base")
    assert not ready[1]


def test_recipe_filters_display_gpu_and_small_v100(monkeypatch):
    monkeypatch.setattr(db, "all_records", lambda _: [])
    monkeypatch.setattr(
        presets.gpu,
        "snapshot",
        lambda: {
            "gpus": [
                {"name": "Quadro P400", "index": 0, "uuid": "P400", "memory_total_mib": 2048},
                {"name": "V100", "index": 1, "uuid": "V100-16", "memory_total_mib": 16384},
                {"name": "V100", "index": 2, "uuid": "V100-32", "memory_total_mib": 32768},
            ]
        },
    )
    assert [d["uuid"] for d in presets.candidates()[1]] == ["V100-32"]


def test_completed_download_reports_verified_total(monkeypatch, state):
    import hashlib
    from types import SimpleNamespace

    from onecat import jobs
    from onecat.creative import components

    content = b"verified model component"
    entry = {
        "path": "weights.safetensors",
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    row = {
        "id": "test",
        "repo_id": "verified/test",
        "revision": "fixed",
        "role": "transformer",
        "files": [entry],
        "bytes": len(content),
    }
    monkeypatch.setattr(components, "catalog", lambda: [row])

    class Hub:
        def list_repo_files(self, *args, **kwargs):
            return [
                SimpleNamespace(
                    path=entry["path"], size=entry["bytes"], sha256=entry["sha256"], is_dir=False
                )
            ]

        def download_repo(self, *args, **kwargs):
            # A small transfer can finish before any throttled progress callback.
            (kwargs["local_dir"] / entry["path"]).write_bytes(content)

    monkeypatch.setattr(components, "hub", Hub)
    db.put(
        "jobs",
        "download",
        {
            "id": "download",
            "kind": "creative_download",
            "state": "running",
            "payload": {"component_id": "test"},
            "cancel_requested": False,
        },
    )
    components.download(jobs.Job("download"))
    record = db.get("jobs", "download")
    assert record["downloaded_bytes"] == record["expected_bytes"] == len(content)
    assert record["bytes_per_second"] == 0
