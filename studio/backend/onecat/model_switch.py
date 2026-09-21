# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""One admission queue for Studio-owned text and creative model lifecycles."""

import fcntl

from . import db
from .config import state_root
from .jobs import TERMINAL, create_job, list_jobs, request_cancel

KINDS = {"start_model", "stop_model", "creative_load", "creative_stop"}


def gpu_scope(kind, payload):
    if kind.startswith("creative_"):
        return set(
            db.get("creative_services", payload.get("service_id", ""), {}).get("gpu_uuids", [])
        )
    if kind == "start_model":
        return set(db.get("profiles", payload.get("profile_id", ""), {}).get("gpu_uuids", []))
    return set(db.get("engine", "active", {}).get("profile", {}).get("gpu_uuids", []))


def schedule(kind, payload):
    # Serialize duplicate detection and cancellation with creation, not just execution.
    with (state_root() / "model-queue.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        list_jobs()
        pending = [
            j for j in db.all_records("jobs") if j["kind"] in KINDS and j["state"] not in TERMINAL
        ]
        for job in pending:
            if (
                job["kind"] == kind
                and job["payload"] == payload
                and not job.get("cancel_requested")
            ):
                return {k: v for k, v in job.items() if k != "payload"}
        scope = gpu_scope(kind, payload)
        for job in pending:
            if job["kind"] not in {"start_model", "creative_load"}:
                continue
            same_service = (
                kind.startswith("creative_")
                and job["kind"] == "creative_load"
                and payload.get("service_id") == job["payload"].get("service_id")
            )
            text_replacement = (
                kind in {"start_model", "stop_model"} and job["kind"] == "start_model"
            )
            if (
                same_service
                or text_replacement
                or scope.intersection(gpu_scope(job["kind"], job["payload"]))
            ):
                request_cancel(job["id"])
        result = create_job(kind, payload)
        return {k: v for k, v in result.items() if k != "payload"}
