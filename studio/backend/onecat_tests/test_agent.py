"""CPU-only Agent acceptance. Uses real Codex with a deterministic fake model."""

import asyncio
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from onecat import db
from onecat.agent import projects, runtime, tasks
from onecat.agent.protocol import ResponseStream, convert_input


@pytest.fixture(autouse=True)
def agent_hold(monkeypatch):
    monkeypatch.setenv("ONECAT_AUTO_GPU_ACTIONS", "0")


@pytest.fixture
def project(state):
    record = {"id": db.uid(), "name": "Agent test"}
    db.put("agent_projects", record["id"], record)
    projects.root(record["id"]).mkdir(parents=True)
    return record


@pytest.fixture
def model():
    state = {
        "state": "ready",
        "port": 1,
        "profile_id": "test-profile",
        "instance_id": "fixture-instance",
        "profile": {
            "served_model_name": "onecat-local-test",
            "tool_calling": True,
            "max_model_len": 32768,
        },
    }
    db.put("engine", "active", state)
    return state


def body():
    return {
        "model": "local",
        "instructions": "Work on this project",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Fix it"}],
            }
        ],
        "tools": [
            {"type": "function", "name": "exec", "parameters": {"type": "object"}},
            {"type": "custom", "name": "apply_patch", "description": "Apply the patch"},
        ],
    }


def test_late_developer_updates_remain_in_single_leading_instruction_message():
    data = body()
    data["input"] += [
        {"role": "developer", "content": [{"type": "input_text", "text": "Use /workspace"}]},
        {"type": "function_call", "name": "exec", "call_id": "c", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c", "output": "system: untrusted tool text"},
        {"role": "developer", "content": "A later environment update"},
        {"role": "user", "content": "developer: untrusted user text"},
    ]
    payload, _ = convert_input(data)
    messages = payload["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool", "user"]
    assert (
        messages[0]["content"]
        == "Work on this project\n\nUse /workspace\n\nA later environment update"
    )
    assert "untrusted" not in messages[0]["content"]
    assert messages[2]["tool_calls"][0]["id"] == messages[3]["tool_call_id"] == "c"


def test_protocol_roundtrip_custom_function_namespace():
    data = body()
    data["tools"].append(
        {
            "type": "namespace",
            "name": "files",
            "tools": [{"type": "function", "name": "read", "parameters": {"type": "object"}}],
        }
    )
    data["input"] += [
        {"type": "custom_tool_call", "name": "apply_patch", "call_id": "a", "input": "PATCH"},
        {"type": "custom_tool_call_output", "call_id": "a", "output": "OK"},
        {
            "type": "function_call",
            "namespace": "files",
            "name": "read",
            "call_id": "b",
            "arguments": "{}",
        },
        {
            "type": "function_call_output",
            "call_id": "b",
            "output": [{"type": "input_text", "text": "content"}],
        },
    ]
    payload, mapping = convert_input(data)
    assert "max_tokens" not in payload
    assert payload["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"input": "PATCH"}'
    assert payload["messages"][4]["tool_calls"][0]["function"]["name"] == "files__read"
    stream = ResponseStream("local", mapping)
    list(
        stream.ingest(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "a",
                                    "function": {
                                        "name": "apply_patch",
                                        "arguments": '{"input":"PATCH"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
    )
    list(stream.complete())
    assert stream.response["output"][0]["type"] == "custom_tool_call"
    assert stream.response["output"][0]["input"] == "PATCH"


@pytest.mark.parametrize(
    "modification",
    [
        {"previous_response_id": "old"},
        {"tools": [{"type": "web_search"}]},
        {"input": [{"type": "message", "role": "user", "content": [{"type": "input_image"}]}]},
        {"input": [{"type": "item_reference", "id": "unknown"}]},
    ],
)
def test_protocol_rejects_unsupported_context(modification):
    with pytest.raises(ValueError):
        convert_input({**body(), **modification})


def test_truncated_call_never_executes_and_missing_finish_fails():
    _, mapping = convert_input(body())
    stream = ResponseStream("local", mapping)
    with pytest.raises(ValueError, match="finish reason"):
        list(stream.complete())
    list(
        stream.ingest(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"name": "exec", "arguments": "{}"}}
                            ]
                        },
                        "finish_reason": "length",
                    }
                ]
            }
        )
    )
    events = b"".join(stream.complete())
    assert b"response.incomplete" in events
    assert stream.response["output"] == []


