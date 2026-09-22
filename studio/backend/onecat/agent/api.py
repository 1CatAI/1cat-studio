# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import tempfile
import time
from contextlib import suppress
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response, UploadFile
from pydantic import Field
from starlette.responses import StreamingResponse

from .. import auth, db
from ..config import state_root
from ..schemas import StrictModel
from . import projects, runtime, tasks

router = APIRouter(prefix="/api/agent", dependencies=[Depends(auth.require_admin)])


class NewProject(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    repository: str | None = Field(default=None, max_length=200)


class NewTask(StrictModel):
    project_id: str
    prompt: str = Field(min_length=1, max_length=64000)
    request_id: str = Field(min_length=8, max_length=100)
    source_thread: str | None = None
    operation: Literal["turn", "review", "compact", "skills"] = "turn"
    permission: Literal["workspace-write", "read-only"] = "workspace-write"
    thinking: bool | None = None
    thinking_effort: Literal["low", "medium", "high", "xhigh"] | None = None
    mode: Literal["default", "plan"] = "default"


class ContinueTask(StrictModel):
    prompt: str = Field(
        default="继续完成这个任务 / Continue this task", min_length=1, max_length=64000
    )
    request_id: str = Field(min_length=8, max_length=100)
    operation: Literal["turn", "review", "compact", "skills"] = "turn"
    permission: Literal["workspace-write", "read-only"] | None = None
    thinking: bool | None = None
    thinking_effort: Literal["low", "medium", "high", "xhigh"] | None = None
    mode: Literal["default", "plan"] | None = None


class SteerTask(StrictModel):
    prompt: str = Field(min_length=1, max_length=30000)
    request_id: str = Field(min_length=8, max_length=100)
    turn_id: str = Field(min_length=1, max_length=200)


class Decision(StrictModel):
    decision: Literal["accept", "decline", "cancel"] = "accept"
    answers: dict[str, dict[str, list[str]]] | None = None


@router.get("/status")
async def status():
    result = await asyncio.to_thread(runtime.info)
    from .. import engine

    state = engine.private_state()
    profile = state.get("profile") or {}
    from ..model_controls import thinking_support

    result["model"] = {
        "thinking": thinking_support(profile),
        "ready": state.get("state") == "ready",
        "name": profile.get("served_model_name"),
        "label": profile.get("name"),
        "tool_calling": bool(profile.get("tool_calling")),
        "context_window": profile.get("max_model_len"),
    }
    return result


@router.get("/projects")
def list_projects():
    return {"items": db.all_records("agent_projects")}


@router.post("/projects")
async def create_project(data: NewProject):
    name = data.name.strip()
    if not name:
        raise ValueError("Enter a project name")
    if data.repository and not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", data.repository
    ):
        raise ValueError("Enter a public GitHub HTTPS repository URL")
    record = {
        "id": db.uid(),
        "name": name,
        "created_at": time.time(),
        "repository": data.repository,
    }
    location = state_root() / "agent/projects" / record["id"] / "workspace"
    location.parent.mkdir(parents=True, mode=0o700)
    try:
        if data.repository:
            with tempfile.TemporaryDirectory(prefix="onecat-git-") as home:
                process = await asyncio.create_subprocess_exec(
                    "/usr/bin/git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "protocol.allow=never",
                    "-c",
                    "protocol.https.allow=always",
                    "clone",
                    "--quiet",
                    "--depth=1",
                    "--",
                    data.repository,
                    str(location),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "HOME": home,
                        "GIT_CONFIG_NOSYSTEM": "1",
                        "GIT_TERMINAL_PROMPT": "0",
                    },
                )
                try:
                    _, stderr = await asyncio.wait_for(process.communicate(), 90)
                    if process.returncode:
                        raise ValueError(
                            "GitHub import failed: " + stderr.decode(errors="replace")[-1000:]
                        )
                finally:
                    if process.returncode is None:
                        with suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                        await process.wait()
        else:
            location.mkdir(mode=0o700)
        db.put("agent_projects", record["id"], record)
    except TimeoutError as error:
        shutil.rmtree(location.parent, ignore_errors=True)
        raise ValueError(
            "仓库导入超时，请检查网络后重试 / Repository import timed out; check connectivity and retry"
        ) from error
    except BaseException:
        shutil.rmtree(location.parent, ignore_errors=True)
        raise
    return record


@router.delete("/projects/{project_id}")
def delete_project(project_id: str):
    projects.get(project_id)
    tasks.require_idle(project_id)
    shutil.rmtree(projects.root(project_id).parent)
    for record in db.all_records("agent_tasks"):
        if record["project_id"] == project_id:
            shutil.rmtree(state_root() / "agent/tasks" / record["id"], ignore_errors=True)
            db.delete("agent_tasks", record["id"])
    db.delete("agent_projects", project_id)
    return {"deleted": True}


