# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Durable Studio tasks; the official Codex process owns planning and tools."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import hashlib
import os
import re
import signal
import tempfile
import time
from collections import deque
from pathlib import Path

import httpx
from fastapi import HTTPException

from .. import db, proxy
from ..request_metrics import TokenTiming, usage_cache
from . import projects, runtime
from .protocol import ResponseStream, convert_input

ACTIVE = {"starting", "running", "waiting", "stopping"}
runs: dict[str, Run] = {}
workers: dict[str, asyncio.Task] = {}
uploading: set[str] = set()


def get(task_id):
    if task_id in runs:
        return copy.deepcopy(runs[task_id].record)
    record = db.get("agent_tasks", task_id)
    if not record:
        raise HTTPException(404, "Agent task not found")
    return record


def active_project(project_id):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT data FROM records WHERE bucket='agent_tasks' "
            "AND json_extract(data,'$.project_id')=? AND json_extract(data,'$.state') "
            "IN ('starting','running','waiting','stopping') LIMIT 1",
            (project_id,),
        ).fetchone()
    return json.loads(row[0]) if row else None


def summaries(project_id=None, query="", offset=0):
    # Sidebar polling never transfers full transcripts into the Python heap.
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT json_remove(data,'$.items','$.diff','$.request_ids','$.request_signatures','$.steering','$.plan') "
            "FROM records WHERE bucket='agent_tasks' AND (? IS NULL OR json_extract(data,'$.project_id')=?) "
            "AND instr(lower(json_extract(data,'$.title')), lower(?)) > 0 "
            "ORDER BY updated DESC, id DESC LIMIT 100 OFFSET ?",
            (project_id, project_id, query.strip(), max(0, offset)),
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def require_idle(project_id):
    if project_id in uploading:
        raise HTTPException(
            409,
            "项目文件正在上传，请稍后重试 / Project files are uploading; retry after completion",
        )
    if active_project(project_id) or any(r.project["id"] == project_id for r in runs.values()):
        raise HTTPException(
            409, "项目正在执行任务，请先停止 / Stop the project's current task first"
        )


def recover():
    runs.clear()
    workers.clear()
    uploading.clear()
    for record in db.all_records("agent_tasks"):
        if record["state"] in ACTIVE or record.get("settled") is False:
            record.update(
                state="interrupted",
                error="Studio 已重启，任务内容已保留，可以继续 / Studio restarted; resume this task",
                approvals=[],
                updated_at=time.time(),
                settled=True,
            )
            db.put("agent_tasks", record["id"], record)


async def shutdown():
    for run in list(runs.values()):
        run.shutdown_requested = True
    for task_id, worker in list(workers.items()):
        run = runs.get(task_id)
        # Do not inject a second cancellation into an already-running finally
        # block: that would abandon process reaping and project diff persistence.
        if not worker.cancelling() and run and run.record["state"] in ACTIVE:
            worker.cancel()
    if workers:
        await asyncio.gather(*list(workers.values()), return_exceptions=True)


def model_state():
    _, _, state = proxy.connection()
    if not state["profile"].get("tool_calling"):
        raise HTTPException(
            409,
            "当前模型未启用工具调用，请在启动预设中启用后重新启动 / Enable tool calling in the model profile first",
        )
    return state