def test_stream_text_and_usage_are_actual_counts():
    stream = ResponseStream("local", {})
    events = list(stream.start())
    for text in ["中文", "👨‍👩‍👧", " **text**"]:
        events += list(
            stream.ingest({"choices": [{"delta": {"content": text}, "finish_reason": None}]})
        )
    list(
        stream.ingest(
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 38,
                    "completion_tokens": 17,
                    "prompt_tokens_details": {"cached_tokens": 20},
                },
            }
        )
    )
    events += list(stream.complete())
    assert stream.response["usage"]["output_tokens"] == 17
    assert stream.response["usage"]["input_tokens_details"]["cached_tokens"] == 20
    assert stream.response["output"][0]["content"][0]["text"] == "中文👨‍👩‍👧 **text**"
    sequences = [
        json.loads(event.decode().split("data: ")[1])["sequence_number"] for event in events
    ]
    assert sequences == list(range(len(sequences)))


@pytest.mark.parametrize(
    "path",
    ["../secret", "/etc/passwd", "foo/../../bar", ".codex/config.toml", "foo\\bar", "x\x00y"],
)
def test_project_paths_reject_escape(project, path):
    with pytest.raises(ValueError):
        projects.upload(project["id"], path, b"bad")


def test_project_symlinks_exports_and_binary_files(project, tmp_path):
    outside = tmp_path / "secret"
    outside.write_text("not in workspace")
    root = projects.root(project["id"])
    (root / "link").symlink_to(outside)
    (root / "directory").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(HTTPException):
        projects.read(project["id"], "link")
    with pytest.raises(HTTPException):
        projects.read(project["id"], "directory/secret")
    projects.upload(project["id"], "src/test.py", b"print('hello')")
    archive = zipfile.ZipFile(io.BytesIO(projects.export(project["id"])))
    assert archive.namelist() == ["src/test.py"]


def test_project_exact_file_limit_allows_replace_but_not_new_file(project):
    root = projects.root(project["id"])
    for i in range(2000):
        (root / f"file-{i}").touch()
    assert not projects.files(project["id"])["truncated"]
    projects.upload(project["id"], "file-0", b"replacement")
    with pytest.raises(ValueError, match="2,000"):
        projects.upload(project["id"], "overflow", b"new")
    assert not (root / "overflow").exists()


def test_project_normalized_replacement_counts_existing_bytes(project, monkeypatch):
    projects.upload(project["id"], "src/file.txt", b"12345678")
    monkeypatch.setattr(projects, "MAX_PROJECT", 10)
    projects.upload(project["id"], "src//file.txt", b"abcdefghij")
    assert projects.read(project["id"], "src/file.txt") == b"abcdefghij"


def test_excluded_regular_files_do_not_break_project_export(project):
    root = projects.root(project["id"])
    (root / ".git").write_text("gitdir: elsewhere")
    (root / "index.html").write_text("hello")
    assert [f["path"] for f in projects.files(project["id"])["items"]] == ["index.html"]
    assert zipfile.ZipFile(io.BytesIO(projects.export(project["id"]))).namelist() == ["index.html"]


def test_export_accepts_generated_file_larger_than_preview_limit(project, monkeypatch):
    monkeypatch.setattr(projects, "MAX_FILE", 4)
    (projects.root(project["id"]) / "generated.bin").write_bytes(b"12345678")
    with pytest.raises(ValueError):
        projects.read(project["id"], "generated.bin")
    archive = zipfile.ZipFile(io.BytesIO(projects.export(project["id"])))
    assert archive.read("generated.bin") == b"12345678"


