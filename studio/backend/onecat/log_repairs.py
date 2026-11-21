# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Hand a diagnosed incident to Studio's isolated Codex workspace."""

import asyncio
import json
import shutil
import time

from . import db, engine, log_diagnostics as diagnostics
from .agent import tasks
from .config import state_root


def _case_key(source: str) -> str:
    if source == "engine":
        state = engine.private_state()
        return "engine:" + (state.get("launch_id") or state.get("job_id") or "current")
    return source


def _case(key: str) -> dict | None:
    link = db.get("log_repairs", key, {})
    task = db.get("agent_tasks", link.get("task_id", ""), {})
    if not task:
        return None
    return {"id": task["id"], "state": task["state"], "settled": task.get("settled", True)}


def latest(source: str) -> dict | None:
    return _case(_case_key(source))


def _evidence(source: str) -> tuple[dict, str]:
    snapshot = diagnostics.capture(source)
    secrets = diagnostics.secret_values()
    data = diagnostics._select(diagnostics.INCIDENT_FIELDS, snapshot)
    data["current_diagnostics"] = {
        name: diagnostics._redact_values(diagnostics.inspect(name, snapshot["profile"]), secrets)
        for name in ("engine_configuration", "host_resources", "gpu_status", "kernel_gpu_events")
    }
    return data, snapshot["locale"]


async def start(source: str) -> dict:
    key = _case_key(source)
    existing = _case(key)
    if existing and (existing["state"] in tasks.ACTIVE or not existing["settled"]):
        return existing

    data, locale = await asyncio.to_thread(_evidence, source)
    provider = diagnostics.provider_settings()
    revision = provider.get("revision") if diagnostics.public_settings(provider)["configured"] else None
    project = {
        "id": db.uid(),
        "name": ("故障处理" if locale != "en" else "Incident repair") + " · " + source,
        "created_at": time.time(),
        "repository": None,
        "log_repair_source": source,
    }
    workspace = state_root() / "agent/projects" / project["id"] / "workspace"
    prompt = (
        "检查 incident.json 中的故障，定位首个异常。在工作区验证并实现有证据支持的修复，"
        "写下验证结果和仍需在生产环境确认的操作。"
        if locale != "en"
        else "Investigate incident.json. Find the earliest exception, implement and test any justified "
             "repair in this workspace, and record the results and any remaining production checks."
    )
    try:
        workspace.mkdir(parents=True, mode=0o700)
        (workspace / "incident.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        db.put("agent_projects", project["id"], project)
        task = tasks.start(
            project["id"], prompt, db.uid(), engine="codex", permission="workspace-write",
            repair_provider_revision=revision,
        )
    except BaseException:
        shutil.rmtree(workspace.parent, ignore_errors=True)
        db.delete("agent_projects", project["id"])
        raise
    db.put("log_repairs", key, {"task_id": task["id"]})
    return {"id": task["id"], "state": task["state"], "settled": task.get("settled", False)}
