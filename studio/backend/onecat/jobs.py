# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Persistent subprocess jobs survive a browser disconnect and manager restart."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import psutil

from . import db
from .config import backend_root, initialize_paths

TERMINAL = {"completed", "failed", "cancelled"}
DOWNLOAD_CANCEL_GRACE = 5


class Cancelled(Exception):
    pass


def same_process(pid: int | None, created: float | None) -> bool:
    if not pid or not created:
        return False
    try:
        proc = psutil.Process(pid)
        return abs(proc.create_time() - created) < 0.01 and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def create_job(kind: str, payload: dict) -> dict:
    root = initialize_paths()
    id = db.uid()
    record = {
        "id": id,
        "kind": kind,
        "payload": payload,
        "state": "queued",
        "stage": "queued",
        "progress": 0,
        "created_at": time.time(),
        "cancel_requested": False,
    }
    if kind == "install_runtime":
        release = payload.get("release", {})
        record.update(runtime_release_id=payload.get("release_id"),
                      runtime_version=release.get("version"))
    if kind == "creative_download":
        record["component_id"] = payload.get("component_id")
    db.put("jobs", id, record)
    env = {
        **os.environ,
        "ONECAT_STUDIO_HOME": str(root),
        "PYTHONPATH": str(backend_root()),
        "PYTHONUNBUFFERED": "1",
        "HF_HUB_DISABLE_XET": "1",
    }
    try:
        with (root / "logs" / f"job-{id}.log").open("ab") as output:
            process = subprocess.Popen(
                [sys.executable, "-m", "onecat.worker", id],
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        db.patch(
            "jobs", id,
            {"pid": process.pid, "process_created": psutil.Process(process.pid).create_time()},
        )
    except (OSError, psutil.Error) as error:
        if db.get("jobs", id)["state"] not in TERMINAL:
            Job(id).fail(error)
            raise ValueError("Unable to start background task: " + str(error)) from error
    return db.get("jobs", id)


def list_jobs() -> list[dict]:
    result = []
    for record in db.all_records("jobs"):
        if (
            record["state"] not in TERMINAL and not record.get("pid")
            and time.time() - record.get("created_at", time.time()) > 30
        ):
            record = db.patch("jobs", record["id"], {
                "state": "cancelled" if record.get("cancel_requested") else "failed",
                "error": "Background task could not start; retry the operation",
                "finished_at": time.time(),
            })
        if record["state"] not in TERMINAL and record.get("pid"):
            if (
                record.get("kind") in {"download_model", "creative_download"}
                and record.get("cancel_requested")
                and record.get("cancel_requested_at")
                and time.time() - record["cancel_requested_at"] >= DOWNLOAD_CANCEL_GRACE
                and same_process(record["pid"], record.get("process_created"))
            ):
                # Hub thread pools can wait indefinitely after SIGTERM. Only a
                # cancelled, owned file-transfer worker may be killed; inference
                # and runtime-installation processes keep cooperative teardown.
                try:
                    process = psutil.Process(record["pid"])
                    latest = db.get("jobs", record["id"], {})
                    if (
                        process.cmdline()[-3:] == ["-m", "onecat.worker", record["id"]]
                        and latest.get("state") not in TERMINAL
                        and abs(process.create_time() - record["process_created"]) < 0.01
                    ):
                        process.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if not same_process(record["pid"], record.get("process_created")):
                record = db.patch(
                    "jobs",
                    record["id"],
                    {
                        "state": "cancelled" if record.get("cancel_requested") else "failed",
                        "error": "Operation cancelled; partial downloads can be resumed"
                        if record.get("cancel_requested")
                        else "Worker exited before completing the operation",
                        "finished_at": time.time(),
                    },
                )
        result.append({k: v for k, v in record.items() if k != "payload"})
    return result


def request_cancel(id: str):
    record = db.get("jobs", id)
    if not record or record["state"] in TERMINAL or record.get("cancel_requested"):
        return
    db.patch(
        "jobs",
        id,
        {
            "cancel_requested": True,
            "cancel_requested_at": record.get("cancel_requested_at") or time.time(),
        },
    )
    # Interrupt blocking network calls too. GPU/lifecycle jobs use cooperative
    # cancellation so their teardown and hardware restoration remain in control.
    if (
        record["kind"]
        in {
            "download_model",
            "creative_download",
            "import_runtime",
            "install_runtime",
            "import_offline",
            "export_runtime",
        }
        and record.get("state") == "running"
    ):
        if same_process(record.get("pid"), record.get("process_created")):
            try:
                os.kill(record["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass


class Job:
    def __init__(self, id: str):
        self.id = id
        self.record = db.get("jobs", id)
        self.payload = self.record["payload"]

    def check_cancelled(self):
        if db.get("jobs", self.id).get("cancel_requested"):
            raise Cancelled("Operation cancelled")

    def update(self, stage: str, progress: float | None = None, *, ignore_cancel=False, **details):
        if not ignore_cancel:
            self.check_cancelled()
        patch = {"state": "running", "stage": stage, "updated_at": time.time(), **details}
        if progress is not None:
            patch["progress"] = min(100, max(0, progress))
        db.patch("jobs", self.id, patch)

    def finish(self, result: dict | None = None):
        db.patch(
            "jobs",
            self.id,
            {
                "state": "completed",
                "progress": 100,
                "result": result or {},
                "finished_at": time.time(),
            },
        )

    def fail(self, error: Exception):
        db.patch(
            "jobs",
            self.id,
            {
                "state": "cancelled" if isinstance(error, Cancelled) else "failed",
                "error": str(error)[-3000:],
                "finished_at": time.time(),
            },
        )
