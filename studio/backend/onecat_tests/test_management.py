import io
import json
import tarfile

import pytest
from fastapi import HTTPException
from onecat import db, engine, gpu, proxy
from onecat.efficiency import recommend
from onecat.runtimes import extract_archive
from onecat.schemas import Profile


def test_auth_scopes_csrf_and_revocation(client):
    key = client.post("/api/keys", json={"name": "integration"}).json()["key"]
    assert key.startswith("oc_")
    assert key not in client.get("/api/keys").text
    assert (
        client.get("/api/profiles", headers={"Authorization": "Bearer " + key}).status_code == 403
    )
    assert client.get("/v1/models", headers={"Authorization": "Bearer " + key}).status_code == 200
    assert (
        client.post("/api/keys", json={}, headers={"Origin": "https://other.example"}).status_code
        == 403
    )
    id = client.get("/api/keys").json()["items"][0]["id"]
    assert client.delete("/api/keys/" + id).status_code == 200
    assert client.get("/v1/models", headers={"Authorization": "Bearer " + key}).status_code == 401
    assert client.post("/api/auth/setup", json={"password": "another-password"}).status_code == 409
    assert (
        client.post(
            "/api/auth/password",
            json={"current_password": "test-only-password", "new_password": "updated-password"},
        ).status_code
        == 200
    )
    assert client.get("/api/profiles").status_code == 401
    assert client.post("/api/auth/login", json={"password": "updated-password"}).status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/profiles").status_code == 401


def test_chat_unicode_edit_import_backup(client, state):
    thread = client.post(
        "/api/chat/import",
        json={
            "title": "中文聊天",
            "messages": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好 🌊"},
            ],
        },
    ).json()
    loaded = client.get("/api/chat/threads/" + thread["id"]).json()
    assert loaded["messages"][1]["content"][0]["text"] == "你好 🌊"
    items = loaded["messages"][:1]
    items[0]["content"][0]["text"] = "修改了"
    assert (
        client.put(
            "/api/chat/threads/" + thread["id"] + "/messages",
            json={"messages": items, "replace": True},
        ).status_code
        == 200
    )
    assert len(client.get("/api/chat/threads/" + thread["id"]).json()["messages"]) == 1
    assert client.get("/api/chat/threads?q=修改").json()["items"][0]["id"] == thread["id"]
    assert (
        client.put(
            "/api/chat/threads/" + thread["id"] + "/messages", json={"messages": [4]}
        ).status_code
        == 400
    )
    backup = client.post("/api/backup").json()["filename"]
    assert client.get("/api/backup/" + backup).status_code == 200
    client.delete("/api/chat/threads/" + thread["id"])
    assert (
        client.post(
            "/api/backup/restore", json={"path": str(state / "backups" / backup)}
        ).status_code
        == 200
    )
    assert client.get("/api/chat/threads").status_code == 401
    client.post("/api/auth/login", json={"password": "test-only-password"})
    assert client.get("/api/chat/threads/" + thread["id"]).status_code == 200


@pytest.mark.parametrize(
    "args", [["--port=9000"], ["--api-key", "secret"], ["-tp", "8"], ["--speculative-config={}"]]
)
def test_managed_fields_cannot_be_overridden(args):
    with pytest.raises(ValueError):
        Profile(name="test", model_path="/model", runtime_id="r", extra_args=args)


def test_request_admission_blocks_during_maintenance():
    db.put("engine", "active", {"state": "ready", "port": 1234})
    db.put("engine", "maintenance", {"job_id": "job"})
    with pytest.raises(HTTPException):
        proxy.begin("model", "api", 1234)
    assert engine.active_requests() == 0
    db.delete("engine", "maintenance")
    with pytest.raises(HTTPException):
        proxy.begin("model", "api", 4321)
    id, start = proxy.begin("model", "api", 1234)
    assert engine.active_requests() == 1
    proxy.finish(id, start, 200, {"completion_tokens": 4}, 0.1)
    assert engine.active_requests() == 0


