# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest
import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from onecat import db
from onecat.agent import pi_runtime, projects, runtime, tasks
from onecat.agent.api import NewTask


@pytest.fixture
def project():
    record = {"id": db.uid(), "name": "PI contract"}
    db.put("agent_projects", record["id"], record)
    projects.root(record["id"]).mkdir(parents=True)
    db.put("engine", "active", {"state": "ready", "port": 1234, "profile_id": "local", "launch_id": "one",
           "profile": {"name": "Local", "served_model_name": "local", "tool_calling": True,
                       "max_model_len": 32768, "max_num_seqs": 2, "gpu_uuids": []}})
    return record


def test_token_scope_expiry_and_revocation(monkeypatch):
    now = pi_runtime.time.time()
    token = pi_runtime.issue_model_token("task", "project", "swarm", engine_identity=["one"])
    assert pi_runtime.model_context(token)["task_id"] == "task"
    assert pi_runtime.model_context(token)["engine_identity"] == ["one"]
    monkeypatch.setattr(pi_runtime.time, "time", lambda: now + 3601)
    assert pi_runtime.model_context(token) is None
    token = pi_runtime.issue_model_token("other", "project")
    pi_runtime.revoke_model_token(token)
    assert pi_runtime.model_context(token) is None


def test_token_stays_valid_while_a_long_task_keeps_using_it(monkeypatch):
    now = pi_runtime.time.time()
    token = pi_runtime.issue_model_token("task", "project", "swarm")
    for hours in (0.9, 1.8, 2.7, 3.6):
        monkeypatch.setattr(pi_runtime.time, "time", lambda h=hours: now + h * 3600)
        assert pi_runtime.model_context(token)["task_id"] == "task"
    monkeypatch.setattr(pi_runtime.time, "time", lambda: now + 3.6 * 3600 + pi_runtime.TOKEN_IDLE_S + 1)
    assert pi_runtime.model_context(token) is None


