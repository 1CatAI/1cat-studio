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
