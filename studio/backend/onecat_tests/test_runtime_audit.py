# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import copy
import hashlib
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from onecat import app, db, gpu, jobs, runtimes
from onecat import runtime_releases as releases
from onecat_tests.test_runtime_releases import published, recipe, write_wheel


def task(id="install", payload=None):
    db.put(
        "jobs",
        id,
        {
            "id": id,
            "kind": "install_runtime",
            "state": "running",
            "payload": payload or {},
            "cancel_requested": False,
        },
    )
    return jobs.Job(id)


def test_retry_reuses_partial_environment_without_erasing_dependencies(state, monkeypatch):
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv is required for the real offline venv retry regression")
    row = recipe()
    folder = state / "runtimes" / row["id"]
    envdir = folder / "env"
    subprocess.run(
        [
            uv,
            "venv",
            "--offline",
            "--no-config",
            "--python",
            sys.executable,
            str(envdir),
        ],
        check=True,
        capture_output=True,
    )
    marker = envdir / "keep-downloaded-dependency"
    marker.write_text("already installed")
    asset = row["assets"][0]
    wheel = write_wheel(state / "downloads" / row["id"] / asset["name"], asset)
    asset["sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    commands = []
    real_run = subprocess.run
    monkeypatch.setattr(releases, "availability", lambda *args: [])
    monkeypatch.setattr(gpu, "snapshot", lambda: {"gpus": []})
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: uv)
    monkeypatch.setattr(
        runtimes.shutil, "disk_usage", lambda path: SimpleNamespace(free=100 * 1024**3)
    )

    def command(job, argv, **kwargs):
        commands.append(argv)
        if "venv" in argv:
            real_run(argv, stdin=subprocess.DEVNULL, capture_output=True, check=True, **kwargs)

    monkeypatch.setattr(runtimes, "run_command", command)
    monkeypatch.setattr(
        runtimes.subprocess,
        "run",
        lambda argv, **kwargs: SimpleNamespace(
            stdout=sys.executable if "find" in argv else "3.12.14"
        ),
    )
    monkeypatch.setattr(runtimes, "inspect_runtime", lambda record: record)
    assert runtimes.install(task(payload={"release": row}))["runtime_id"] == row["id"]
    assert marker.read_text() == "already installed"
    assert any("--constraint" in argv for argv in commands)


def test_release_description_change_does_not_create_duplicate_install(client, monkeypatch):
    row = recipe()
    monkeypatch.setattr(releases, "resolve", lambda *args: copy.deepcopy(row))
    monkeypatch.setattr(app.gpu, "snapshot", lambda: {"gpus": []})

    def create(kind, payload):
        id = db.uid()
        return db.put("jobs", id, {"id": id, "kind": kind, "payload": payload, "state": "queued"})

    monkeypatch.setattr(app, "create_job", create)
    first = client.post("/api/runtimes/install", json={"release_id": row["id"]}).json()
    row.update(title="Updated release notes", torch="2.10.0")
    second = client.post("/api/runtimes/install", json={"release_id": row["id"]}).json()
    assert first["id"] == second["id"]
    assert len(db.all_records("jobs")) == 1


def test_cancel_is_idempotent_during_process_cleanup(monkeypatch):
    task()
    db.patch("jobs", "install", {"pid": 12345, "process_created": 123})
    signals = []
    monkeypatch.setattr(jobs, "same_process", lambda *args: True)
    monkeypatch.setattr(jobs.os, "kill", lambda *args: signals.append(args))
    jobs.request_cancel("install")
    jobs.request_cancel("install")
    assert len(signals) == 1


def test_completed_partial_is_verified_and_promoted_without_redownload(state, monkeypatch):
    data = b"completed download before worker interruption"
    target = state / "downloads/file.whl"
    target.parent.mkdir(parents=True)
    target.with_suffix(".whl.part").write_bytes(data)
    monkeypatch.setattr(
        runtimes.requests, "get", lambda *a, **k: pytest.fail("Unnecessary redownload")
    )
    runtimes.download_file(
        task(), "https://example.com/file", target, hashlib.sha256(data).hexdigest()
    )
    assert target.read_bytes() == data
    assert not target.with_suffix(".whl.part").exists()