@router.get("/projects/{project_id}/files")
def list_files(project_id: str):
    return projects.files(project_id)


@router.get("/projects/{project_id}/file")
def read_file(project_id: str, path: str):
    content = projects.read(project_id, path)
    try:
        text = content.decode("utf-8")
        if "\x00" in text:
            raise UnicodeDecodeError("utf8", content, 0, 1, "binary content")
    except UnicodeDecodeError:
        return {"path": path, "binary": True, "bytes": len(content)}
    return {"path": path, "text": text, "bytes": len(content), "binary": False}


@router.post("/projects/{project_id}/files")
async def upload_file(project_id: str, file: UploadFile, path: str | None = None):
    tasks.require_idle(project_id)
    name = path or file.filename or "upload"
    projects.parts(name)
    tasks.uploading.add(project_id)
    try:
        data = await file.read(projects.MAX_FILE + 1)
        # shield the thread: cancellation must not release the write guard while
        # the filesystem write is still in progress.
        write = asyncio.create_task(asyncio.to_thread(projects.upload, project_id, name, data))
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            await write
            raise
        return {"path": name, "bytes": len(data)}
    finally:
        tasks.uploading.discard(project_id)


@router.get("/projects/{project_id}/download")
def download_project(project_id: str):
    return Response(
        projects.export(project_id),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="project-{project_id[:8]}.zip"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/tasks")
def list_tasks(
    project_id: str | None = None,
    q: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0, le=100000),
):
    return {"items": tasks.summaries(project_id, q, offset)}


@router.post("/tasks")
async def new_task(data: NewTask):
    if not data.prompt.strip():
        raise ValueError("Enter a task")
    return tasks.start(
        data.project_id,
        data.prompt,
        data.request_id,
        source_thread=data.source_thread,
        operation=data.operation,
        permission=data.permission,
        mode=data.mode,
        thinking=data.thinking,
        thinking_effort=data.thinking_effort,
    )


@router.get("/tasks/{task_id}")
def task(task_id: str):
    return tasks.get(task_id)


@router.post("/tasks/{task_id}/continue")
async def continue_task(task_id: str, data: ContinueTask):
    record = tasks.get(task_id)
    if not data.prompt.strip():
        raise ValueError("Enter a task")
    return tasks.start(
        record["project_id"],
        data.prompt,
        data.request_id,
        task_id=task_id,
        operation=data.operation,
        permission=data.permission,
        mode=data.mode,
        thinking=data.thinking,
        thinking_effort=data.thinking_effort,
    )


@router.post("/tasks/{task_id}/steer")
async def steer_task(task_id: str, data: SteerTask):
    if not data.prompt.strip():
        raise ValueError("Enter instructions")
    return await tasks.steer(task_id, data.prompt.strip(), data.request_id, data.turn_id)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    return await tasks.cancel(task_id)


@router.post("/tasks/{task_id}/approvals/{approval_id}")
async def approval(task_id: str, approval_id: str, data: Decision):
    return await tasks.approve(task_id, approval_id, data.decision, data.answers)


@router.get("/tasks/{task_id}/events")
async def events(task_id: str, request: Request):
    tasks.get(task_id)

    async def stream():
        run = tasks.runs.get(task_id)
        revision = run.revision if run else 0
        yield (
            "data: "
            + json.dumps({"type": "snapshot", "task": tasks.get(task_id)}, ensure_ascii=False)
            + "\n\n"
        )
        if run is None:
            return
        while not await request.is_disconnected():
            run.changed.clear()
            events = list(run.events)
            if events and revision < events[0][0] - 1:
                revision = run.revision
                yield (
                    "data: "
                    + json.dumps(
                        {"type": "snapshot", "task": tasks.get(task_id)}, ensure_ascii=False
                    )
                    + "\n\n"
                )
            else:
                batch = [event for number, event in events if number > revision]
                if batch:
                    revision = events[-1][0]
                    yield (
                        "data: "
                        + json.dumps({"type": "batch", "events": batch}, ensure_ascii=False)
                        + "\n\n"
                    )
            if task_id not in tasks.workers:
                yield (
                    "data: "
                    + json.dumps(
                        {"type": "snapshot", "task": tasks.get(task_id)}, ensure_ascii=False
                    )
                    + "\n\n"
                )
                return
            try:
                await asyncio.wait_for(run.changed.wait(), 15)
                await asyncio.sleep(0.08)
            except TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
