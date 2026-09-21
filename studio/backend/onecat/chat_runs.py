# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Browser-independent chat runs with bounded stream replay and durable messages."""

from __future__ import annotations

import asyncio
import codecs
import copy
import json
import hashlib
import time
from collections import deque

from fastapi import HTTPException, Request
from starlette.responses import StreamingResponse

from . import db, proxy

_runs: dict[str, Run] = {}
_tasks: dict[str, asyncio.Task] = {}


def current(thread: str) -> dict | None:
    return db.get("chat_runs", thread)


def active() -> list[dict]:
    return [r for r in db.all_records("chat_runs") if r["state"] == "running"]


def status(thread: str) -> dict:
    """Small independent completion check when the browser misses the SSE tail."""
    record = current(thread)
    if not record:
        raise HTTPException(404, "Generation not found")
    message = None
    if record["state"] != "running":
        from onecat import chat_store as studio_db

        from .chat_metrics import hydrate

        saved = studio_db.get_chat_message(thread, record["message_id"])
        message = hydrate(saved) if saved else None
        if message is None and record.get("pending_commit"):
            message = {"id": record["message_id"], "threadId": thread, "role": "assistant", "content": [],
                       "createdAt": int(record["started_at"] * 1000), "metadata": {"generation_state":record["state"]}}
    return {"run": record, "message": message}


def require_idle(thread: str):
    if (current(thread) or {}).get("state") == "running":
        raise HTTPException(409, "请先停止当前生成 / Stop the current generation first")


def recover():
    from onecat import chat_store as studio_db

    _runs.clear()
    _tasks.clear()
    for record in active():
        error = "服务管理器已重启，已保留生成内容 / Studio restarted; partial output was saved"
        record.update(state="failed", error=error, updated_at=time.time())
        db.put("chat_runs", record["thread_id"], record)
        for message in studio_db.list_chat_messages(record["thread_id"]):
            if message["id"] == record["message_id"]:
                message["metadata"] = {
                    **(message.get("metadata") or {}),
                    "generation_state": "failed",
                    "finish_reason": "error",
                    "error": error,
                }
                studio_db.sync_chat_messages(record["thread_id"], [message])


async def shutdown():
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel("Studio is restarting")
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