def test_resuming_wrong_range_does_not_destroy_valid_partial(state, monkeypatch):
    data = b"abcdef"
    target = state / "downloads/file.whl"
    target.parent.mkdir(parents=True)
    partial = target.with_suffix(".whl.part")
    partial.write_bytes(data[:3])

    class Response:
        status_code = 206

        def __init__(self):
            self.headers = {"content-range": "bytes 0-2/6", "content-length": "3"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            yield data[:3]

    monkeypatch.setattr(runtimes.requests, "get", lambda *a, **k: Response())
    with pytest.raises(ValueError, match="[Rr]ange"):
        runtimes.download_file(
            task(), "https://example.com/file", target, hashlib.sha256(data).hexdigest()
        )
    assert partial.read_bytes() == data[:3]


def test_conflicting_digest_and_checksum_file_blocks_only_that_release(monkeypatch):
    bad = published("1.6.0", companion=True)
    bad["assets"][1]["digest"] = None
    good = published("1.5.0")
    monkeypatch.setattr(releases, "github_page", lambda page: [bad, good])
    monkeypatch.setattr(
        releases,
        "_checksums",
        lambda item: (
            {
                bad["assets"][0]["name"]: "f" * 64,
                bad["assets"][1]["name"]: "2" * 64,
            }
            if item is bad
            else {}
        ),
    )
    rows = releases.fetch_releases()
    assert len(rows) == 2
    assert any("conflict" in p.lower() for p in rows[0]["problems"])
    assert not rows[1]["problems"]


def test_one_unreadable_checksum_file_does_not_hide_other_releases(monkeypatch):
    bad, good = published("1.6.0"), published("1.5.0")
    monkeypatch.setattr(releases, "github_page", lambda page: [bad, good])

    def sums(item):
        if item is bad:
            raise releases.requests.ConnectionError("checksum unavailable")
        return {}

    monkeypatch.setattr(releases, "_checksums", sums)
    rows = releases.fetch_releases()
    assert len(rows) == 2 and rows[0]["problems"] and not rows[1]["problems"]


@pytest.mark.parametrize("resumed", [True, False])
def test_valid_resume_and_ignored_range_finish_with_exact_progress(state, monkeypatch, resumed):
    data = b"abcdef"
    target = state / "downloads/file.whl"
    target.parent.mkdir(parents=True)
    target.with_suffix(".whl.part").write_bytes(data[:3])

    class Response:
        status_code = 206 if resumed else 200

        def __init__(self):
            self.headers = {"content-length": "3" if resumed else "6"}
            if resumed:
                self.headers["content-range"] = "bytes 3-5/6"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            yield data[3:] if resumed else data

    def get(url, **kwargs):
        assert kwargs["headers"]["Range"] == "bytes=3-"
        return Response()

    monkeypatch.setattr(runtimes.requests, "get", get)
    runtimes.download_file(
        task(), "https://example.com/file", target, hashlib.sha256(data).hexdigest()
    )
    assert target.read_bytes() == data
    record = db.get("jobs", "install")
    assert record["downloaded_bytes"] == record["expected_bytes"] == 6
    assert record["progress"] == 100


def test_incomplete_transfer_retains_partial_for_retry(state, monkeypatch):
    target = state / "downloads/file.whl"

    class Response:
        status_code = 200

        def __init__(self):
            self.headers = {"content-length": "6"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            yield b"abc"

    monkeypatch.setattr(runtimes.requests, "get", lambda *a, **k: Response())
    with pytest.raises(ValueError, match="incomplete"):
        runtimes.download_file(
            task(), "https://example.com/file", target, hashlib.sha256(b"abcdef").hexdigest()
        )
    assert target.with_suffix(".whl.part").read_bytes() == b"abc" and not target.exists()
