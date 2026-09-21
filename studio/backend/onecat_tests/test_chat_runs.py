# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import asyncio
import json
import time

import pytest
from fastapi import HTTPException
from onecat import chat_runs, db, proxy
from starlette.responses import StreamingResponse
from onecat import chat_store as studio_db


def thread(id="chat"):
    now = int(time.time() * 1000)
    studio_db.upsert_chat_thread(
        {"id": id, "title": "Test", "modelType": "text", "createdAt": now, "updatedAt": now}
    )
    studio_db.sync_chat_messages(
        id,
        [
            {
                "id": id + "-user",
                "threadId": id,
                "role": "user",
                "parentId": None,
                "createdAt": now,
                "content": [{"type": "text", "text": "Write code"}],
            }
        ],
    )
    return id


def fixture(monkeypatch, *, length=20, done=True):
    calls = []

    async def forward(request, *args):
        calls.append(await request.json())

        async def events():
            for _ in range(length):
                await asyncio.sleep(0.01)
                yield (
                    "data: "
                    + json.dumps({"choices": [{"delta": {"content": "中🙂"}}]}, ensure_ascii=False)
                    + "\n\n"
                ).encode()
            yield (
                "data: "
                + json.dumps(
                    {
                        "usage": {"prompt_tokens": 10, "completion_tokens": length},
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                    }
                )
                + "\n\n"
            ).encode()
            if done:
                yield b"data: [DONE]\n\n"

        return StreamingResponse(events(), headers={"X-Request-ID": "measured-request"})

    monkeypatch.setattr(proxy, "forward", forward)
    return calls


def test_disconnect_reconnect_does_not_restart_or_cancel_and_preserves_usage(monkeypatch):
    calls = fixture(monkeypatch)
    id = thread()

    async def scenario():
        chat_runs.recover()
        payload = {"message_id": "answer", "request": {"messages": []}}
        first = chat_runs.start(id, payload)
        assert chat_runs.start(id, payload)["id"] == first["id"]
        with pytest.raises(HTTPException) as busy:
            chat_runs.start(id, {**payload, "message_id": "duplicate"})
        assert busy.value.status_code == 409
        observer = chat_runs.subscribe(id).body_iterator
        assert "onecat_snapshot" in await anext(observer)
        await observer.aclose()
        await asyncio.sleep(0.05)
        assert chat_runs.current(id)["state"] == "running"
        resumed = [event async for event in chat_runs.subscribe(id).body_iterator]
        assert "中🙂" in resumed[0]
        assert resumed[-1] == "data: [DONE]\n\n"
        assert chat_runs.current(id)["state"] == "completed"
        assert len(calls) == 1
        messages = studio_db.list_chat_messages(id)
        assert len(messages) == 2
        assert messages[-1]["content"] == [{"type": "text", "text": "中🙂" * 20}]
        assert messages[-1]["metadata"]["usage"]["completion_tokens"] == 20
        chat_runs.recover()
        recovered = [event async for event in chat_runs.subscribe(id).body_iterator]
        assert "中🙂" * 20 in recovered[0]

    asyncio.run(scenario())


def test_explicit_cancel_keeps_partial_output_and_allows_retry(monkeypatch):
    fixture(monkeypatch, length=1000)
    id = thread()

    async def scenario():
        chat_runs.recover()
        chat_runs.start(id, {"message_id": "answer", "request": {}})
        await asyncio.sleep(0.05)
        await chat_runs.cancel(id)
        assert chat_runs.current(id)["state"] == "cancelled"
        assert studio_db.list_chat_messages(id)[-1]["content"]
        assert not chat_runs.active()
        chat_runs.start(id, {"message_id": "retry", "request": {}})
        await chat_runs.cancel(id)  # Also cover cancellation before task starts.
        assert chat_runs.current(id)["state"] == "cancelled"

    asyncio.run(scenario())


def test_early_end_is_failure_and_restart_explains_interruption(monkeypatch):
    fixture(monkeypatch, length=2, done=False)
    id = thread()

    async def scenario():
        chat_runs.recover()
        chat_runs.start(id, {"message_id": "answer", "request": {}})
        _ = [event async for event in chat_runs.subscribe(id).body_iterator]
        assert chat_runs.current(id)["state"] == "failed"
        assert studio_db.list_chat_messages(id)[-1]["content"]
        db.patch("chat_runs", id, {"state": "running"})
        chat_runs.recover()
        assert chat_runs.current(id)["state"] == "failed"
        assert "重启" in chat_runs.current(id)["error"]

    asyncio.run(scenario())


def test_completion_marker_does_not_wait_for_transport_close(monkeypatch):
    id = thread()
    closed = []

    async def forward(*args):
        async def events():
            try:
                yield 'data: {"choices":[{"delta":{"content":"Final answer"}}]}\n\n'
                yield 'data: {"usage":{"completion_tokens":42}}\n\n'
                yield "data: [DONE]\n\n"
                await asyncio.Future()  # The transport remains open after completion.
            finally:
                closed.append(True)

        return StreamingResponse(events())

    monkeypatch.setattr(proxy, "forward", forward)

    async def scenario():
        chat_runs.recover()
        chat_runs.start(id, {"message_id": "answer", "request": {}})
        worker = chat_runs._tasks[id]
        await asyncio.wait_for(asyncio.shield(worker), 1)
        assert closed == [True]
        assert chat_runs.current(id)["state"] == "completed"
        saved = studio_db.get_chat_message(id, "answer")
        assert saved["metadata"]["usage"]["completion_tokens"] == 42
        assert saved["metadata"]["generation_state"] == "completed"

    asyncio.run(scenario())


def test_status_only_transfers_answer_after_durable_completion(monkeypatch):
    fixture(monkeypatch, length=2)
    id = thread()

    async def scenario():
        chat_runs.recover()
        record = chat_runs.start(id, {"message_id": "answer", "request": {}})
        assert chat_runs.status(id) == {"run": record, "message": None}
        await chat_runs._tasks[id]
        # The fallback must work after in-memory observers have gone away.
        chat_runs._runs.clear()
        result = chat_runs.status(id)
        assert result["run"]["id"] == record["id"]
        assert result["run"]["state"] == "completed"
        assert result["message"]["id"] == "answer"
        assert result["message"]["content"] == [{"type": "text", "text": "中🙂" * 2}]
        assert result["message"]["metadata"]["usage"]["completion_tokens"] == 2

    asyncio.run(scenario())


def test_status_endpoint_requires_admin_and_reports_unknown_run(client):
    assert client.get("/api/chat/threads/missing/generation").status_code == 404
    record = {"id": "generation", "state": "running", "thread_id": "chat", "message_id": "answer"}
    db.put("chat_runs", "chat", record)
    result = client.get("/api/chat/threads/chat/generation")
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.json() == {"run": record, "message": None}
    client.cookies.clear()
    assert client.get("/api/chat/threads/chat/generation").status_code == 401