def test_runtime_verifies_commit_protocol_and_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(pi_runtime, "component_root", lambda: tmp_path)
    monkeypatch.setattr(runtime, "info", lambda: {"sandbox_ready": True})
    binary = tmp_path / "omp"
    binary.write_bytes(b"#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    assert not pi_runtime.info()["installed"]
    manifest = {"upstream_commit": pi_runtime.UPSTREAM_COMMIT, "protocol_version": 2,
                "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
    (tmp_path / "onecat-component.json").write_text(json.dumps(manifest))
    assert pi_runtime.info()["ready"]
    binary.write_bytes(b"modified executable")
    assert not pi_runtime.info()["installed"]


@pytest.mark.parametrize("mode,eager", [("single", None), ("auto", "preferred"), ("swarm", "always")])
def test_native_modes_and_outer_sandbox_keep_pi_quantity_policy(project, monkeypatch, mode, eager):
    monkeypatch.setattr(pi_runtime, "info", lambda: {"ready": True})
    task = {"id": "task", "model": "local", "collaboration": mode, "execution_permission": "read-only"}
    native = pi_runtime.state_root() / "agent/pi/config.yml"
    native.parent.mkdir(parents=True)
    native.write_text("task:\n  maxConcurrency: 7\n  batch: false\nasync:\n  enabled: false\n")
    args, env, token = pi_runtime.prepare(project, task, Path("/tmp/test-model.sock"))
    try:
        home = pi_runtime.state_root() / "agent/tasks/task/pi"
        settings = json.loads((home / "studio.yml").read_text())
        assert settings.get("task", {}).get("eager") == eager
        assert "maxConcurrency" not in json.dumps(settings)
        assert "maxDepth" not in json.dumps(settings)
        assert "--config" in args and "/pi-native.yml" in args
        assert args[args.index(str(projects.root(project['id']))) - 1] == "--ro-bind"
        assert "--unshare-all" in args and "--clearenv" in args
        assert ("/opt/pi-single.ts" in args) == (mode == "single")
        assert ("--trusted-extension" in args) == (mode == "single")
        assert "--mode" in args and "rpc" in args
        assert "maxConcurrency: 7" in native.read_text()
        models = json.loads((home / ".omp/agent/models.yml").read_text())
        assert models["providers"]["onecat"]["apiKey"] == token
        assert models["providers"]["onecat"]["baseUrl"].endswith("/internal/agent/v1")
    finally:
        pi_runtime.revoke_model_token(token)


def test_default_api_and_unavailable_pi_do_not_fall_back(project, monkeypatch):
    from onecat.app import create_app
    defaults = NewTask(project_id=project["id"], prompt="hello", request_id="request-1")
    assert (defaults.engine, defaults.collaboration) == ("codex", "single")
    monkeypatch.setattr(pi_runtime, "info", lambda: {"installed": False, "ready": False, "sandbox_ready": True,
                                                   "version": pi_runtime.VERSION, "protocol_version": 2})
    monkeypatch.setattr(runtime, "info", lambda: {"installed": True, "sandbox_ready": True, "version": runtime.VERSION})
    client = TestClient(create_app())
    assert client.get("/api/requests/aggregate").status_code == 401
    client.post("/api/auth/setup", json={"password": "test-pi-password"})
    result = client.get("/api/agent/status").json()
    assert result["engines"]["pi"]["protocol_version"] == 2
    assert result["model"]["max_num_seqs"] == 2
    response = client.post("/api/agent/tasks", json={**defaults.model_dump(), "engine": "pi"})
    assert response.status_code == 409
    assert not db.all_records("agent_tasks")
    assert client.get("/api/requests?source=agent").status_code == 200
    assert client.get("/api/requests/aggregate?window_s=0").status_code == 422


FAKE_OMP = r'''
import base64,json,os,subprocess,sys,threading,time
from pathlib import Path
root=Path(sys.argv[1]); root.mkdir(exist_ok=True)
lock=threading.Lock(); settled=False
model={"id":"local","name":"Local","api":"openai-completions","provider":"onecat","baseUrl":"http://local"}
def emit(obj):
 with lock: print(json.dumps(obj),flush=True)
def end_background():
 global settled
 emit({"type":"subagent_lifecycle","payload":{"id":"child","agent":"scout","index":0,"status":"completed"}})
 settled=True
 emit({"type":"session_settled"})
emit({"type":"ready","protocolVersion":1,"supportedProtocolVersions":[1,2],"maxFrameBytes":1048576,"maxReassembledFrameBytes":67108864})
for line in sys.stdin:
 c=json.loads(line); kind=c['type']; data={}
 with (root/'commands').open('a') as f: f.write(kind+'\n')
 if kind=='negotiate_protocol': data={"protocolVersion":2}
 elif kind=='open_session':
  data={"cancelled":False,"resumed":(root/'session').exists(),"sessionId":"persistent-session"}
  (root/'session').write_text('yes')
 elif kind=='set_model': data=model
 elif kind=='get_subagents': data={"subagents":[]}
 elif kind=='get_state': data={"sessionId":"persistent-session","isSettled":settled}
 emit({"type":"response","command":kind,"id":c.get('id'),"success":True,"data":data})
 if kind=='prompt':
  if c['message']=='slow':
   child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
   (root/'pid').write_text(str(child.pid))
   continue
  emit({"type":"subagent_lifecycle","payload":{"id":"child","agent":"scout","index":0,"status":"started"}})
  emit({"type":"subagent_progress","payload":{"agent":"scout","index":0,"progress":{"id":"child","requests":2,"status":"running","durationMs":100,"lastIntent":"Inspect files"}}})
  # A protocol-v2 chunk uses real upstream framing, proving the client decodes
  # logical events rather than treating chunks as task content.
  raw=json.dumps({"type":"message_update","messageId":"m","padding":"x"*1048576,"assistantMessageEvent":{"type":"text_delta","delta":"hello"}}).encode()
  segments=[raw[i:i+262144] for i in range(0,len(raw),262144)]
  for i,segment in enumerate(segments):
   emit({"type":"rpc_chunk","chunkId":"chunk","index":i,"count":len(segments),"byteLength":len(raw),"data":base64.b64encode(segment).decode()})
  emit({"type":"agent_end","messages":[],"yielded":True,"isTerminal":True})
  emit({"type":"prompt_result","id":c['id'],"status":"completed","agentInvoked":True,"sessionSettled":False})
  threading.Timer(.15,end_background).start()
'''


@pytest.fixture
def fake_omp(tmp_path, monkeypatch):
    script = tmp_path / "fake_omp.py"
    script.write_text(FAKE_OMP)
    marker = tmp_path / "rpc"
    def prepare(project, task, socket):
        task["pi_session_dir"] = str(marker)
        return [sys.executable, "-u", str(script), str(marker)], {}, pi_runtime.issue_model_token(task["id"], project["id"])
    monkeypatch.setattr(pi_runtime, "prepare", prepare)
    monkeypatch.setattr(pi_runtime, "info", lambda: {"installed": True, "ready": True, "sandbox_ready": True,
                                                    "runtime_version": pi_runtime.VERSION})
    return marker


@pytest.mark.asyncio
async def test_official_rpc_client_handshake_chunks_native_events_and_resume(project, fake_omp):
    result = tasks.start(project["id"], "hello", "rpc-first", engine="pi", collaboration="swarm")
    await asyncio.wait_for(tasks.workers[result["id"]], 5)
    record = tasks.get(result["id"])
    assert record["state"] == "completed", record.get("error")
    assert record["pi_session_id"] == "persistent-session"
    assert record["subagents"][0]["status"] == "completed"
    assert record["subagents"][0]["request_count"] == 2
    assert any(item.get("text") == "hello" for item in record["items"])
    assert "get_state" in (fake_omp / "commands").read_text()
    tasks.start(project["id"], "continue", "rpc-resume", task_id=result["id"], engine="pi", collaboration="swarm")
    await asyncio.wait_for(tasks.workers[result["id"]], 5)
    assert tasks.get(result["id"])["pi_session_id"] == "persistent-session"
    assert (fake_omp / "commands").read_text().count("open_session") == 2


@pytest.mark.asyncio
async def test_cancel_aborts_rpc_and_reaps_descendant_process(project, fake_omp):
    import psutil
    record = tasks.start(project["id"], "slow", "rpc-cancel", engine="pi")
    for _ in range(200):
        if (fake_omp / "pid").exists():
            break
        await asyncio.sleep(.01)
    assert (fake_omp / "pid").exists(), tasks.get(record["id"]).get("error")
    pid = int((fake_omp / "pid").read_text())
    worker = tasks.workers[record["id"]]
    await tasks.cancel(record["id"])
    await asyncio.wait_for(worker, 5)
    assert "abort" in (fake_omp / "commands").read_text()
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    assert tasks.get(record["id"])["state"] == "cancelled"
    assert tasks.get(record["id"])["settled"]


@pytest.mark.asyncio
async def test_pi_rejected_steering_can_be_retried_without_duplicate_instructions(project, monkeypatch):
    from types import SimpleNamespace
    from onecat._vendor.omp_rpc.client import RpcCommandError

    record = {"id": "steer", "engine": "pi", "project_id": project["id"],
              "state": "running", "operation": "turn", "current_turn": "turn", "items": []}
    run = tasks.PiRun(record, project, "test")
    monkeypatch.setitem(tasks.runs, record["id"], run)
    delivered = []

    def steer(prompt):
        if not delivered:
            delivered.append("rejected")
            raise RpcCommandError("steer", "No active turn")
        delivered.append(prompt)

    run.client = SimpleNamespace(steer=steer)
    with pytest.raises((ValueError, HTTPException)):
        await tasks.steer("steer", "more", "retry", "turn")
    assert "retry" not in record["steering"]
    await tasks.steer("steer", "more", "retry", "turn")
    await tasks.steer("steer", "more", "retry", "turn")
    assert delivered == ["rejected", "more"]
    assert len(record["items"]) == 1


@pytest.mark.asyncio
async def test_pi_unconfirmed_steering_is_not_delivered_twice(project, monkeypatch):
    from types import SimpleNamespace
    from onecat._vendor.omp_rpc.client import RpcTimeoutError

    record = {"id": "steer", "engine": "pi", "project_id": project["id"],
              "state": "running", "operation": "turn", "current_turn": "turn", "items": []}
    run = tasks.PiRun(record, project, "test")
    monkeypatch.setitem(tasks.runs, record["id"], run)
    delivered = []

    def steer(prompt):
        delivered.append(prompt)
        raise RpcTimeoutError("Response lost after delivery")

    run.client = SimpleNamespace(steer=steer)
    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            await tasks.steer("steer", "more", "retry", "turn")
        assert error.value.status_code == 409
    assert delivered == ["more"]
    assert record["steering"]["retry"]["state"] == "pending"
    assert not record["items"]


def test_recovery_marks_unfinished_pi_children_interrupted(project):
    record = {"id": "recovery", "engine": "pi", "project_id": project["id"],
              "state": "running", "current_turn": "old-turn", "items": [{"text": "partial"}],
              "subagents": [
                  {"id": "running", "status": "running", "request_count": 3, "elapsed_s": 5,
                   "progress": {"status": "running", "lastIntent": "Inspect files"}},
                  {"id": "queued", "parent_id": "running", "status": "queued"},
                  {"id": "done", "status": "completed", "request_count": 2},
              ]}
    db.put("agent_tasks", record["id"], record)
    tasks.recover()
    restored = tasks.get(record["id"])
    assert restored["state"] == "interrupted" and restored["settled"]
    assert restored.get("current_turn") is None
    assert restored["items"] == record["items"]
    assert restored["subagents"] == [
        {**record["subagents"][0], "status": "interrupted",
         "progress": {"status": "interrupted", "lastIntent": "Inspect files"}},
        {**record["subagents"][1], "status": "interrupted"},
        record["subagents"][2],
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("host,expected", [("127.0.0.1", "127.0.0.1"), ("0.0.0.0", "127.0.0.1"), ("::", "::1")])
async def test_pi_bridge_uses_actual_listening_port(project, monkeypatch, host, expected):
    monkeypatch.setenv("ONECAT_STUDIO_LISTEN_HOST", host)
    monkeypatch.setenv("ONECAT_STUDIO_LISTEN_PORT", "18999")
    db.patch("settings", "main", {"port": 8888})
    requests = []
    original = httpx.AsyncClient

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, content=b'data: [DONE]\n\n')

    monkeypatch.setattr(tasks.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handle), **kwargs))
    run = tasks.PiRun({"id": "bridge", "items": []}, project, "test")
    run._pi_token = "test-scoped-model-token"
    server = await asyncio.start_server(run.bridge, "127.0.0.1", 0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
        writer.write(b"POST /internal/agent/v1/chat/completions HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}")
        await writer.drain()
        assert b"[DONE]" in await asyncio.wait_for(reader.read(), 2)
        writer.close()
        await writer.wait_closed()
        assert requests[0].url.port == 18999
        assert requests[0].url.host == expected
        assert requests[0].headers["Authorization"] == "Bearer test-scoped-model-token"
    finally:
        server.close()
        await server.wait_closed()
        await asyncio.gather(*list(run.bridge_clients), return_exceptions=True)