def start(
    project_id,
    prompt,
    request_id,
    task_id=None,
    source_thread=None,
    operation="turn",
    permission=None,
    mode=None,
    thinking=None,
):
    if operation not in {"turn", "review", "compact", "skills"}:
        raise ValueError("Unsupported Agent operation")
    if permission not in {None, "workspace-write", "read-only"} or mode not in {
        None,
        "default",
        "plan",
    }:
        raise ValueError("Unsupported Agent mode or permission")
    if operation == "compact" and (not task_id or not get(task_id).get("thread_id")):
        raise ValueError("先开始一个任务，再压缩上下文 / Start a task before compacting context")
    project = projects.get(project_id)
    # A retry after a lost HTTP response must return the same task/turn.
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT id FROM records WHERE bucket='agent_tasks' "
            "AND json_extract(data,'$.project_id')=? AND EXISTS "
            "(SELECT 1 FROM json_each(records.data,'$.request_ids') WHERE value=?) LIMIT 1",
            (project_id, request_id),
        ).fetchone()
    signature = hashlib.sha256(
        json.dumps(
            [task_id, prompt, operation, permission, mode, thinking, source_thread],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    if existing:
        previous = get(existing[0])
        saved = previous.get("request_signatures", {}).get(request_id)
        if (task_id and previous["id"] != task_id) or (saved and saved != signature):
            raise HTTPException(
                409,
                "请求标识已用于不同操作 / Request ID was already used for a different operation",
            )
        return previous
    require_idle(project_id)
    if len(workers) >= 2:
        raise HTTPException(
            409, "最多同时运行两个 Agent 任务 / At most two Agent tasks may run at once"
        )
    component = runtime.info()
    if not component["installed"] or not component["sandbox_ready"]:
        raise HTTPException(
            409, "Agent 组件或执行沙箱尚未就绪 / Agent component or sandbox is not ready"
        )
    if operation == "skills":
        state = db.get("engine", "active", {})
        state = {
            **state,
            "port": state.get("port", 0),
            "profile": state.get("profile")
            or {"served_model_name": "local", "max_model_len": 32768},
        }
    else:
        state = model_state()
    now = time.time()
    if task_id:
        record = get(task_id)
        if record["project_id"] != project_id:
            raise HTTPException(409, "Task belongs to another project")
    else:
        record = {
            "id": db.uid(),
            "project_id": project_id,
            "title": prompt[:80],
            "created_at": now,
            "items": [],
            "request_ids": [],
            "approvals": [],
            "model_calls": 0,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "missing_calls": 0,
                "cache_missing_calls": 0,
            },
            "codex_version": runtime.VERSION,
            "source_thread": source_thread,
            "elapsed_s": 0,
        }
    if record["codex_version"] != runtime.VERSION:
        raise HTTPException(
            409,
            "请使用创建此任务的 Codex 版本继续 / Resume with the original Codex component version",
        )
    if operation != "skills":
        from ..model_controls import thinking_kwargs

        chosen_thinking = (
            thinking
            if thinking is not None
            else record.get(
                "thinking", state["profile"].get("default_sampling", {}).get("thinking")
            )
        )
        thinking_kwargs(state["profile"], chosen_thinking)
        record.update(
            model=state["profile"]["served_model_name"],
            profile_id=state.get("profile_id"),
            engine_instance=state.get("launch_id") or state.get("instance_id"),
            engine_identity=proxy.instance_identity(state),
            engine_port=state["port"],
            context_window=state["profile"].get("max_model_len", 32768),
            model_label=state["profile"].get("name", state["profile"]["served_model_name"]),
            thinking=chosen_thinking,
            plan=[],
            changes=[],
            diff="",
            file_error=None,
        )
    else:
        record.setdefault("model", state["profile"]["served_model_name"])
    record.update(
        state="starting",
        settled=False,
        updated_at=now,
        error=None,
        approvals=[],
        current_turn=None,
        operation=operation,
        live_metrics=None,
        permission=permission or record.get("permission", "workspace-write"),
        mode=mode or record.get("mode", "default"),
    )
    record.setdefault("request_signatures", {})[request_id] = signature
    record.setdefault("turn_count", sum(item["type"] == "userMessage" for item in record["items"]))
    record["execution_permission"] = (
        "read-only"
        if record["permission"] == "read-only"
        or record["mode"] == "plan"
        or operation in {"review", "compact", "skills"}
        else "workspace-write"
    )
    record["request_ids"].append(request_id)
    record["items"].append(
        {
            "id": db.uid(),
            "type": "userMessage" if operation == "turn" else "commandMessage",
            "text": prompt,
        }
    )
    if operation == "turn":
        record["turn_count"] = record.get("turn_count", 0) + 1
    db.put("agent_tasks", record["id"], record)
    run = Run(record, project, prompt)
    runs[record["id"]] = run
    worker = asyncio.create_task(run.execute())
    workers[record["id"]] = worker
    worker.add_done_callback(run.worker_finished)
    return get(record["id"])


async def steer(task_id, prompt, request_id, turn_id):
    run = runs.get(task_id)
    if not run:
        raise HTTPException(
            409,
            "当前轮次已结束，草稿已保留，请继续任务 / This turn ended; continue the task with your draft",
        )
    async with run.steer_lock:
        record = run.record
        entries = record.setdefault("steering", {})
        if request_id in entries:
            previous = entries[request_id]
            if previous["prompt"] != prompt or previous["turn_id"] != turn_id:
                raise HTTPException(409, "Request ID was used for different instructions")
            if previous["state"] == "accepted":
                return get(task_id)
            raise HTTPException(
                409,
                "补充要求的接收结果尚未确认，请先检查进展，避免重复发送 / Delivery is unconfirmed; check progress before resending",
            )
        if (
            record["state"] != "running"
            or run.cancel_requested
            or record.get("operation") != "turn"
            or not turn_id
            or turn_id != record.get("current_turn")
        ):
            raise HTTPException(
                409,
                "当前无法补充要求，草稿已保留 / Cannot add instructions to this turn; your draft is preserved",
            )
        entries[request_id] = {"prompt": prompt, "turn_id": turn_id, "state": "pending"}
        run.save()
        try:
            await run.rpc(
                "turn/steer",
                {
                    "threadId": record["thread_id"],
                    "expectedTurnId": turn_id,
                    "input": [{"type": "text", "text": prompt}],
                },
            )
        except (TimeoutError, ConnectionError, RuntimeError) as error:
            raise HTTPException(
                409,
                "补充要求接收结果未知，请检查进展后再操作 / Delivery is unconfirmed; check task progress before resending",
            ) from error
        except ValueError:
            # A native RPC rejection is definitive, unlike a connection timeout.
            entries.pop(request_id, None)
            run.save()
            raise
        item = {"id": db.uid(), "type": "userMessage", "text": prompt, "steered": True}
        record["items"].append(item)
        entries[request_id]["state"] = "accepted"
        run.emit({"type": "item", "item": item})
        run.save()
        return get(task_id)


async def cancel(task_id):
    record = get(task_id)
    run = runs.get(task_id)
    if not run or record["state"] not in ACTIVE or run.cancel_requested:
        return record
    run.cancel_requested = True
    run.status("stopping")
    if run.record.get("current_turn"):
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                run.rpc(
                    "turn/interrupt",
                    {"threadId": run.record["thread_id"], "turnId": run.record["current_turn"]},
                ),
                2,
            )
    worker = workers.get(task_id)
    if worker and not worker.done():
        worker.cancel()
    return get(task_id)