def test_failed_upload_preserves_original_and_cleans_temporary_file(project, monkeypatch):
    projects.upload(project["id"], "important.txt", b"original")

    def fail(*args, **kwargs):
        raise OSError("disk failure")

    monkeypatch.setattr(projects.os, "replace", fail)
    with pytest.raises(OSError, match="disk failure"):
        projects.upload(project["id"], "important.txt", b"replacement")
    assert projects.read(project["id"], "important.txt") == b"original"
    assert [f["path"] for f in projects.files(project["id"])["items"]] == ["important.txt"]


def test_atomic_replacement_preserves_script_execute_permission(project):
    path = projects.root(project["id"]) / "run.sh"
    path.write_text("#!/bin/sh\necho old")
    path.chmod(0o755)
    projects.upload(project["id"], "run.sh", b"#!/bin/sh\necho new")
    assert path.stat().st_mode & 0o777 == 0o755
    assert path.read_bytes() == b"#!/bin/sh\necho new"


@pytest.mark.asyncio
async def test_immediate_cancel_releases_project_without_starting_codex(
    project, model, monkeypatch
):
    monkeypatch.setattr(runtime, "info", lambda: {"installed": True, "sandbox_ready": True})

    async def never_start(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(tasks.Run, "execute", never_start)
    record = tasks.start(project["id"], "go", "immediate-cancel")
    worker = tasks.workers[record["id"]]
    await tasks.cancel(record["id"])
    await asyncio.gather(worker, return_exceptions=True)
    await asyncio.sleep(0)
    assert record["id"] not in tasks.runs
    assert record["id"] not in tasks.workers
    assert tasks.get(record["id"])["settled"]
    assert tasks.get(record["id"])["state"] == "cancelled"
    tasks.require_idle(project["id"])


@pytest.mark.asyncio
async def test_duplicate_approval_race_only_sends_one_reply(project):
    record = {
        "id": "approval-race",
        "state": "waiting",
        "items": [],
        "approvals": [
            {"id": "one", "rpc_id": 2, "method": "item/commandExecution/requestApproval"}
        ],
    }
    run = tasks.Run(record, project, "go")
    # Yield during the write to reproduce two browser tabs answering together.
    messages = []

    async def write(message):
        messages.append(message)
        await asyncio.sleep(0.01)

    run.write = write
    tasks.runs[record["id"]] = run
    try:
        results = await asyncio.gather(
            *(tasks.approve(record["id"], "one", "accept") for _ in range(2)),
            return_exceptions=True,
        )
        assert len(messages) == 1
        assert sum(isinstance(r, HTTPException) for r in results) == 1
    finally:
        tasks.runs.pop(record["id"], None)


@pytest.mark.asyncio
async def test_question_requires_matching_nonempty_answer(project):
    record = {
        "id": "question",
        "state": "waiting",
        "items": [],
        "approvals": [
            {
                "id": "one",
                "rpc_id": 2,
                "method": "item/tool/requestUserInput",
                "params": {"questions": [{"id": "choice", "question": "Which?"}]},
            }
        ],
    }
    run = tasks.Run(record, project, "go")
    run.write = AsyncMock()
    tasks.runs[record["id"]] = run
    try:
        for answers in [
            None,
            {},
            {"wrong": {"answers": ["value"]}},
            {"choice": {"answers": [" "]}},
        ]:
            with pytest.raises(ValueError):
                await tasks.approve(record["id"], "one", "accept", answers)
        run.write.assert_not_called()
        await tasks.approve(
            record["id"], "one", "accept", {"choice": {"answers": ["custom reply"]}}
        )
        run.write.assert_awaited_once()
    finally:
        tasks.runs.pop(record["id"], None)


@pytest.mark.parametrize(
    "choice",
    [
        "none",
        "required",
        {"type": "function", "name": "exec"},
        {"type": "custom", "name": "apply_patch"},
    ],
)
def test_provider_preserves_explicit_tool_choice(choice):
    payload, _ = convert_input({**body(), "tool_choice": choice})
    expected = (
        choice
        if isinstance(choice, str)
        else {"type": "function", "function": {"name": choice["name"]}}
    )
    assert payload["tool_choice"] == expected


def test_snapshot_budget_does_not_claim_unchanged_file_modified(project):
    projects.upload(project["id"], "file.txt", b"unchanged")
    run = tasks.Run({"id": "diff"}, project, "go")
    run.snapshot_files()
    run.baseline["file.txt"] = None  # Text omitted because snapshot budget ran out.
    assert run.file_changes()["changes"] == []


def test_truncated_listing_does_not_claim_omitted_file_deleted(project, monkeypatch):
    projects.upload(project["id"], "still-here", b"unchanged")
    run = tasks.Run({"id": "diff"}, project, "go")
    run.snapshot_files()
    monkeypatch.setattr(projects, "files", lambda _: {"items": [], "truncated": True})
    assert run.file_changes() == {"changes": [], "diff": "", "files_truncated": True}


@pytest.mark.asyncio
async def test_resume_clears_previous_turn_summary(project, model, monkeypatch):
    monkeypatch.setattr(runtime, "info", lambda: {"installed": True, "sandbox_ready": True})

    async def never_start(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(tasks.Run, "execute", never_start)
    record = tasks.start(project["id"], "first", "first-request")
    worker = tasks.workers[record["id"]]
    await tasks.cancel(record["id"])
    await asyncio.gather(worker, return_exceptions=True)
    await asyncio.sleep(0)
    db.patch(
        "agent_tasks",
        record["id"],
        {
            "plan": [{"step": "old", "status": "completed"}],
            "diff": "old diff",
            "changes": [{"path": "old"}],
            "file_error": "old error",
        },
    )
    continued = tasks.start(project["id"], "second", "second-request", task_id=record["id"])
    try:
        assert continued["plan"] == continued["changes"] == []
        assert continued["diff"] == "" and continued["file_error"] is None
        assert len(continued["items"]) == 2
    finally:
        await tasks.shutdown()


@pytest.mark.asyncio
async def test_cancel_git_import_reaps_child_and_removes_partial_project(monkeypatch):
    from onecat.agent import api as agent_api
    from onecat.config import state_root

    child = AsyncMock()
    child.returncode, child.pid = None, 345
    started = asyncio.Event()

    async def communicate():
        started.set()
        await asyncio.Event().wait()

    child.communicate = communicate
    monkeypatch.setattr(agent_api.asyncio, "create_subprocess_exec", AsyncMock(return_value=child))
    from unittest.mock import Mock

    kill = Mock()
    monkeypatch.setattr(agent_api.os, "killpg", kill)
    pending = asyncio.create_task(
        agent_api.create_project(
            agent_api.NewProject(name="clone", repository="https://github.com/example/project")
        )
    )
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    kill.assert_called_once_with(345, agent_api.signal.SIGKILL)
    child.wait.assert_awaited_once()
    assert db.all_records("agent_projects") == []
    assert list((state_root() / "agent/projects").iterdir()) == []


def test_sandbox_probe_recovers_after_policy_change(monkeypatch):
    from types import SimpleNamespace

    clock = [10.0]
    probes = []

    def probe(*args, **kwargs):
        probes.append(True)
        return SimpleNamespace(returncode=1 if len(probes) == 1 else 0, stderr="permission denied")

    monkeypatch.setattr(runtime, "_sandbox_checked", None)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime.subprocess, "run", probe)
    assert not runtime.info()["sandbox_ready"]
    clock[0] += 31
    assert runtime.info()["sandbox_ready"]


@pytest.mark.asyncio
async def test_shutdown_does_not_interrupt_cleanup_of_stopped_task(project, model, monkeypatch):
    monkeypatch.setattr(runtime, "info", lambda: {"installed": True, "sandbox_ready": True})
    started, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))

    async def execute(self):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()

    monkeypatch.setattr(tasks.Run, "execute", execute)
    record = tasks.start(project["id"], "go", "shutdown-race")
    await started.wait()
    await tasks.cancel(record["id"])
    await cleaning.wait()
    shutdown = asyncio.create_task(tasks.shutdown())
    await asyncio.sleep(0.01)
    release.set()
    await shutdown
    assert cleaned.is_set()
    assert tasks.get(record["id"])["settled"]


