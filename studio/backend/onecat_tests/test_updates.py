# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import base64
import hashlib
import io
import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from onecat import db, ota, update_protocol as protocol, updates


@pytest.fixture
def signed(monkeypatch, tmp_path):
    key = Ed25519PrivateKey.generate()
    keys = tmp_path / "keys.json"
    keys.write_text(
        json.dumps({"test": base64.b64encode(key.public_key().public_bytes_raw()).decode()})
    )
    monkeypatch.setattr(protocol, "KEYS", keys)
    updates._cache.clear()

    def make(**changes):
        value = dict(
            format=protocol.FORMAT,
            channel="stable",
            version="0.5.0-0123456789",
            source_commit="a" * 40,
            sequence=100,
            target="linux-x86_64",
            url="http://updates.test/release.tar.gz",
            size=6,
            unpacked_size=60,
            sha256=hashlib.sha256(b"abcdef").hexdigest(),
            min_updater=1,
            key_id="test",
        )
        value.update(changes)
        value["signature"] = base64.b64encode(key.sign(protocol.canonical(value))).decode()
        return value

    return make


def test_signature_and_required_fields(signed):
    value = signed()
    assert protocol.verify(value) is value
    value["url"] = "http://attacker.test/file"
    with pytest.raises(ValueError, match="signature"):
        protocol.verify(value)
    for changes in (
        {"size": 0},
        {"sequence": True},
        {"source_commit": "abc"},
        {"min_updater": 2},
        {"version": "../../bad"},
        {"target": "macos"},
        {"url": "http://user:secret@host/file"},
    ):
        with pytest.raises(ValueError):
            protocol.verify(signed(**changes))
    with pytest.raises(ValueError, match="no signed"):
        protocol.verify({"format": protocol.FORMAT})
    with pytest.raises(ValueError, match="Duplicate"):
        protocol.parse_manifest(b'{"size":1,"size":2}')


def response(data, status=200, headers=None):
    result = io.BytesIO(data)
    result.status, result.headers = status, headers or {}
    return result


def test_channel_replay_and_changed_release_rejected(signed, monkeypatch):
    monkeypatch.setattr(
        updates, "urlopen", lambda *a, **kw: response(json.dumps(signed()).encode())
    )
    assert updates.fetch(True)["sequence"] == 100
    for value in (signed(sequence=99), signed(notes="changed")):
        monkeypatch.setattr(
            updates, "urlopen", lambda *a, **kw: response(json.dumps(value).encode())
        )
        with pytest.raises(ValueError):
            updates.fetch(True)


@pytest.mark.parametrize("status", [200, 206, 416])
def test_resume_respects_range_response(signed, tmp_path, monkeypatch, status):
    target = tmp_path / "release.tar.gz"
    target.with_suffix(".gz.part").write_bytes(b"abc")
    requests = []

    def get(req, **kwargs):
        requests.append(req.get_header("Range"))
        if status == 416 and len(requests) == 1:
            raise HTTPError(req.full_url, 416, "range", {}, None)
        if status == 206:
            return response(b"def", 206, {"Content-Range": "bytes 3-5/6"})
        return response(b"abcdef")

    monkeypatch.setattr(ota, "urlopen", get)
    progress = []
    ota.download(signed(), target, lambda **v: progress.append(v))
    assert target.read_bytes() == b"abcdef"
    assert requests[0] == "bytes=3-"
    if status == 416:
        assert requests == ["bytes=3-", None]
    assert progress[-1]["downloaded_bytes"] == 6


def test_corrupt_partial_and_asset_rejected(signed, tmp_path, monkeypatch):
    target = tmp_path / "release.tar.gz"
    monkeypatch.setattr(ota, "urlopen", lambda *a, **kw: response(b"xxx"))
    with pytest.raises(ValueError, match="interrupted"):
        ota.download(signed(), target, lambda **v: None)
    assert target.with_suffix(".gz.part").read_bytes() == b"xxx"
    monkeypatch.setattr(
        ota, "urlopen", lambda *a, **kw: response(b"abcdef", 206, {"Content-Range": "bytes 0-5/6"})
    )
    with pytest.raises(ValueError, match="Content-Range"):
        ota.download(signed(), target, lambda **v: None)
    monkeypatch.setattr(ota, "urlopen", lambda *a, **kw: response(b"badbad"))
    with pytest.raises(ValueError, match="SHA256"):
        ota.download(signed(), target, lambda **v: None)
    assert not target.exists()
    assert not target.with_suffix(".gz.part").exists()