async def approve(task_id, approval_id, decision, answers=None):
    run = runs.get(task_id)
    if not run:
        raise HTTPException(409, "This approval is no longer active")
    async with run.approval_lock:
        return await _approve(run, task_id, approval_id, decision, answers)


async def _approve(run, task_id, approval_id, decision, answers):
    if run.record["state"] not in {"waiting", "running"} or run.cancel_requested:
        raise HTTPException(409, "This approval is no longer active")
    approval = next((a for a in run.record["approvals"] if a["id"] == approval_id), None)
    if not approval:
        raise HTTPException(409, "Approval expired or already answered")
    if approval["method"] == "item/tool/requestUserInput":
        questions = approval.get("params", {}).get("questions", [])
        expected = {q["id"] for q in questions}
        if decision != "accept" or not answers or set(answers) != expected:
            raise ValueError("请回答每个问题 / Answer each question before submitting")
        for answer in answers.values():
            values = answer.get("answers")
            if (
                set(answer) != {"answers"}
                or not values
                or any(not isinstance(v, str) or not v.strip() or len(v) > 10000 for v in values)
            ):
                raise ValueError(
                    "回复不能为空或超过 10,000 字 / Answers must be nonempty and at most 10,000 characters"
                )
        result = {"answers": answers}
    else:
        if decision not in {"accept", "decline", "cancel"}:
            raise ValueError("Invalid approval decision")
        result = {"decision": decision}
    await run.write({"id": approval["rpc_id"], "result": result})
    run.record["approvals"] = [a for a in run.record["approvals"] if a["id"] != approval_id]
    run.status("waiting" if run.record["approvals"] else "running")
    return get(task_id)