def test_api_auth_and_project_flow():
    from onecat.app import create_app

    # No lifespan: no telemetry, no hardware monitor, no model reconciliation.
    client = TestClient(create_app())
    assert client.get("/api/agent/projects").status_code == 401
    assert (
        client.post("/api/auth/setup", json={"password": "test-password-agent"}).status_code == 200
    )
    assert (
        client.post(
            "/api/agent/projects", json={"name": "X", "repository": "http://127.0.0.1:8888"}
        ).status_code
        == 400
    )
    result = client.post("/api/agent/projects", json={"name": "My project"})
    assert result.status_code == 200
    id = result.json()["id"]
    assert (
        client.post(
            f"/api/agent/projects/{id}/files", files={"file": ("index.html", b"<h1>Hello</h1>")}
        ).status_code
        == 200
    )
    assert (
        client.get(f"/api/agent/projects/{id}/file", params={"path": "index.html"}).json()["text"]
        == "<h1>Hello</h1>"
    )
    assert (
        client.post(
            f"/api/agent/projects/{id}/files?path=../secret", files={"file": ("a", b"x")}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/agent/tasks", json={"project_id": id, "prompt": "go", "request_id": "abcdefgh"}
        ).status_code
        == 503
    )
    assert (
        client.get(f"/api/agent/projects/{id}/download").headers["content-type"]
        == "application/zip"
    )
    from onecat import auth

    key = auth.issue("inference only")
    assert (
        client.get("/api/agent/projects", headers={"authorization": "Bearer " + key}).status_code
        == 403
    )
    assert client.delete(f"/api/agent/projects/{id}").status_code == 200