@pytest.mark.parametrize(
    "name,symlink", [("../escaped", False), ("/tmp/escaped", False), ("link", True)]
)
def test_archive_rejects_escape(tmp_path, name, symlink):
    source = tmp_path / "bad.tar"
    target = tmp_path / "target"
    target.mkdir()
    with tarfile.open(source, "w") as archive:
        member = tarfile.TarInfo(name)
        if symlink:
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/passwd"
        else:
            member.size = 1
        archive.addfile(member, io.BytesIO(b"x") if not symlink else None)
    with pytest.raises((ValueError, tarfile.FilterError)):
        extract_archive(source, target)


def test_hardware_capture_rejects_unknown(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(gpu, "check_ownership", lambda ids: None)
    monkeypatch.setattr(
        gpu.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout=json.dumps({"gpu": {"clock_policy_known": False}})
        ),
    )
    with pytest.raises(ValueError, match="unknown"):
        gpu.capture_settings(["gpu"])


def test_calibration_recommendation_requires_complete_matching_restored_run():
    runtime = {"capabilities": {"build": "same"}}
    db.put("runtimes", "r", runtime)
    db.put("engine", "active", {"identity": "profile", "runtime_id": "r"})
    run = {
        "profile_identity": "profile",
        "collector_version": 2,
        "runtime_identity": runtime["capabilities"],
        "state": "failed",
        "hardware_restored": True,
        "summary": [],
    }
    db.put("benchmarks", "run", run)
    with pytest.raises(ValueError):
        recommend({"run_id": "run"})

    def point(w, j, elapsed):
        return {
            "valid_repeats": 3,
            "setting": {"power_limit_w": w},
            "prefill_tokens_s": 3500,
            "decode_tokens_s": 60,
            "ttft_s": 2.5,
            "total_gpu_j": j,
            "total_gpu_j_std": 3,
            "elapsed_s": elapsed,
        }

    run.update(
        state="completed",
        summary=[point(450, 8000, 20), point(470, 8001, 19), point(800, 12000, 15)],
    )
    db.put("benchmarks", "run", run)
    assert recommend({"run_id": "run"})["recommendation"]["setting"]["power_limit_w"] == 470
    assert recommend({"run_id": "run", "min_decode_tokens_s": 100})["recommendation"] is None
    db.patch("engine", "active", {"identity": "different"})
    with pytest.raises(ValueError):
        recommend({"run_id": "run"})


def test_crashed_calibration_recovery_retains_failed_journal(state, monkeypatch):
    journal = state / "benchmarks" / "run" / "restore-hardware.json"
    journal.parent.mkdir()
    journal.write_text(json.dumps({"gpu": {"reset_clocks": True}}))
    monkeypatch.setattr(gpu, "check_ownership", lambda ids: None)

    def fail(settings):
        raise RuntimeError("helper unavailable")

    monkeypatch.setattr(gpu, "restore", fail)
    gpu.recover_interrupted()
    assert journal.exists() and db.get("engine", "hardware_recovery")
    monkeypatch.setattr(gpu, "restore", lambda settings: None)
    gpu.recover_interrupted()
    assert not journal.exists() and db.get("benchmarks", "run")["hardware_restored"]


def test_startup_recovers_only_preexisting_requests():
    import time

    from fastapi.testclient import TestClient
    from onecat.app import create_app

    with db.connect() as conn:
        conn.execute("INSERT INTO requests(id,started) VALUES('old',?)", (time.time(),))
    with TestClient(create_app()):
        with db.connect() as conn:
            assert conn.execute("SELECT status FROM requests WHERE id='old'").fetchone()[0] == 499
            conn.execute("INSERT INTO requests(id,started) VALUES('new',?)", (time.time(),))
        time.sleep(2.1)
        with db.connect() as conn:
            assert conn.execute("SELECT status FROM requests WHERE id='new'").fetchone()[0] is None