class Run:
    def __init__(self, record, project, prompt):
        self.record, self.project, self.prompt = record, project, prompt
        self.process = None
        self.pending = {}
        self.counter = 0
        self.write_lock = asyncio.Lock()
        self.approval_lock = asyncio.Lock()
        self.model_lock = asyncio.Lock()
        self.steer_lock = asyncio.Lock()
        self.done = asyncio.Event()
        self.changed = asyncio.Event()
        self.events = deque(maxlen=512)
        self.revision = 0
        self.saved = 0.0
        self.cancel_requested = False
        self.shutdown_requested = False
        self.bridge_clients = set()
        self.stderr = ""
        self.calls_this_turn = 0
        self.tool_started = {}
        self.tool_completed = set()
        self.record.setdefault(
            "timing_missing_calls",
            self.record.get("model_calls", 0) if not self.record.get("metrics") else 0,
        )
        self.record.setdefault(
            "metrics",
            {
                "llm_s": 0,
                "tool_s": 0,
                "tool_calls": 0,
                "ttft_s": 0,
                "ttft_count": 0,
                "decode_s": 0,
                "decode_tokens": 0,
                "decode_calls": 0,
            },
        )

    def worker_finished(self, worker):
        # asyncio can cancel a newly submitted coroutine before its try/finally
        # executes. Always settle the durable record and release that project.
        error = None if worker.cancelled() else worker.exception()
        if not self.record.get("settled"):
            state = (
                "interrupted"
                if self.shutdown_requested
                else "cancelled"
                if self.cancel_requested or worker.cancelled()
                else "failed"
            )
            self.record["approvals"] = []
            self.status(
                state, settled=True, error=str(error) if error else self.record.get("error")
            )
        if runs.get(self.record["id"]) is self:
            runs.pop(self.record["id"], None)
        if workers.get(self.record["id"]) is worker:
            workers.pop(self.record["id"], None)
        self.changed.set()

    def emit(self, event):
        self.revision += 1
        self.events.append((self.revision, copy.deepcopy(event)))
        self.changed.set()
        self.record["updated_at"] = time.time()
        if time.monotonic() - self.saved > 0.5:
            self.save()

    def save(self):
        db.put("agent_tasks", self.record["id"], self.record)
        self.saved = time.monotonic()

    def status(self, state, **fields):
        self.record.update(state=state, **fields)
        self.emit(
            {"type": "status", "state": state, "approvals": self.record["approvals"], **fields}
        )
        self.save()

    async def write(self, message):
        async with self.write_lock:
            if not self.process or self.process.returncode is not None:
                raise RuntimeError("Codex process has exited")
            self.process.stdin.write(json.dumps(message, ensure_ascii=False).encode() + b"\n")
            await self.process.stdin.drain()

    async def rpc(self, method, params):
        self.counter += 1
        id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[id] = future
        try:
            await self.write({"id": id, "method": method, "params": params})
            return await asyncio.wait_for(future, 30)
        finally:
            self.pending.pop(id, None)

    def item(self, item_id):
        return next((i for i in reversed(self.record["items"]) if i["id"] == item_id), None)

    async def ingest(self, message):
        if "method" not in message and "id" in message:
            future = self.pending.get(message["id"])
            if future and not future.done():
                if message.get("error"):
                    future.set_exception(
                        ValueError(message["error"].get("message", str(message["error"])))
                    )
                else:
                    future.set_result(message.get("result", {}))
            return
        method, params = message.get("method", ""), message.get("params") or {}
        if "id" in message:
            if method in {
                "item/commandExecution/requestApproval",
                "item/fileChange/requestApproval",
                "item/tool/requestUserInput",
            }:
                approval = {
                    "id": db.uid(),
                    "rpc_id": message["id"],
                    "method": method,
                    "params": params,
                }
                self.record["approvals"].append(approval)
                self.status("waiting")
            else:
                # No arbitrary app-server RPC surface or implicit permission escalation.
                result = (
                    {"permissions": {}, "scope": "turn"}
                    if method == "item/permissions/requestApproval"
                    else None
                )
                await self.write(
                    {
                        "id": message["id"],
                        **(
                            {"result": result}
                            if result
                            else {
                                "error": {
                                    "code": -32601,
                                    "message": "This integration does not enable this capability",
                                }
                            }
                        ),
                    }
                )
            return
        if params.get("threadId") and params["threadId"] != self.record.get("thread_id"):
            return
        if method in {"item/started", "item/completed"}:
            item = copy.deepcopy(params["item"])
            item["finished"] = method == "item/completed"
            if item["type"] == "userMessage":
                return
            if item["type"] in {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall"}:
                if method == "item/started":
                    self.tool_started.setdefault(item["id"], time.monotonic())
                elif item["id"] not in self.tool_completed:
                    self.tool_completed.add(item["id"])
                    began = self.tool_started.pop(item["id"], None)
                    if began is not None:
                        self.record["metrics"]["tool_s"] += time.monotonic() - began
                        self.record["metrics"]["tool_calls"] += 1
                        self.emit({"type": "metrics", "metrics": self.record["metrics"]})
            for field in ("aggregatedOutput", "text", "review"):
                if isinstance(item.get(field), str) and len(item[field]) > 512000:
                    item[field] = item[field][:512000] + "\n[Output truncated in activity view]"
            existing = self.item(item["id"])
            if existing is None:
                self.record["items"].append(item)
            else:
                existing.update(item)
                item = existing
            self.emit({"type": "item", "item": item})
        elif method in {
            "item/agentMessage/delta",
            "item/commandExecution/outputDelta",
            "item/reasoning/summaryTextDelta",
            "item/reasoning/textDelta",
            "item/plan/delta",
        }:
            id = params["itemId"]
            item = self.item(id)
            field = "aggregatedOutput" if "commandExecution" in method else "text"
            if item is None:
                item = {
                    "id": id,
                    "type": "reasoning"
                    if "reasoning" in method
                    else "plan"
                    if "/plan/" in method
                    else "agentMessage",
                }
                self.record["items"].append(item)
                self.emit({"type": "item", "item": dict(item)})
            delta = params.get("delta", "")
            room = max(0, 512000 - len(item.get(field, "")))
            delta = delta[:room]
            item[field] = item.get(field, "") + delta
            if delta:
                self.emit({"type": "delta", "id": id, "field": field, "delta": delta})
        elif method == "turn/started":
            self.record["current_turn"] = params["turn"]["id"]
            self.status("running")
            self.emit({"type": "turn", "current_turn": self.record["current_turn"]})
        elif method == "thread/tokenUsage/updated":
            # The latest context occupancy is distinct from accumulated billing usage.
            self.record["context_usage"] = params.get("tokenUsage")
            self.emit({"type": "context", "context_usage": self.record["context_usage"]})
        elif method == "turn/diff/updated":
            self.record["diff"] = params.get("diff", "")[: 1024 * 1024]
            self.emit({"type": "diff", "diff": self.record["diff"]})
        elif method == "turn/plan/updated":
            self.record["plan"] = params.get("plan", [])
            self.emit({"type": "plan", "plan": self.record["plan"]})
        elif method == "serverRequest/resolved":
            self.record["approvals"] = [
                a for a in self.record["approvals"] if a["rpc_id"] != params.get("requestId")
            ]
            if self.record["state"] == "waiting" and not self.record["approvals"]:
                self.status("running")
        elif method == "error":
            self.record["error"] = (params.get("error") or {}).get("message", "Codex error")
            self.emit({"type": "error", "error": self.record["error"]})
        elif method == "turn/completed":
            turn = params["turn"]
            error = (turn.get("error") or {}).get("message")
            state = {"completed": "completed", "interrupted": "cancelled"}.get(
                turn["status"], "failed"
            )
            self.record["approvals"] = []
            self.status(
                state, error=error or (self.record.get("error") if state == "failed" else None)
            )
            self.done.set()

    async def read_events(self):
        try:
            while line := await self.process.stdout.readline():
                await self.ingest(json.loads(line))
        except Exception as error:  # noqa: BLE001 — contain errors at the external-process boundary
            self.record["error"] = str(error)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("Codex connection ended"))
            if (
                self.record["state"] in ACTIVE
                and not self.cancel_requested
                and not self.shutdown_requested
            ):
                self.status(
                    "failed",
                    error=self.record.get("error")
                    or "Codex process exited: " + self.stderr[-2000:],
                )
            self.done.set()

    async def read_errors(self):
        while chunk := await self.process.stderr.read(8192):
            self.stderr = (self.stderr + chunk.decode(errors="replace"))[-16000:]

    async def bridge(self, reader, writer):
        current = asyncio.current_task()
        self.bridge_clients.add(current)
        sent = False
        try:
            async with self.model_lock:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
                lines = head.decode("ascii").split("\r\n")
                if lines[0].split()[:2] != ["POST", "/v1/responses"]:
                    raise ValueError("Only the task's Responses endpoint is available")
                headers = dict(line.lower().split(":", 1) for line in lines[1:] if ":" in line)
                length = int(headers.get("content-length", "0"))
                if (
                    not 0 < length <= 8 * 1024 * 1024
                    or headers.get("content-encoding", "identity").strip() != "identity"
                ):
                    raise ValueError("Invalid or compressed model request")
                body = json.loads(await asyncio.wait_for(reader.readexactly(length), 15))
                if body.get("model") != self.record["model"]:
                    raise ValueError("This task is bound to a different model")
                if self.calls_this_turn >= 64:
                    raise ValueError(
                        "本轮已达到 64 次模型调用，请检查进展后继续 / Turn reached its 64-call limit"
                    )
                self.calls_this_turn += 1
                payload, mapping = convert_input(body)
                writer.write(
                    b"HTTP/1.0 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n"
                )
                sent = True
                stream = ResponseStream(self.record["model"], mapping)
                for event in stream.start():
                    writer.write(event)
                await writer.drain()

                # Race the client disconnect against inference so a stopped
                # Codex HTTP call immediately releases the model request too.
                async def generate():
                    async for chunk in self.infer(payload):
                        for event in stream.ingest(chunk):
                            writer.write(event)
                        await writer.drain()
                    for event in stream.complete():
                        writer.write(event)
                    await writer.drain()

                generation = asyncio.create_task(generate())
                disconnected = asyncio.create_task(reader.read(1))
                try:
                    done, _ = await asyncio.wait(
                        [generation, disconnected], return_when=asyncio.FIRST_COMPLETED
                    )
                    if generation in done:
                        await generation
                    else:
                        generation.cancel()
                finally:
                    for task in (generation, disconnected):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(generation, disconnected, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 — contain errors at the external-process boundary
            with contextlib.suppress(OSError):
                if sent:
                    writer.write(stream.failure(error))
                else:
                    data = json.dumps(
                        {"error": {"message": str(error)[:2000], "type": "invalid_request_error"}}
                    ).encode()
                    writer.write(
                        b"HTTP/1.0 400 Bad Request\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n"
                        + data
                    )
                await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            self.bridge_clients.discard(current)

    async def infer(self, payload):
        base, headers, state = proxy.connection()
        if (
            state["port"] != self.record["engine_port"]
            or state.get("profile_id") != self.record["profile_id"]
            or (
                proxy.instance_identity(state) != tuple(self.record["engine_identity"])
                if self.record.get("engine_identity")
                else (state.get("launch_id") or state.get("instance_id"))
                != self.record.get("engine_instance")
            )
        ):
            raise ValueError(
                "模型已切换，请重新选择并继续任务 / The model instance changed; resume the task"
            )
        request_id, started = proxy.begin(
            self.record["model"],
            "agent",
            state["port"],
            expected_instance=proxy.instance_identity(state),
        )
        usage, first, error, status = {}, None, None, 499
        clock = time.monotonic()
        timing = TokenTiming(clock)
        last_live = clock
        from ..model_controls import thinking_kwargs

        payload = {
            **payload,
            "return_token_ids": True,
            "chat_template_kwargs": thinking_kwargs(state["profile"], self.record.get("thinking")),
        }
        self.record["model_calls"] += 1
        try:
            async with (
                httpx.AsyncClient(
                    timeout=httpx.Timeout(1800, connect=10), trust_env=False
                ) as client,
                client.stream(
                    "POST", base + "/v1/chat/completions", headers=headers, json=payload
                ) as response,
            ):
                status = response.status_code
                if status != 200:
                    data = (await response.aread()).decode(errors="replace")[:2000]
                    raise ValueError(f"Local model returned HTTP {status}: {data}")
                ended = False
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        ended = True
                        break
                    event = json.loads(data)
                    if event.get("error"):
                        raise ValueError(str(event["error"]))
                    if event.get("usage"):
                        usage = event["usage"]
                    now = time.monotonic()
                    timing.observe(event, now)
                    if now - last_live >= 0.25:
                        last_live = now
                        self.record["live_metrics"] = timing.live(now)
                        self.emit(
                            {
                                "type": "live",
                                "live_metrics": self.record["live_metrics"],
                                "model_calls": self.record["model_calls"],
                            }
                        )
                    if first is None and any(
                        c.get("delta", {}).get("content")
                        or c.get("delta", {}).get("tool_calls")
                        or c.get("delta", {}).get("reasoning_content")
                        or c.get("delta", {}).get("reasoning")
                        for c in event.get("choices", [])
                    ):
                        first = time.monotonic() - clock
                    yield event
                if not ended:
                    raise ValueError("Local inference stream ended unexpectedly")
        except asyncio.CancelledError:
            status, error = 499, "Agent request cancelled"
            raise
        except Exception as exc:
            if status == 200:
                status = 502
            error = str(exc)
            raise
        finally:
            elapsed = time.monotonic() - clock
            measured = timing.summary(usage) if status == 200 and not error else {}
            metrics = self.record["metrics"]
            metrics["llm_s"] += elapsed
            if measured.get("ttft_s") is not None:
                metrics["ttft_s"] += measured["ttft_s"]
                metrics["ttft_count"] += 1
            if measured.get("decode_s"):
                metrics["decode_s"] += measured["decode_s"]
                metrics["decode_tokens"] += timing.tokens - timing.first_tokens
                metrics["decode_calls"] += 1
            self.record["live_metrics"] = None
            proxy.finish(
                request_id,
                started,
                status,
                usage,
                first,
                error,
                elapsed=elapsed,
                metrics={
                    **measured,
                    "agent_task_id": self.record["id"],
                    "agent_project_id": self.project["id"],
                    "pending": False,
                    "decode_reason": None
                    if measured.get("decode_s")
                    else "Agent provider did not supply complete token timing",
                    "prefill_reason": "Agent provider did not supply attributable prefill timing",
                },
            )
            totals = self.record["usage"]
            if (
                usage.get("prompt_tokens") is not None
                and usage.get("completion_tokens") is not None
            ):
                totals["input_tokens"] += usage["prompt_tokens"]
                totals["output_tokens"] += usage["completion_tokens"]
            else:
                totals["missing_calls"] += 1
            cached = usage_cache(usage).get("cached_tokens")
            if cached is None:
                totals["cache_missing_calls"] += 1
            else:
                totals["cached_tokens"] += cached
            self.emit(
                {
                    "type": "usage",
                    "usage": dict(totals),
                    "model_calls": self.record["model_calls"],
                    "metrics": metrics,
                    "live_metrics": None,
                }
            )

    async def execute(self):
        server, readers = None, []
        started = time.monotonic()
        # Unix socket addresses have a 108-byte Linux limit; state paths may be longer.
        temporary = tempfile.TemporaryDirectory(prefix="onecat-agent-")
        socket_path = Path(temporary.name) / "model.sock"
        try:
            await asyncio.to_thread(self.snapshot_files)
            server = await asyncio.start_unix_server(self.bridge, path=str(socket_path))
            os.chmod(socket_path, 0o600)
            args, env = runtime.prepare(self.project, self.record, socket_path)
            self.process = await asyncio.create_subprocess_exec(
                *args,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=4 * 1024 * 1024,
            )
            readers = [
                asyncio.create_task(self.read_events()),
                asyncio.create_task(self.read_errors()),
            ]
            await self.rpc(
                "initialize",
                {
                    "clientInfo": {"name": "onecat_studio", "version": "0.4.0"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            await self.write({"method": "initialized", "params": {}})
            readonly = (
                self.record.get("permission") == "read-only"
                or self.record.get("mode") == "plan"
                or self.record.get("operation") in {"review", "skills", "compact"}
            )
            params = {
                "model": self.record["model"],
                "modelProvider": "onecat",
                "cwd": "/workspace",
                "approvalPolicy": "on-request",
                "sandbox": "read-only" if readonly else "workspace-write",
                "developerInstructions": "You are the project's coding agent in 1Cat Studio. Work in /workspace. "
                "The process is externally sandboxed: only this project is available, with no external network or GPUs. "
                "Inspect files, implement the task and run available tests. Report actual results and failures. "
                "Never claim commands succeeded without executing them. Do not modify .codex configuration. "
                "Use the user's language. Keep progress concise.",
            }
            if self.record.get("operation") != "skills":
                if self.record.get("thread_id"):
                    params["threadId"] = self.record["thread_id"]
                    result = await self.rpc("thread/resume", params)
                else:
                    result = await self.rpc("thread/start", params)
                self.record["thread_id"] = result["thread"]["id"]
                self.save()
            operation = self.record.get("operation", "turn")
            if operation == "skills":
                result = await self.rpc(
                    "skills/list", {"cwds": ["/workspace"], "forceReload": True}
                )
                entries = result.get("data", [])
                lines = [
                    f"- ${skill['name']}: {skill.get('description', '')}"
                    for entry in entries
                    for skill in entry.get("skills", [])
                    if skill.get("enabled", True)
                ]
                lines += [
                    f"- {error.get('path', '')}: {error.get('message', '')}"
                    for entry in entries
                    for error in entry.get("errors", [])
                ]
                item = {
                    "id": db.uid(),
                    "type": "agentMessage",
                    "finished": True,
                    "text": "\n".join(lines)
                    or "项目尚无可用技能。可在 .agents/skills/<name>/SKILL.md 添加，然后用 $name 调用。\nNo project skills found. Add .agents/skills/<name>/SKILL.md and invoke with $name.",
                }
                self.record["items"].append(item)
                self.emit({"type": "item", "item": item})
                self.status("completed")
                self.done.set()
            elif operation == "compact":
                await self.rpc("thread/compact/start", {"threadId": self.record["thread_id"]})
            elif operation == "review":
                await self.rpc(
                    "review/start",
                    {
                        "threadId": self.record["thread_id"],
                        "delivery": "inline",
                        "target": {
                            "type": "custom",
                            "instructions": self.prompt
                            if self.prompt != "/review"
                            else "Review this project's changes and code. Inspect files and available git diff. Report actionable bugs with file references; do not edit files.",
                        },
                    },
                )
            else:
                inputs = [{"type": "text", "text": self.prompt}]
                names = set(re.findall(r"(?<![\w$])\$([\w-]+)", self.prompt))
                if names:
                    available = await self.rpc(
                        "skills/list", {"cwds": ["/workspace"], "forceReload": True}
                    )
                    for group in available.get("data", []):
                        for skill in group.get("skills", []):
                            if (
                                skill["name"] in names
                                and skill.get("enabled", True)
                                and skill.get("path", "").startswith("/workspace/")
                            ):
                                inputs.append(
                                    {"type": "skill", "name": skill["name"], "path": skill["path"]}
                                )
                result = await self.rpc(
                    "turn/start",
                    {
                        "threadId": self.record["thread_id"],
                        "input": inputs,
                        "sandboxPolicy": {"type": "readOnly", "networkAccess": False}
                        if readonly
                        else {
                            "type": "workspaceWrite",
                            "writableRoots": ["/workspace"],
                            "networkAccess": False,
                        },
                        "approvalPolicy": "on-request",
                        "collaborationMode": {
                            "mode": self.record.get("mode", "default"),
                            "settings": {
                                "model": self.record["model"],
                                "reasoning_effort": None,
                                "developer_instructions": None,
                            },
                        },
                    },
                )
                self.record["current_turn"] = result["turn"]["id"]
            self.save()
            await asyncio.wait_for(self.done.wait(), 1800)
        except asyncio.CancelledError:
            self.record["approvals"] = []
            self.status(
                "interrupted" if self.shutdown_requested else "cancelled",
                error="Studio 已重启，可以继续 / Studio restarted; task can be resumed"
                if self.shutdown_requested
                else None,
            )
        except Exception as error:  # noqa: BLE001 — contain errors at the external-process boundary
            self.record["approvals"] = []
            detail = (
                str(error)
                or "任务达到 30 分钟上限，可检查进展后继续 / Task reached its 30-minute limit"
            )
            if self.process and self.process.returncode is not None:
                detail += "\n" + self.stderr[-2000:]
            self.status("failed", error=detail[:4000])
        finally:
            if self.process and self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(self.process.wait(), 3)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(self.process.pid, signal.SIGKILL)
                    await self.process.wait()
            if server:
                server.close()
                await server.wait_closed()
            for task in list(self.bridge_clients) + readers:
                task.cancel()
            await asyncio.gather(*list(self.bridge_clients), *readers, return_exceptions=True)
            self.record["elapsed_s"] += time.monotonic() - started
            self.emit({"type": "elapsed", "elapsed_s": self.record["elapsed_s"]})
            self.save()
            self.done.set()
            self.changed.set()
            temporary.cleanup()
            try:
                if self.record.get("operation") != "skills":
                    changes = await asyncio.to_thread(self.file_changes)
                    self.record.update(changes)
                    self.emit({"type": "changes", **changes})
            except (OSError, ValueError, HTTPException) as error:
                self.record["file_error"] = str(error)
            self.record["settled"] = True
            self.emit({"type": "status", "state": self.record["state"], "settled": True})
            self.save()
            workers.pop(self.record["id"], None)
            runs.pop(self.record["id"], None)

    def snapshot_files(self):
        listing = projects.files(self.project["id"])
        baseline = {}
        budget = 16 * 1024 * 1024
        for file in listing["items"]:
            if file["bytes"] > min(1024 * 1024, budget):
                baseline[file["path"]] = None
                continue
            data = projects.read(self.project["id"], file["path"])
            budget -= len(data)
            baseline[file["path"]] = data.decode("utf-8", errors="replace")
        self.baseline = baseline
        self.baseline_truncated = listing["truncated"]
        self.baseline_metadata = {f["path"]: (f["bytes"], f["modified"]) for f in listing["items"]}

    def file_changes(self):
        import difflib

        baseline = getattr(self, "baseline", {})
        listing = projects.files(self.project["id"])
        current = {f["path"]: f for f in listing["items"]}
        changes, diffs, budget = [], [], 1024 * 1024
        for path in sorted(set(baseline) | set(current)):
            if (path not in current and listing["truncated"]) or (
                path not in baseline and getattr(self, "baseline_truncated", False)
            ):
                continue  # An omitted file is not evidence of addition/deletion.
            before = baseline.get(path)
            after = None
            if (
                path in baseline
                and path in current
                and before is None
                and getattr(self, "baseline_metadata", {}).get(path)
                == (current[path]["bytes"], current[path]["modified"])
            ):
                continue
            if path in current and current[path]["bytes"] <= 1024 * 1024:
                after = projects.read(self.project["id"], path).decode("utf-8", errors="replace")
            if path in baseline and path in current and before == after:
                metadata = (current[path]["bytes"], current[path]["modified"])
                if (
                    before is not None
                    or getattr(self, "baseline_metadata", {}).get(path) == metadata
                ):
                    continue
            kind = (
                "added"
                if path not in baseline
                else "deleted"
                if path not in current
                else "modified"
            )
            changes.append({"path": path, "kind": kind})
            if budget > 0 and (before is not None or after is not None):
                diff = "".join(
                    difflib.unified_diff(
                        (before or "").splitlines(True),
                        (after or "").splitlines(True),
                        fromfile="a/" + path,
                        tofile="b/" + path,
                    )
                )[:budget]
                budget -= len(diff)
                diffs.append(diff)
        return {
            "changes": changes,
            "diff": "\n".join(diffs),
            "files_truncated": listing["truncated"] or getattr(self, "baseline_truncated", False),
        }