def test_recover_interrupted_task_preserves_history(project):
    db.put(
        "agent_tasks",
        "old",
        {
            "id": "old",
            "project_id": project["id"],
            "state": "running",
            "items": [{"text": "partial"}],
            "approvals": [{}],
        },
    )
    tasks.recover()
    record = tasks.get("old")
    assert record["state"] == "interrupted" and record["items"] == [{"text": "partial"}]
    assert record["approvals"] == []


def require_runtime():
    info = runtime.info()
    if not info["installed"] or not info["sandbox_ready"]:
        pytest.skip("Bundled Codex and isolated execution sandbox required")


class FakeStream(httpx.AsyncByteStream):
    def __init__(self, event, usage=True):
        self.event, self.usage = event, usage

    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"reasoning_content":"Inspect the project carefully."},"finish_reason":null}]}\n\n'
        yield b"data: " + json.dumps(self.event).encode() + b"\n\n"
        if self.usage:
            yield b'data: {"choices":[],"usage":{"prompt_tokens":42,"completion_tokens":9,"prompt_tokens_details":{"cached_tokens":10}}}\n\n'
        yield b"data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_official_codex_reads_edits_tests_isolates_and_resumes(project, model, monkeypatch):
    require_runtime()
    root = projects.root(project["id"])
    (root / "calc.py").write_text("def add(a,b):\n    return a-b\n")
    commands = [
        "cat calc.py",
        "python3 -c \"from pathlib import Path; Path('calc.py').write_text('def add(a,b):\\n    return a+b\\n')\"",
        "python3 -c \"from calc import add; assert add(2,3)==5; print('TEST PASSED')\"",
        "python3 - <<'PYCODE'\nimport os,socket\n"
        f"assert not os.path.exists({str(Path.home() / '.codex')!r})\n"
        "assert not os.path.exists('/dev/nvidia0')\ntry:\n s=socket.socket(); s.settimeout(.2)\n assert s.connect_ex(('127.0.0.1',8888))!=0\n assert s.connect_ex(('1.1.1.1',443))!=0\nexcept PermissionError:\n pass\nprint('ISOLATION PASSED')\nPYCODE",
    ]
    payloads = []

    async def fake(request):
        payload = json.loads(request.content)
        index = len(payloads)
        payloads.append(payload)
        if index < len(commands):
            event = {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": f"call_{index}",
                                    "type": "function",
                                    "function": {
                                        "name": "exec_command",
                                        "arguments": json.dumps(
                                            {
                                                "cmd": commands[index],
                                                "login": False,
                                                "max_output_tokens": 1000,
                                            }
                                        ),
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        else:
            event = {
                "choices": [{"delta": {"content": "Fixed and tested."}, "finish_reason": "stop"}]
            }
        return httpx.Response(200, stream=FakeStream(event))

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(fake), **kwargs),
    )
    record = tasks.start(project["id"], "Fix add and run tests", "real-probe-1")
    duplicate = tasks.start(project["id"], "Fix add and run tests", "real-probe-1")
    assert record["id"] == duplicate["id"]
    with pytest.raises(HTTPException):
        tasks.start(project["id"], "Conflicting write", "real-probe-2")
    await asyncio.wait_for(tasks.workers[record["id"]], 60)
    completed = tasks.get(record["id"])
    assert completed["state"] == "completed", completed.get("error")
    assert "+    return a+b" in completed["diff"]
    assert any(
        i["type"] == "reasoning"
        and "Inspect the project" in (i.get("text", "") or str(i.get("summary", "")))
        for i in completed["items"]
    )
    outputs = [i.get("aggregatedOutput", "") or "" for i in completed["items"]]
    assert any("TEST PASSED" in x for x in outputs) and any(
        "ISOLATION PASSED" in x for x in outputs
    )
    assert completed["usage"] == {
        "input_tokens": 210,
        "output_tokens": 45,
        "cached_tokens": 50,
        "missing_calls": 0,
        "cache_missing_calls": 0,
    }
    assert any(
        m["role"] == "tool" and "TEST PASSED" in m["content"] for m in payloads[-1]["messages"]
    )
    tasks.start(project["id"], "Summarize the result", "real-probe-resume", task_id=record["id"])
    await asyncio.wait_for(tasks.workers[record["id"]], 30)
    resumed = tasks.get(record["id"])
    assert resumed["state"] == "completed" and resumed["thread_id"] == completed["thread_id"]
    assert resumed["usage"]["input_tokens"] == 252
    assert not tasks.workers and not tasks.runs
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT status,source,prompt_tokens,completion_tokens FROM requests"
        ).fetchall()
    assert len(rows) == 6 and all(tuple(row) == (200, "agent", 42, 9) for row in rows)


