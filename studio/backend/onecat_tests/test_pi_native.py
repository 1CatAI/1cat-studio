# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Real pinned OMP, sandbox and HTTP proxy against a deterministic CPU provider.

These integration checks do not establish GPU throughput or model quality.
"""
import asyncio
import json
import socket
from contextlib import asynccontextmanager

import pytest
import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse

from onecat import db, proxy
from onecat.agent import pi_runtime, projects, tasks
from onecat.decode_metrics import aggregator


@asynccontextmanager
async def serve(app):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        worker = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            for _ in range(100):
                if server.started:
                    break
                if worker.done():
                    await worker
                await asyncio.sleep(.02)
            assert server.started
            yield sock.getsockname()[1]
        finally:
            server.should_exit = True
            await asyncio.wait_for(worker, 10)


class Provider:
    def __init__(self):
        self.payloads = []
        self.child_active = 0
        self.max_child_active = 0
        self.expected_concurrency = 2
        self.children_overlap = asyncio.Event()
        self.spawned = False
        self.spawn = True
        self.spawn_via = "task"
        self.hold = False
        self.child_started = asyncio.Event()
        self.child_release = asyncio.Event()
        self.app = FastAPI()
        self.app.post("/v1/chat/completions")(self.complete)

    async def complete(self, request: Request):
        payload = await request.json()
        self.payloads.append(payload)
        user_text = "\n".join(str(m.get("content")) for m in payload["messages"] if m["role"] == "user")
        child = "PI_NATIVE_CHILD" in user_text
        delta = {"content": "Verified native PI response."}
        if not child and self.spawn and not self.spawned:
            self.spawned = True
            available = {tool["function"]["name"] for tool in payload.get("tools", [])}
            assert self.spawn_via in available, sorted(available)
            arguments = {"context": "Verify native delegation", "tasks": [
                {"agent": "task", "name": name, "task": "PI_NATIVE_CHILD " + name + ": reply without tools."}
                for name in ("alpha", "beta")
            ]}
            if self.spawn_via == "eval":
                arguments = {"language": "js", "code": "const hs = await Promise.all(['alpha', 'beta'].map(label => agent('PI_NATIVE_CHILD '+label, {agent: 'task', label}))); await Promise.all(hs.map(h => h.wait()));"}
            delta = {"tool_calls": [{"index": 0, "id": "native_spawn", "type": "function", "function": {
                "name": self.spawn_via, "arguments": json.dumps(arguments)}}]}

        async def stream():
            if child:
                self.child_active += 1
                self.max_child_active = max(self.max_child_active, self.child_active)
                if self.child_active >= self.expected_concurrency:
                    self.children_overlap.set()
                self.child_started.set()
            try:
                yield self.chunk({"role": "assistant", "content": ""})
                yield self.chunk(delta, ids=[1])
                if child:
                    if self.hold:
                        await self.child_release.wait()
                    else:
                        await asyncio.wait_for(self.children_overlap.wait(), 5)
                        await asyncio.sleep(.1)
                # The final output-bearing batch contributes two decode tokens.
                yield self.chunk({"content": " Done."} if "content" in delta else {}, ids=[2, 3])
                yield self.chunk({}, finish="tool_calls" if "tool_calls" in delta else "stop")
                yield b'data: {"choices":[],"usage":{"prompt_tokens":40,"completion_tokens":3,"total_tokens":43}}\n\n'
                yield b"data: [DONE]\n\n"
            finally:
                if child:
                    self.child_active -= 1
        return StreamingResponse(stream(), media_type="text/event-stream")

    @staticmethod
    def chunk(delta, ids=None, finish=None):
        choice = {"index": 0, "delta": delta, "finish_reason": finish}
        if ids is not None:
            choice["token_ids"] = ids
        return ("data: " + json.dumps({"id": "native-test", "object": "chat.completion.chunk", "created": 1,
                                      "model": "local", "choices": [choice]}) + "\n\n").encode()


@pytest.fixture
async def native_environment(monkeypatch):
    status = pi_runtime.info()
    if not status["ready"]:
        pytest.skip("Build pinned PI and install the sandbox to run native acceptance: " + str(status.get("error")))
    monkeypatch.setenv("ONECAT_AUTO_GPU_ACTIONS", "0")
    project = {"id": db.uid(), "name": "Native PI acceptance"}
    db.put("agent_projects", project["id"], project)
    projects.root(project["id"]).mkdir(parents=True)
    native = pi_runtime.state_root() / "agent/pi/config.yml"
    native.parent.mkdir(parents=True, exist_ok=True)
    # Explicit native settings used only by this fixture. Production must keep
    # project/PI concurrency, recursion and async policy authoritative.
    native.write_text(json.dumps({"eval": {"py": False, "js": False}, "task": {"batch": True}}))
    provider = Provider()

    async def empty(*args):
        return {}
    monkeypatch.setattr(proxy, "engine_metrics", empty)
    monkeypatch.setattr(proxy, "enrich", empty)
    from onecat.app import create_app
    async with serve(provider.app) as model_port, serve(create_app()) as studio_port:
        db.put("settings", "main", {"port": studio_port})
        db.put("engine", "active", {"state": "ready", "port": model_port, "profile_id": "native-test", "launch_id": "native-test",
               "profile": {"name": "Native fixture", "served_model_name": "local", "tool_calling": True,
                           "max_model_len": 32768, "max_num_seqs": 2, "gpu_uuids": []}})
        try:
            yield project, provider
        finally:
            provider.child_release.set()
            await tasks.shutdown()
            await asyncio.gather(*list(proxy.pending), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,capacity", [("single", 2), ("auto", 2), ("swarm", 1), ("swarm", 2)])
async def test_pinned_pi_native_delegation_capacity_and_resume(native_environment, mode, capacity):
    project, provider = native_environment
    provider.expected_concurrency = capacity
    state = db.get("engine", "active")
    state["profile"]["max_num_seqs"] = capacity
    db.put("engine", "active", state)
    record = tasks.start(project["id"], "Run the delegation acceptance fixture", "native-first", engine="pi", collaboration=mode)
    await asyncio.wait_for(tasks.workers[record["id"]], 90)
    result = tasks.get(record["id"])
    assert result["state"] == "completed", result.get("error")
    if mode == "single":
        assert not result["subagents"]
        assert provider.max_child_active == 0
    else:
        assert len(result["subagents"]) == 2, result["subagents"]
        assert all(child["status"] == "completed" for child in result["subagents"])
        assert provider.max_child_active == capacity
    assert result["model_calls"] >= 2
    assert any("Verified native PI response" in item.get("text", "") for item in result["items"])
    assert aggregator.snapshot()["active_requests"] == aggregator.snapshot()["waiting_requests"] == 0
    session_id = result["pi_session_id"]
    tasks.start(project["id"], "Continue the same session", "native-resume", task_id=result["id"], engine="pi", collaboration=mode)
    await asyncio.wait_for(tasks.workers[result["id"]], 60)
    resumed = tasks.get(result["id"])
    assert resumed["state"] == "completed", resumed.get("error")
    assert resumed["pi_session_id"] == session_id
    with db.connect() as conn:
        rows = conn.execute("SELECT status,source,metrics FROM requests").fetchall()
    assert all(row["status"] == 200 and row["source"] == "agent" for row in rows)
    assert all(json.loads(row["metrics"])["agent_engine"] == "pi" for row in rows)


@pytest.mark.asyncio
async def test_pinned_pi_stop_cancels_native_swarm_and_proxy_queue(native_environment):
    project, provider = native_environment
    provider.hold = True
    state = db.get("engine", "active")
    state["profile"]["max_num_seqs"] = 1
    db.put("engine", "active", state)
    result = tasks.start(project["id"], "Run the cancellation fixture", "native-cancel", engine="pi", collaboration="swarm")
    worker = tasks.workers[result["id"]]
    try:
        await asyncio.wait_for(provider.child_started.wait(), 60)
        for _ in range(100):
            if aggregator.snapshot()["waiting_requests"]:
                break
            await asyncio.sleep(.02)
        assert aggregator.snapshot()["waiting_requests"] >= 1
        await tasks.cancel(result["id"])
        await asyncio.wait_for(worker, 10)
        assert tasks.get(result["id"])["state"] == "cancelled"
        assert aggregator.snapshot()["active_requests"] == aggregator.snapshot()["waiting_requests"] == 0
        assert not proxy.agent_pending
    finally:
        provider.child_release.set()


@pytest.mark.asyncio
async def test_pinned_pi_lists_and_invokes_project_skill(native_environment):
    project, provider = native_environment
    skill = projects.root(project["id"]) / ".omp/skills/native-fixture/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: native-fixture\ndescription: Native skill acceptance\n---\nReply with a short confirmation.\n")
    record = tasks.start(project["id"], "/skills", "native-skills", engine="pi", operation="skills")
    await asyncio.wait_for(tasks.workers[record["id"]], 60)
    result = tasks.get(record["id"])
    assert result["state"] == "completed", result.get("error")
    assert not provider.payloads
    assert any("/skill:native-fixture" in item.get("text", "") for item in result["items"])
    provider.spawn = False
    tasks.start(project["id"], "/skill:native-fixture hello", "native-skill-call", task_id=record["id"], engine="pi")
    await asyncio.wait_for(tasks.workers[record["id"]], 60)
    result = tasks.get(record["id"])
    assert result["state"] == "completed", result.get("error")
    assert len(provider.payloads) == 1
    assert "Reply with a short confirmation." in json.dumps(provider.payloads[0]["messages"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["single", "swarm"])
async def test_pinned_pi_eval_agent_obeys_native_spawn_policy(native_environment, mode):
    project, provider = native_environment
    native = pi_runtime.state_root() / "agent/pi/config.yml"
    native.write_text(json.dumps({"eval": {"py": False, "js": True}, "task": {"batch": True}}))
    provider.spawn_via = "eval"
    record = tasks.start(project["id"], "Verify eval delegation", "native-eval", engine="pi", collaboration=mode)
    await asyncio.wait_for(tasks.workers[record["id"]], 90)
    result = tasks.get(record["id"])
    assert result["state"] == "completed", result.get("error")
    assert len(result["subagents"]) == (0 if mode == "single" else 2), result["subagents"]
    assert provider.max_child_active == (0 if mode == "single" else 2)