@pytest.mark.parametrize(
    "bucket,state_value",
    [("jobs", "running"), ("agent_tasks", "waiting"), ("creative_runs", "queued")],
)
def test_busy_tasks_not_stopped(state, bucket, state_value):
    db.put(bucket, "test", {"id": "test", "state": state_value})
    assert ota.busy_reason(state)
    db.put(bucket, "test", {"id": "test", "state": "completed"})
    assert ota.busy_reason(state) is None


def test_running_source_ignores_stale_current_release(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "APP_ROOT", tmp_path / "source")
    prefix = tmp_path / "app"
    (prefix / "current").mkdir(parents=True)
    (prefix / "current/manifest.json").write_text('{"version":"old"}')
    monkeypatch.setenv("ONECAT_STUDIO_PREFIX", str(prefix))
    assert updates.deployment()["mode"] == "source"
    assert updates.installed_version() != "old"


def test_api_pins_selected_release_and_blocks_activation(client, signed, monkeypatch, state):
    value = signed()
    monkeypatch.setattr(updates, "fetch", lambda *a, **kw: value)
    calls = []
    monkeypatch.setattr(updates, "start", lambda *a, **kw: calls.append((a, kw)) or {})
    assert client.post("/api/updates/install", json={}).status_code == 409
    assert not calls
    assert (
        client.post(
            "/api/updates/install",
            json={"release_id": protocol.release_id(value), "switch_to_release": True},
        ).status_code
        == 200
    )
    assert calls[0][1]["switch_to_release"] is True
    protocol.atomic_json(state / "updates/activation.json", {"operation": "test"})
    assert client.put("/api/settings", json={"theme": "dark"}).status_code == 503
    assert client.post("/v1/chat/completions", json={}).status_code == 503
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/updates/progress").status_code == 200


class Services:
    def __init__(self, state, healthy=True):
        self.calls, self.state, self.healthy = [], state, healthy

    def run(self, *args):
        self.calls.append(args)

    def health(self, port, version):
        self.calls.append(("health", version))
        if version and not self.healthy:
            db.put("settings", "main", {"theme": "changed-by-new-version"})
            return False
        return True


def operation(signed, tmp_path, state, monkeypatch, healthy=True):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    work = state / "updates/operations/test"
    work.mkdir(parents=True)
    manifest = work / "release.json"
    protocol.atomic_json(manifest, signed())
    args = Namespace(
        state=state,
        prefix=tmp_path / "app",
        manifest=manifest,
        service="onecat-studio-test.service",
        port=8898,
        host="127.0.0.1",
        operation="test",
    )
    services = Services(state, healthy)
    op = ota.Operation(args, services)
    op.prefix.mkdir()
    (op.prefix / "current").symlink_to("/old/release")
    (op.prefix / "previous").symlink_to("/older/release")
    op.dropin.parent.mkdir(parents=True)
    op.dropin.write_text("original override")
    db.put("settings", "main", {"theme": "original"})
    return op, services


def test_failed_activation_restores_links_unit_and_database(signed, tmp_path, state, monkeypatch):
    op, services = operation(signed, tmp_path, state, monkeypatch, healthy=False)
    monkeypatch.setattr(ota, "download", lambda *a: None)
    monkeypatch.setattr(ota, "prepare_release", lambda *a: op.prefix / "releases/new")
    assert op.run() == 0
    assert updates.read_status()["stage"] == "rolled_back"
    assert db.get("settings", "main")["theme"] == "original"
    assert str((op.prefix / "current").readlink()) == "/old/release"
    assert str((op.prefix / "previous").readlink()) == "/older/release"
    assert op.dropin.read_text() == "original override"
    assert not op.gate.exists()
    assert all("engine" not in str(call) for call in services.calls)


def test_journal_recovers_after_worker_interruption(signed, tmp_path, state, monkeypatch):
    op, services = operation(signed, tmp_path, state, monkeypatch)
    journal = op.snapshot()
    ota.backup_databases(state, op.work / "backup")
    op.save(journal, backup_ready=True, stage="activated")
    db.put("settings", "main", {"theme": "changed"})
    ota.link(op.prefix / "current", "/new/release")
    op.dropin.write_text("new override")
    protocol.atomic_json(op.gate, {"operation": "test"})
    assert op.run() == 0
    assert updates.read_status()["stage"] == "rolled_back"
    assert db.get("settings", "main")["theme"] == "original"
    assert str((op.prefix / "current").readlink()) == "/old/release"
    assert not op.gate.exists()


def test_disk_failure_does_not_stop_manager(signed, tmp_path, state, monkeypatch):
    op, services = operation(signed, tmp_path, state, monkeypatch)
    monkeypatch.setattr(ota.shutil, "disk_usage", lambda p: SimpleNamespace(free=0))
    op.run()
    assert updates.read_status()["stage"] == "failed"
    assert services.calls == []
    assert not op.journal_path.exists()
