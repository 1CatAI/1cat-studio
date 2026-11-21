import hashlib
from types import SimpleNamespace

import pytest
from onecat import catalog, db, models
from onecat.jobs import Job


def test_modelscope_download_verifies_manifest_and_resumes(state, monkeypatch):
    contents = {"config.json": b'{"model_type":"llama"}', "model.safetensors": b"fixture weights"}
    files = [
        SimpleNamespace(path=n, size=len(v), sha256=hashlib.sha256(v).hexdigest(), is_dir=False)
        for n, v in contents.items()
    ]
    calls = []
    monkeypatch.setattr(
        catalog,
        "require_download",
        lambda *args: {
            "id": "onecat/tiny",
            "files": [{"path": f.path, "bytes": f.size, "sha256": f.sha256} for f in files],
        },
    )

    class Hub:
        def list_repo_files(self, repo, type, revision):
            return files

        def download_repo(self, repo, type, **kw):
            calls.append(kw["local_dir"])
            folder = kw["local_dir"]
            folder.mkdir(parents=True, exist_ok=True)
            for n, v in contents.items():
                if not (folder / n).exists():
                    (folder / n).write_bytes(v)

    monkeypatch.setattr(models, "hub", lambda: Hub())
    for id in ["first", "resume"]:
        db.put(
            "jobs",
            id,
            {
                "id": id,
                "kind": "download_model",
                "state": "running",
                "payload": {"repo_id": "onecat/tiny"},
            },
        )
        result = models.download(Job(id))
        assert result["provider"] == "modelscope"
    assert calls[0] == calls[1] and len(db.all_records("models")) == 1
    (calls[0] / "model.safetensors").write_bytes(b"corrupted transfer")
    db.put(
        "jobs",
        "bad",
        {
            "id": "bad",
            "kind": "download_model",
            "state": "running",
            "payload": {"repo_id": "onecat/tiny"},
        },
    )
    with pytest.raises(ValueError, match="checksum"):
        models.download(Job("bad"))


def test_only_modelscope_is_configured(client):
    result = client.get("/api/settings").json()
    assert result["modelscope_endpoint"] == "https://modelscope.cn" and "hf_endpoint" not in result
    assert (
        client.put(
            "/api/settings/modelscope-token", json={"token": "test-private-token"}
        ).status_code
        == 200
    )
    result = client.get("/api/settings").json()
    assert result["modelscope_token_set"] and "test-private-token" not in str(result)


def test_snapshot_reports_bytes_inside_shard_and_checks_digest(tmp_path, monkeypatch):
    contents = b"x" * (10 * 1024 * 1024)
    shard = tmp_path / "shard.safetensors"
    shard.write_bytes(contents)
    manifest = [{"path": shard.name, "bytes": len(contents), "sha256": hashlib.sha256(contents).hexdigest()}]
    updates = []

    class Recorder:
        def update(self, stage, progress, **details):
            updates.append(details["verification"])

        def check_cancelled(self):
            pass

    clock = iter(range(100))
    monkeypatch.setattr(models, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    models.verify_snapshot(Recorder(), tmp_path, manifest)
    assert updates[0]["bytes_read"] == 0
    assert any(0 < row["bytes_read"] < len(contents) and row["current_file"] == shard.name
               and row["files_done"] == 0 for row in updates)
    assert updates[-1] == {"bytes_read": len(contents), "total_bytes": len(contents),
                           "files_done": 1, "total_files": 1, "current_file": None}
    manifest[0]["sha256"] = "0" * 64
    updates.clear()
    with pytest.raises(ValueError, match="checksum"):
        models.verify_snapshot(Recorder(), tmp_path, manifest)
    assert all(row["files_done"] == 0 for row in updates)
