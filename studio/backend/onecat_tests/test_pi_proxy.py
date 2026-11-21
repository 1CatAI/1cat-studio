# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from onecat import db, proxy
from onecat.agent import pi_runtime, tasks
from onecat.decode_metrics import aggregator


@pytest.fixture
def parent(monkeypatch):
    state = {"state": "ready", "port": 1234, "profile_id": "p", "launch_id": "one",
             "profile": {"name": "Local", "served_model_name": "local", "gpu_uuids": [], "max_num_seqs": 2}}
    db.put("engine", "active", state)
    record = {"id": "parent", "project_id": "project", "state": "running", "items": [], "approvals": [],
              "model_calls": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
              "missing_calls": 0, "cache_missing_calls": 0}}
    run = tasks.PiRun(record, {"id": "project"}, "test")
    tasks.runs["parent"] = run
    async def empty(*args):
        return {}
    monkeypatch.setattr(proxy, "engine_metrics", empty)
    monkeypatch.setattr(proxy, "enrich", empty)
    token = pi_runtime.issue_model_token("parent", "project", "swarm", engine_identity=proxy.instance_identity(state))
    yield token, run
    pi_runtime.revoke_model_token(token)
    tasks.runs.pop("parent", None)
    proxy.agent_pending.clear()


def test_internal_bearer_scope_instance_binding_and_request_attribution(parent, monkeypatch):
    token, run = parent
    original = httpx.AsyncClient
    async def handler(request):
        assert request.headers.get("authorization") != "Bearer " + token
        return httpx.Response(200, json={"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}})
    monkeypatch.setattr(proxy.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    from onecat.app import create_app
    client = TestClient(create_app())
    data = {"model": "local", "messages": [], "stream": False}
    assert client.post("/internal/agent/v1/chat/completions", json=data).status_code == 401
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/agent/status", headers=headers).status_code == 401
    response = client.post("/internal/agent/v1/chat/completions", headers=headers, json=data)
    assert response.status_code == 200
    with db.connect() as conn:
        row = conn.execute("SELECT source,metrics FROM requests").fetchone()
    assert row[0] == "agent"
    metrics = json.loads(row[1])
    assert metrics["agent_engine"] == "pi"
    assert metrics["agent_task_id"] == "parent" and metrics["swarm_id"] == "swarm"
    assert metrics["queue_s"] >= 0
    assert run.record["model_calls"] == 1 and run.record["usage"]["output_tokens"] == 2
    db.patch("engine", "active", {"launch_id": "replacement"})
    assert client.post("/internal/agent/v1/chat/completions", headers=headers, json=data).status_code == 409
    pi_runtime.revoke_model_token(token)
    assert client.post("/internal/agent/v1/chat/completions", headers=headers, json=data).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("capacity", [1, 2])
async def test_proxy_streams_queue_or_overlap_at_model_capacity(parent, monkeypatch, capacity):
    token, _ = parent
    state = db.get("engine", "active")
    state["profile"]["max_num_seqs"] = capacity
    db.put("engine", "active", state)
    release = asyncio.Event()
    arrived = 0
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal arrived
            arrived += 1
            yield b'data: {"choices":[{"index":0,"token_ids":[1],"delta":{"content":"a"}}]}\n\n'
            await release.wait()
            yield b'data: {"choices":[{"index":0,"token_ids":[2,3],"delta":{"content":"bc"}}]}\n\n'
            yield b'data: {"usage":{"prompt_tokens":5,"completion_tokens":3},"choices":[]}\n\ndata: [DONE]\n\n'
    original = httpx.AsyncClient
    async def handler(request):
        return httpx.Response(200, stream=Stream())
    monkeypatch.setattr(proxy.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    async def invoke():
        async def receive():
            return {"type": "http.request", "body": json.dumps({"model": "local", "stream": True, "messages": []}).encode()}
        request = Request({"type": "http", "method": "POST", "headers": []}, receive)
        request.state.onecat_agent_context = pi_runtime.model_context(token)
        response = await proxy.forward(request, "/v1/chat/completions")
        return b"".join([data async for data in response.body_iterator])
    calls = [asyncio.create_task(invoke()), asyncio.create_task(invoke())]
    for _ in range(100):
        if arrived == capacity:
            break
        await asyncio.sleep(.005)
    assert arrived == capacity
    measured = aggregator.snapshot(2, "parent")
    assert measured["active_requests"] == capacity
    assert measured["waiting_requests"] == 2 - capacity
    release.set()
    responses = await asyncio.wait_for(asyncio.gather(*calls), 2)
    assert all(b"[DONE]" in response for response in responses)
    assert aggregator.snapshot(2)["decode_tokens_s"] == 2
    assert aggregator.snapshot(2)["active_requests"] == 0
    with db.connect() as conn:
        measured = [json.loads(r[0]) for r in conn.execute("SELECT metrics FROM requests ORDER BY started")]
    assert measured[1]["queue_s"] > 0 if capacity == 1 else measured[1]["queue_s"] == 0
    assert measured[1]["elapsed_s"] >= measured[1]["queue_s"]
    assert measured[1]["ttft_s"] >= measured[1]["queue_s"]
    await asyncio.gather(*list(proxy.pending))


@pytest.mark.asyncio
async def test_queued_disconnect_and_parent_cancel_release_capacity(parent, monkeypatch):
    token, _ = parent
    state = db.get("engine", "active")
    state["profile"]["max_num_seqs"] = 1
    db.put("engine", "active", state)
    upstream_started, upstream_closed = asyncio.Event(), asyncio.Event()
    original = httpx.AsyncClient

    async def handler(request):
        upstream_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            upstream_closed.set()

    monkeypatch.setattr(proxy.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))

    async def invoke(disconnect):
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": b'{"model":"local","stream":true,"messages":[]}'}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        request = Request({"type": "http", "method": "POST", "headers": []}, receive)
        request.state.onecat_agent_context = pi_runtime.model_context(token)
        await proxy.forward(request, "/v1/chat/completions")

    active = asyncio.create_task(invoke(asyncio.Event()))
    await asyncio.wait_for(upstream_started.wait(), 2)
    disconnect = asyncio.Event()
    queued = asyncio.create_task(invoke(disconnect))
    for _ in range(100):
        if aggregator.snapshot()["waiting_requests"]:
            break
        await asyncio.sleep(.005)
    assert aggregator.snapshot()["waiting_requests"] == 1
    disconnect.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(queued, 2)
    assert aggregator.snapshot()["waiting_requests"] == 0
    assert aggregator.snapshot()["active_requests"] == 1
    await asyncio.wait_for(proxy.cancel_agent("parent"), 2)
    assert active.cancelled() and upstream_closed.is_set()
    assert aggregator.snapshot()["active_requests"] == 0
    assert not proxy.agent_pending
    with db.connect() as conn:
        assert [row[0] for row in conn.execute("SELECT status FROM requests")] == [499, 499]
    await asyncio.gather(*list(proxy.pending))


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["codex", "pi"])
async def test_agent_history_records_prefill_and_power(agent, monkeypatch):
    import time
    from collections import deque

    from onecat import telemetry
    from onecat.app import create_app

    state = {"state": "ready", "port": 1234, "profile_id": "p", "launch_id": "one",
             "profile": {"name": "Local", "served_model_name": "local", "gpu_uuids": ["gpu"]}}
    db.put("engine", "active", state)
    record = {"id": "task", "project_id": "project", "engine_port": 1234, "profile_id": "p",
              "engine_instance": "one", "model": "local", "model_calls": 0, "usage": {
                  "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "missing_calls": 0,
                  "cache_missing_calls": 0}}
    run = (tasks.Run if agent == "codex" else tasks.PiRun)(record, {"id": "project"}, "test")
    calls = 0

    async def handler(request):
        nonlocal calls
        if request.url.path == "/metrics":
            stats = {"request_prompt_tokens": 10, "request_generation_tokens": 3,
                     "request_prefill_time_seconds": .2, "request_decode_time_seconds": .1}
            return httpx.Response(200, text="\n".join(
                f'vllm:{name}_{suffix}{{model_name="local"}} {calls * value}'
                for name, total in stats.items() for suffix, value in [("count", 1), ("sum", total)]
            ))
        assert request.url.path == "/v1/chat/completions"
        calls += 1
        return httpx.Response(200, text=(
            'data: {"choices":[{"delta":{"content":"abc"},"token_ids":[1,2,3]}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":3,'
            '"prompt_tokens_details":{"cached_tokens":2}}}\n\ndata: [DONE]\n\n'
        ))

    # Bracket the request with constant 80 W samples on its assigned board.
    now = time.monotonic()
    monkeypatch.setattr(telemetry, "samples", deque(
        {"monotonic": now + i / 10, "gpus": [{"uuid": "gpu", "power_w": 80}]}
        for i in range(-5, 21)
    ))
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    body = {"model": "local", "messages": [], "stream": True, "stream_options": {"include_usage": True}}
    if agent == "codex":
        events = [event async for event in run.infer(body)]
        assert events[-1]["usage"]["prompt_tokens"] == 10
    else:
        monkeypatch.setitem(tasks.runs, "task", run)
        token = pi_runtime.issue_model_token("task", "project", None, engine_identity=proxy.instance_identity(state))
        try:
            async with original(transport=httpx.ASGITransport(app=create_app()), base_url="http://local") as client:
                response = await client.post("/internal/agent/v1/chat/completions", json=body,
                                             headers={"Authorization": "Bearer " + token})
                assert response.is_success and "[DONE]" in response.text
        finally:
            pi_runtime.revoke_model_token(token)
    await asyncio.gather(*list(proxy.pending))
    with db.connect() as conn:
        row = conn.execute("SELECT source,status,elapsed,metrics FROM requests").fetchone()
    metrics = json.loads(row["metrics"])
    assert (row["source"], row["status"], metrics["agent_engine"]) == ("agent", 200, agent)
    assert metrics["pending"] is False and metrics["prefill_tokens_s"] == 40
    assert metrics["average_gpu_w"] == pytest.approx(80)
    assert metrics["energy_wh"] == pytest.approx(80 * row["elapsed"] / 3600)
