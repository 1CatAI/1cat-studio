# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Recover owned launches and report progress without depending on a browser."""

from __future__ import annotations

import json
import re
import time

import httpx

from . import db
from .jobs import TERMINAL, same_process

PHASES = (
    "checking",
    "loading_weights",
    "compiling",
    "capturing_graphs",
    "checking_service",
    "ready",
)


def log_progress(text: str, previous: dict | None = None) -> dict:
    previous = previous or {}
    phase = previous.get("phase", "loading_weights")
    if phase not in PHASES:
        phase = "loading_weights"
    detail, progress = previous.get("detail", ""), previous.get("phase_progress")
    for raw in re.split(r"[\r\n]", text):
        line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw).strip()
        lower = line.lower()
        # The initial engine config contains words such as torch.compile and
        # inductor; it is not evidence that weights have finished loading.
        if (
            len(line) > 1200
            or "compilation_config" in lower
            or "compilationconfig(" in lower
            or "[vllm.py:" in line
            or "auto-setting" in lower
        ):
            continue
        selected = None
        if any(
            s in lower for s in ("loading safetensors", "loading checkpoint", "loading weights")
        ):
            selected = "loading_weights"
        elif any(s in lower for s in ("torch.compile", "compiling", "compile time", "inductor")):
            selected = "compiling"
        elif any(s in lower for s in ("capturing cuda", "graph capture", "capturing graphs")):
            selected = "capturing_graphs"
        elif any(
            s in lower
            for s in (
                "application startup complete",
                "uvicorn running",
                "engine initialization complete",
            )
        ):
            selected = "checking_service"
        if selected and PHASES.index(selected) >= PHASES.index(phase):
            phase, detail, progress = selected, line[-350:], None
            count = re.search(r"(?:\|\s*|\s)(\d+)\s*/\s*(\d+)\s*\[", line)
            if count and phase in {"loading_weights", "capturing_graphs"}:
                done, total = map(int, count.groups())
                if total and 0 <= done <= total:
                    progress = {"done": done, "total": total, "percent": done / total * 100}
    return {"phase": phase, "detail": detail, "phase_progress": progress}


def healthy(data: dict, client=None) -> bool:
    if not data.get("port") or not data.get("profile", {}).get("served_model_name"):
        return False
    owned = client is None
    client = client or httpx.Client(timeout=2, trust_env=False)
    base = f"http://127.0.0.1:{int(data['port'])}"
    headers = {"Authorization": "Bearer " + data.get("api_key", "")}
    try:
        health = client.get(base + "/health", headers=headers)
        if not health.is_success:
            return False
        response = client.get(base + "/v1/models", headers=headers)
        return response.is_success and any(
            m.get("id") == data["profile"]["served_model_name"]
            for m in response.json().get("data", [])
        )
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return False
    finally:
        if owned:
            client.close()


def patch_instance(data: dict, patch: dict) -> dict | None:
    """A late health response from an old launch must never change a new launch."""
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM records WHERE bucket='engine' AND id='active'"
        ).fetchone()
        if not row:
            return None
        current = json.loads(row[0])
        identity = ("launch_id", "pid", "process_created", "profile_id", "state")
        if any(current.get(k) != data.get(k) for k in identity):
            return None
        current.update(patch)
        conn.execute(
            "UPDATE records SET data=?,updated=? WHERE bucket='engine' AND id='active'",
            (json.dumps(current), time.time()),
        )
        return current


def reconcile_owned():
    from . import engine
    from .config import state_root

    data = engine.private_state()
    if data.get("state") not in {"loading", "ready", "unavailable"} or not data.get("pid"):
        return
    job = db.get("jobs", data.get("job_id", ""), {})
    if job.get("cancel_requested") or data.get("state") == "stopping":
        return
    alive = same_process(data["pid"], data.get("process_created"))
    if not alive:
        patch_instance(
            data,
            {
                "state": "failed",
                "phase": "failed",
                "pid": None,
                "error": "Engine process exited; inspect the startup log and retry",
            },
        )
        return
    if healthy(data):
        if patch_instance(
            data,
            {
                "state": "ready",
                "phase": "ready",
                "error": None,
                "health_checked_at": time.time(),
                "health_failures": 0,
                "ready_at": data.get("ready_at") or time.time(),
            },
        ):
            if (
                job
                and job.get("state") != "completed"
                and not same_process(job.get("pid"), job.get("process_created"))
            ):
                db.patch(
                    "jobs",
                    job["id"],
                    {
                        "state": "completed",
                        "stage": "ready",
                        "progress": 100,
                        "finished_at": time.time(),
                        "error": None,
                        "result": {"recovered": True, "profile_id": data.get("profile_id")},
                    },
                )
        return
    if data.get("state") in {"ready", "unavailable"}:
        # One transient health timeout does not declare a live engine dead.
        failures = data.get("health_failures", 0) + 1
        patch_instance(
            data,
            {
                "health_failures": failures,
                **({"state": "unavailable", "phase": "checking_service"} if failures >= 3 else {}),
            },
        )
    else:
        progress = log_progress(engine.read_log_tail(state_root() / "logs" / "engine.log"), data)
        if any(data.get(k) != v for k, v in progress.items()):
            patch_instance(data, {**progress, "progress_updated_at": time.time()})
        if job.get("state") in TERMINAL:
            # Keep observing an orphaned but still-initializing engine. UI can stop it.
            patch_instance(
                data, {"recovery_note": "Startup worker exited; monitoring the owned engine"}
            )