@pytest.mark.asyncio
async def test_cancel_inference_releases_request_and_child_processes(project, model, monkeypatch):
    require_runtime()
    waiting = asyncio.Event()
    released = asyncio.Event()

    async def slow(self, payload):
        waiting.set()
        try:
            await asyncio.sleep(60)
            yield {}
        finally:
            released.set()

    monkeypatch.setattr(tasks.Run, "infer", slow)
    record = tasks.start(project["id"], "Wait", "cancel-test")
    worker = tasks.workers[record["id"]]
    await asyncio.wait_for(waiting.wait(), 10)
    process = tasks.runs[record["id"]].process
    await tasks.cancel(record["id"])
    await asyncio.wait_for(worker, 10)
    assert tasks.get(record["id"])["state"] == "cancelled"
    assert released.is_set() and process.returncode is not None
    assert not tasks.workers


@pytest.mark.asyncio
async def test_approval_is_scoped_and_cannot_be_answered_twice(project):
    record = {
        "id": "approval-task",
        "state": "waiting",
        "project_id": project["id"],
        "items": [],
        "approvals": [
            {"id": "one", "rpc_id": 9, "method": "item/commandExecution/requestApproval"}
        ],
    }
    run = tasks.Run(record, project, "test")
    tasks.runs["approval-task"] = run
    messages = []

    async def write(message):
        messages.append(message)

    run.write = write
    try:
        with pytest.raises(HTTPException):
            await tasks.approve("other-task", "one", "accept")
        await tasks.approve("approval-task", "one", "accept")
        assert messages == [{"id": 9, "result": {"decision": "accept"}}]
        with pytest.raises(HTTPException):
            await tasks.approve("approval-task", "one", "accept")
    finally:
        tasks.runs.clear()


def test_model_switch_and_missing_tools_fail_before_start(project, model, monkeypatch):
    require_runtime()
    model["profile"]["tool_calling"] = False
    db.put("engine", "active", model)
    with pytest.raises(HTTPException, match="409"):
        tasks.start(project["id"], "run", "wrong-model")