class Run:
    def __init__(self, record: dict, message: dict, submission=None):
        self.record, self.message = record, message
        self.submission = submission
        self.events = deque(maxlen=512)
        self.revision = 0
        self.changed = asyncio.Event()
        self.answer = ""
        self.thought = ""
        self.thinking_started: float | None = None

    def emit(self, event: dict):
        self.revision += 1
        self.events.append((self.revision, event))
        self.changed.set()

    def snapshot(self):
        return {
            "onecat_snapshot": {"message": copy.deepcopy(self.message), "run": dict(self.record)}
        }

    def save(self):
        from onecat import chat_store as studio_db

        from .chat_metrics import hydrate

        if self.record.get("pending_commit"):
            db.put("chat_runs", self.record["thread_id"], self.record)
            return
        message = hydrate(copy.deepcopy(self.message))
        studio_db.sync_chat_messages(self.record["thread_id"], [message])
        studio_db.update_chat_thread(
            self.record["thread_id"], {"updatedAt": int(time.time() * 1000)}
        )
        db.put("chat_runs", self.record["thread_id"], self.record)

    def ingest(self, event: dict):
        metadata = self.message["metadata"]
        if event.get("request_id"):
            metadata["request_id"] = event["request_id"]
        if event.get("usage"):
            metadata["usage"] = event["usage"]
        if event.get("onecat_live_metrics"):
            metadata["live"] = event["onecat_live_metrics"]
        if event.get("onecat_metrics"):
            metadata["timing"] = event["onecat_metrics"]
        for choice in event.get("choices", []):
            delta = choice.get("delta") or {}
            thought = delta.get("reasoning_content") or delta.get("reasoning") or ""
            if thought and self.thinking_started is None:
                self.thinking_started = time.time()
                metadata["thinking_started_at"] = self.thinking_started
            self.thought += thought
            self.answer += delta.get("content") or ""
            if self.answer and self.thinking_started and "thinking_elapsed_s" not in metadata:
                metadata["thinking_elapsed_s"] = time.time() - self.thinking_started
            if choice.get("finish_reason"):
                metadata["finish_reason"] = choice["finish_reason"]
        self.message["content"] = (
            [{"type": "reasoning", "text": self.thought}] if self.thought else []
        ) + ([{"type": "text", "text": self.answer}] if self.answer else [])
        self.record["updated_at"] = time.time()
        if self.record.get("pending_commit") and self.message["content"]:
            from onecat import chat_store as studio_db

            studio_db.sync_chat_messages(
                self.record["thread_id"], self.submission["messages"], prune_missing=True
            )
            studio_db.update_chat_thread(
                self.record["thread_id"], {"settings": self.submission["settings"]}
            )
            self.record["pending_commit"] = False
            self.save()
            # This snapshot includes the delta above; do not emit it twice.
            self.emit(
                {"onecat_run": dict(self.record), "onecat_message": copy.deepcopy(self.message)}
            )
        else:
            self.emit(event)

    async def execute(self, request: Request):
        response = None
        try:
            response = await proxy.forward(request, "/v1/chat/completions", "studio")
            if response.status_code >= 400:
                data = json.loads(response.body)
                raise ValueError(str(data.get("error", data)))
            self.message["metadata"]["request_id"] = response.headers.get("X-Request-ID")
            self.emit({"request_id": response.headers.get("X-Request-ID")})
            buffer = ""
            decoder = codecs.getincrementaldecoder("utf-8")()
            done = False
            last_save = time.monotonic()
            async for block in response.body_iterator:
                buffer += decoder.decode(block) if isinstance(block, bytes) else block
                buffer = buffer.replace("\r\n", "\n")
                while "\n\n" in buffer:
                    event, buffer = buffer.split("\n\n", 1)
                    data = "\n".join(
                        line[5:].lstrip() for line in event.splitlines() if line.startswith("data:")
                    )
                    if not data:
                        continue
                    if data == "[DONE]":
                        done = True
                        break
                    value = json.loads(data)
                    if value.get("error"):
                        error = value["error"]
                        raise ValueError(
                            error.get("message", str(error))
                            if isinstance(error, dict)
                            else str(error)
                        )
                    self.ingest(value)
                if done:
                    break
                if time.monotonic() - last_save >= 1:
                    self.save()
                    last_save = time.monotonic()
            if not done:
                raise ValueError(
                    "推理连接提前结束，已保留部分回复 / Inference stream ended early; partial output saved"
                )
            if self.record.get("pending_commit"):
                raise ValueError(
                    "模型未返回内容，原对话已保留 / No content returned; original conversation preserved"
                )
            self.record["state"] = "completed"
        except asyncio.CancelledError as error:
            self.record.update(state="cancelled", error=str(error) or None)
            self.message["metadata"]["finish_reason"] = "cancelled"
        except Exception as error:
            detail = getattr(error, "detail", str(error))
            self.record.update(state="failed", error=str(detail))
            self.message["metadata"].update(finish_reason="error", error=str(detail))
        finally:
            if response is not None and hasattr(response, "body_iterator"):
                await response.body_iterator.aclose()
            self.record["updated_at"] = time.time()
            self.message["metadata"]["generation_state"] = self.record["state"]
            self.message["metadata"].setdefault("finish_reason", "stop")
            self.save()
            self.emit({"onecat_run": self.record, "onecat_message": self.message})


