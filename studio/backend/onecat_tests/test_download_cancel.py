# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import subprocess
import sys
import time

import psutil
import pytest
from onecat import db, jobs


def test_stuck_download_is_reaped_and_partial_file_is_retained(state):
    partial = state / "models" / "partial.safetensors"
    partial.write_bytes(b"resume data")
    id = "stuck-download-test"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time;signal.signal(signal.SIGTERM,lambda *a:None);print('ready',flush=True);time.sleep(60)",
            "-m",
            "onecat.worker",
            id,
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        db.put(
            "jobs",
            id,
            {
                "id": id,
                "kind": "download_model",
                "state": "running",
                "pid": process.pid,
                "process_created": psutil.Process(process.pid).create_time(),
            },
        )
        jobs.request_cancel(id)
        assert process.poll() is None
        jobs.list_jobs()
        assert process.poll() is None, "Allow cooperative shutdown before escalation"
        db.patch("jobs", id, {"cancel_requested_at": time.time() - 10})
        jobs.list_jobs()
        assert process.wait(timeout=3) != 0
        assert jobs.list_jobs()[0]["state"] == "cancelled"
        assert partial.read_bytes() == b"resume data"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdout.close()


@pytest.mark.parametrize(
    "kind,alive", [("start_model", True), ("install_runtime", True), ("download_model", False)]
)
def test_reaper_never_kills_inference_installs_or_reused_pids(monkeypatch, kind, alive):
    db.put(
        "jobs",
        "other",
        {
            "id": "other",
            "kind": kind,
            "state": "running",
            "pid": 1234,
            "process_created": 1,
            "cancel_requested": True,
            "cancel_requested_at": time.time() - 10,
        },
    )
    monkeypatch.setattr(jobs, "same_process", lambda *args: alive)

    def unexpected(*args):
        raise AssertionError("Must not signal this process")

    monkeypatch.setattr(jobs.psutil, "Process", unexpected)
    record = jobs.list_jobs()[0]
    assert record["state"] == ("running" if alive else "cancelled")