def test_replay_events_do_not_mutate_previous_items(project):
    record = {"id": "replay", "items": []}
    run = tasks.Run(record, project, "test")
    item = {"id": "1", "text": "a"}
    run.emit({"type": "item", "item": item})
    item["text"] += "b"
    assert run.events[0][1]["item"]["text"] == "a"


@pytest.mark.asyncio
async def test_original_codex_approval_wait_and_decline(project, model, monkeypatch):
    require_runtime()
    calls = []

    async def provider(self, payload):
        calls.append(payload)
        if len(calls) == 1:
            yield {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "approval-call",
                                    "function": {
                                        "name": "exec_command",
                                        "arguments": json.dumps(
                                            {
                                                "cmd": 'python3 -c "print(123)"',
                                                "sandbox_permissions": "require_escalated",
                                                "justification": "Run test?",
                                                "login": False,
                                            }
                                        ),
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        else:
            yield {"choices": [{"delta": {"content": "Acknowledged."}, "finish_reason": "stop"}]}

    monkeypatch.setattr(tasks.Run, "infer", provider)
    record = tasks.start(project["id"], "Test the approval interface", "approval-real")
    worker = tasks.workers[record["id"]]
    try:
        for _ in range(100):
            current = tasks.get(record["id"])
            if current["approvals"] or worker.done():
                break
            await asyncio.sleep(0.05)
        assert current["approvals"], json.dumps(current)
        approval = current["approvals"][0]
        assert current["state"] == "waiting"
        await tasks.approve(record["id"], approval["id"], "decline")
        await asyncio.wait_for(worker, 10)
        completed = tasks.get(record["id"])
        assert completed["state"] == "completed", completed.get("error")
        assert not any(i.get("aggregatedOutput") == "123\n" for i in completed["items"])
        assert len(calls) == 2
    finally:
        if not worker.done():
            await tasks.cancel(record["id"])
            await worker


@pytest.mark.asyncio
async def test_manager_shutdown_keeps_resumable_thread(project, model, monkeypatch):
    require_runtime()
    started = asyncio.Event()

    async def provider(self, payload):
        started.set()
        await asyncio.sleep(60)
        yield {}

    monkeypatch.setattr(tasks.Run, "infer", provider)
    record = tasks.start(project["id"], "A long task", "shutdown-test")
    await asyncio.wait_for(started.wait(), 10)
    await tasks.shutdown()
    result = tasks.get(record["id"])
    assert result["state"] == "interrupted" and result["thread_id"]
    assert not tasks.runs and not tasks.workers


@pytest.mark.asyncio
async def test_model_identity_cannot_change_between_agent_calls(project, model):
    record = {
        "model": model["profile"]["served_model_name"],
        "engine_port": model["port"],
        "profile_id": model["profile_id"],
        "engine_instance": "different-instance",
    }
    run = tasks.Run(record, project, "test")
    with pytest.raises(ValueError, match="model instance changed"):
        async for _ in run.infer({}):
            pytest.fail("No request should be sent to the replacement model")
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM requests").fetchone()[0] == 0


def test_history_summary_omits_transcripts(project):
    db.put(
        "agent_tasks",
        "history",
        {
            "id": "history",
            "project_id": project["id"],
            "title": "task",
            "items": [{"text": "large"}],
            "diff": "large",
            "request_ids": ["x"],
        },
    )
    summary = tasks.summaries(project["id"])
    assert summary == [{"id": "history", "project_id": project["id"], "title": "task"}]


@pytest.mark.asyncio
async def test_official_commands_plan_review_compact_skills_and_readonly(
    project, model, monkeypatch
):
    """Exercise the pinned binary's actual RPCs, not an emulated command handler."""
    require_runtime()
    root = projects.root(project["id"])
    skill = root / ".agents/skills/project-check/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: project-check\ndescription: Check this project\n---\nInspect the project.\n"
    )
    observed, payloads = [], []
    original_rpc = tasks.Run.rpc

    async def rpc(self, method, params):
        observed.append((method, params))
        return await original_rpc(self, method, params)

    async def fake(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        # Mimic the real Qwen template restriction that caught production.
        assert all(m["role"] != "system" for m in payload["messages"][1:])
        return httpx.Response(
            200,
            stream=FakeStream(
                {
                    "choices": [
                        {
                            "delta": {"content": "Project inspected. No changes needed."},
                            "finish_reason": "stop",
                        }
                    ]
                }
            ),
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(tasks.Run, "rpc", rpc)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(fake), **kwargs),
    )
    db.put("engine", "active", {"state": "stopped"})
    task = tasks.start(project["id"], "/skills", "commands-skills", operation="skills")
    await asyncio.wait_for(tasks.workers[task["id"]], 30)
    result = tasks.get(task["id"])
    assert result["state"] == "completed", result.get("error")
    assert any("$project-check" in item.get("text", "") for item in result["items"])
    assert not payloads
    db.put("engine", "active", model)
    original_thread = None
    for operation, mode, prompt in [
        ("turn", "plan", "Plan this project"),
        ("turn", "default", "Inspect this project"),
        ("review", "default", "/review"),
        ("compact", "default", "/compact"),
        ("turn", "default", "Continue after compaction"),
    ]:
        tasks.start(
            project["id"],
            prompt,
            "commands-" + prompt,
            task_id=task["id"],
            operation=operation,
            permission="read-only",
            mode=mode,
        )
        await asyncio.wait_for(tasks.workers[task["id"]], 30)
        result = tasks.get(task["id"])
        assert result["state"] == "completed", (operation, result.get("error"))
        assert result["execution_permission"] == "read-only"
        original_thread = original_thread or result["thread_id"]
        assert result["thread_id"] == original_thread
    assert {"review/start", "thread/compact/start", "skills/list"} <= {
        method for method, _ in observed
    }
    turns = [p for m, p in observed if m == "turn/start"]
    assert turns[0]["collaborationMode"]["mode"] == "plan"
    assert all(p["sandboxPolicy"]["type"] == "readOnly" for p in turns)
    assert not tasks.workers and not tasks.runs


@pytest.mark.asyncio
async def test_readonly_outer_sandbox_cannot_be_bypassed(project, model):
    require_runtime()
    import tempfile

    with tempfile.TemporaryDirectory(prefix="agent-ro-test-") as directory:
        socket_path = Path(directory) / "model.sock"
        socket_path.touch()
        record = {
            "id": db.uid(),
            "model": "local",
            "context_window": 32768,
            "permission": "read-only",
        }
        args, env = runtime.prepare(project, record, socket_path)
        # Use the exact namespace/mount configuration; even a privileged Codex
        # command approval cannot turn a read-only bind into a writable project.
        args = args[: args.index("/usr/bin/python3")] + [
            "/usr/bin/python3",
            "-c",
            "from pathlib import Path; Path('/workspace/forbidden').write_text('bad')",
        ]
        proc = await asyncio.create_subprocess_exec(
            *args, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), 10)
        assert proc.returncode != 0 and b"Read-only file system" in stderr
        assert not (projects.root(project["id"]) / "forbidden").exists()


@pytest.mark.asyncio
async def test_tool_timing_deduplicates_events_and_context_is_not_accumulated_usage(
    project, monkeypatch
):
    clock = [10.0]
    monkeypatch.setattr(tasks.time, "monotonic", lambda: clock[0])
    run = tasks.Run({"id": "timing", "items": [], "thread_id": "thread"}, project, "test")
    start = {
        "method": "item/started",
        "params": {"item": {"id": "tool", "type": "commandExecution"}},
    }
    await run.ingest(start)
    clock[0] = 12
    await run.ingest(start)
    clock[0] = 15
    completed = {
        "method": "item/completed",
        "params": {"item": {"id": "tool", "type": "commandExecution", "status": "completed"}},
    }
    await run.ingest(completed)
    await run.ingest(completed)
    assert run.record["metrics"]["tool_s"] == 5
    assert run.record["metrics"]["tool_calls"] == 1
    for amount in (300, 100):
        await run.ingest(
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "thread",
                    "tokenUsage": {
                        "last": {"totalTokens": amount},
                        "total": {"totalTokens": 400},
                        "modelContextWindow": 32768,
                    },
                },
            }
        )
    assert run.record["context_usage"]["last"]["totalTokens"] == 100