def start(thread: str, payload: dict) -> dict:
    from onecat import chat_store as studio_db

    if not studio_db.get_chat_thread(thread):
        raise HTTPException(404, "Conversation not found")
    message_id = payload.get("message_id")
    body = payload.get("request")
    if (
        not isinstance(message_id, str)
        or not 1 <= len(message_id) <= 128
        or not isinstance(body, dict)
    ):
        raise ValueError("A message_id and request object are required")
    previous = current(thread)
    signature = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    if previous and previous["message_id"] == message_id:
        if previous.get("request_signature") and previous["request_signature"] != signature:
            raise HTTPException(409, "Generation ID was already used for a different request")
        return previous
    require_idle(thread)
    messages = studio_db.list_chat_messages(thread)
    if any(m["id"] == message_id for m in messages):
        raise HTTPException(409, "Message already exists")
    submission = None
    if "messages" in payload:
        from .attachments import validate_content
        from .chat_settings import normalize

        proposed = copy.deepcopy(payload["messages"])
        if not isinstance(proposed, list) or len(proposed) > 10000:
            raise ValueError("Invalid message list")
        seen = set()
        for item in proposed:
            if not isinstance(item, dict) or item.get("role") not in {
                "user",
                "assistant",
                "system",
            }:
                raise ValueError("Invalid message role")
            if (
                not isinstance(item.get("id"), str)
                or not item["id"]
                or item["id"] in seen
                or not isinstance(item.get("content"), list)
            ):
                raise ValueError("Messages require unique IDs and content arrays")
            seen.add(item["id"])
            validate_content(item["content"])
            item["threadId"] = thread
        if payload.get("expected_message_ids") != [item["id"] for item in messages]:
            raise HTTPException(
                409,
                "对话已有更新，请刷新后重试；草稿已保留 / Conversation changed; refresh and retry with your saved draft",
            )
        submission = {"messages": proposed, "settings": normalize(payload.get("settings", {}))}
        messages = proposed
    body = {**body, "stream": True, "return_token_ids": True}
    record = {
        "id": db.uid(),
        "thread_id": thread,
        "message_id": message_id,
        "state": "running",
        "started_at": time.time(),
        "updated_at": time.time(),
        "error": None,
        "request_signature": signature,
        "pending_commit": submission is not None,
    }
    message = {
        "id": message_id,
        "threadId": thread,
        "parentId": messages[-1]["id"] if messages else None,
        "role": "assistant",
        "content": [],
        "createdAt": max(
            int(time.time() * 1000), max((m.get("createdAt", 0) for m in messages), default=0)
        )
        + 1,
        "metadata": {"generation_state": "running"},
    }
    run = Run(record, message, submission)
    run.save()
    # The proxy request is owned by this task; the browser connection only subscribes.
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [
                (b"x-onecat-thread-id", thread.encode()),
                (b"x-onecat-message-id", message_id.encode()),
            ],
        }
    )
    request._json = body
    for key in list(_runs):
        if len(_runs) >= 16 and _runs[key].record["state"] != "running":
            del _runs[key]
    _runs[thread] = run
    task = asyncio.create_task(run.execute(request))
    _tasks[thread] = task
    task.add_done_callback(
        lambda task: _tasks.pop(thread, None) if _tasks.get(thread) is task else None
    )
    return record


async def cancel(thread: str):
    task = _tasks.get(thread)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        run = _runs.get(thread)
        if run and run.record["state"] == "running":
            run.record.update(state="cancelled", updated_at=time.time())
            run.message["metadata"].update(generation_state="cancelled", finish_reason="cancelled")
            run.save()
            run.emit({"onecat_run": run.record, "onecat_message": run.message})
    return current(thread)


def subscribe(thread: str):
    if not current(thread):
        raise HTTPException(404, "Generation not found")

    def event(data):
        return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"

    async def stream():
        from onecat import chat_store as studio_db

        from .chat_metrics import hydrate

        run = _runs.get(thread)
        if not run:
            record = current(thread)
            message = next(
                (
                    m
                    for m in studio_db.list_chat_messages(thread)
                    if m["id"] == record["message_id"]
                ),
                None,
            )
            yield event(
                {
                    "onecat_snapshot": {
                        "message": hydrate(message) if message else {"id":record["message_id"], "threadId":thread, "role":"assistant", "content":[], "createdAt":int(record["started_at"]*1000), "metadata":{"generation_state":record["state"]}},
                        "run": record,
                    }
                }
            )
            yield "data: [DONE]\n\n"
            return
        revision = run.revision
        yield event(run.snapshot())
        while True:
            if run.events and revision < run.events[0][0] - 1:
                revision = run.revision
                yield event(run.snapshot())
            for sequence, value in list(run.events):
                if sequence > revision:
                    revision = sequence
                    yield event(value)
            if run.record["state"] != "running" and revision == run.revision:
                yield "data: [DONE]\n\n"
                return
            run.changed.clear()
            try:
                await asyncio.wait_for(run.changed.wait(), 10)
            except TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
