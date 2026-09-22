# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from starlette.responses import JSONResponse, StreamingResponse
from onecat import chat_store as studio_db
from onecat import db, proxy, chat_runs, model_controls
from onecat.agent import tasks, projects
from onecat_tests.test_agent import project, model, require_runtime, FakeStream
from onecat_tests.test_chat_runs import thread


def test_thinking_is_grounded_in_template_and_cache_invalidates(tmp_path):
    profile = {"model_path": str(tmp_path)}
    assert not model_controls.thinking_support(profile)["supported"]
    with pytest.raises(HTTPException):
        model_controls.thinking_kwargs(profile, True)
    assert model_controls.thinking_kwargs(profile, False) == {}
    file = tmp_path / "tokenizer_config.json"
    file.write_text(json.dumps({"chat_template": "{% if enable_thinking %}<think>{% endif %}"}))
    assert model_controls.thinking_kwargs(profile, True) == {"enable_thinking": True}
    assert model_controls.thinking_kwargs(profile, False) == {"enable_thinking": False}
    file.write_text(json.dumps({"chat_template": "always reasoning"}))
    assert not model_controls.thinking_support(profile)["supported"]


@pytest.mark.asyncio
async def test_launch_identity_rejects_same_port_same_profile_restart(project, model):
    model.pop("instance_id")
    model.update(launch_id="launch-a", pid=42, process_created=123)
    db.put("engine", "active", model)
    record = {
        "id": "fixture",
        "model": "local",
        "project_id": project["id"],
        "engine_port": 1,
        "profile_id": "test-profile",
        "engine_identity": proxy.instance_identity(model),
    }
    run = tasks.Run(record, project, "hello")
    db.put("engine", "active", {**model, "launch_id": "launch-b"})
    with pytest.raises(ValueError, match="instance changed"):
        await anext(run.infer({}))
    with pytest.raises(HTTPException):
        proxy.begin("local", "agent", 1, expected_instance=record["engine_identity"])
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_skills_preserve_prior_result_and_bind_explicit_skill(project, model, monkeypatch):
    require_runtime()
    root = projects.root(project["id"])
    skill = root / ".agents/skills/check-project/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: check-project\ndescription: A project check\n---\nSPECIAL_SKILL_INSTRUCTION_791\n"
    )
    captured, payloads = [], []
    rpc = tasks.Run.rpc

    async def record_rpc(self, method, params):
        captured.append((method, params))
        return await rpc(self, method, params)

    async def fake(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            stream=FakeStream(
                {"choices": [{"delta": {"content": "Done"}, "finish_reason": "stop"}]}
            ),
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(tasks.Run, "rpc", record_rpc)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(fake), **kw)
    )
    task = tasks.start(project["id"], "$check-project inspect", "first-request")
    await asyncio.wait_for(tasks.workers[task["id"]], 30)
    done = tasks.get(task["id"])
    assert done["state"] == "completed", done.get("error")
    inputs = next(p["input"] for m, p in captured if m == "turn/start")
    assert any(i["type"] == "skill" and i["name"] == "check-project" for i in inputs)
    assert "SPECIAL_SKILL_INSTRUCTION_791" in json.dumps(payloads)
    done.update(
        diff="saved diff",
        changes=[{"path": "a.py", "kind": "modified"}],
        plan=[{"step": "done", "status": "completed"}],
    )
    db.put("agent_tasks", task["id"], done)
    db.put("engine", "active", {"state": "stopped"})
    tasks.start(project["id"], "/skills", "second-request", task_id=task["id"], operation="skills")
    await asyncio.wait_for(tasks.workers[task["id"]], 30)
    after = tasks.get(task["id"])
    assert after["state"] == "completed", after.get("error")
    for key in [
        "model",
        "profile_id",
        "context_window",
        "diff",
        "changes",
        "plan",
        "model_calls",
        "usage",
        "turn_count",
    ]:
        assert after[key] == done[key], key


@pytest.mark.asyncio
async def test_steer_is_bound_to_turn_and_retry_is_idempotent(project):
    record = {
        "id": "steering",
        "project_id": project["id"],
        "state": "running",
        "operation": "turn",
        "thread_id": "native-thread",
        "current_turn": "native-turn",
        "items": [],
    }
    run = tasks.Run(record, project, "first")
    run.rpc = AsyncMock(return_value={"turnId": "native-turn"})
    tasks.runs[record["id"]] = run
    try:
        for _ in range(2):
            await tasks.steer(record["id"], "also add tests", "steer-request", "native-turn")
        assert run.rpc.await_count == 1
        assert len(record["items"]) == 1
        assert run.rpc.call_args.args[1]["expectedTurnId"] == "native-turn"
        with pytest.raises(HTTPException):
            await tasks.steer(record["id"], "different", "steer-request", "native-turn")
        with pytest.raises(HTTPException):
            await tasks.steer(record["id"], "more", "other-request", "stale-turn")
        run.rpc = AsyncMock(side_effect=TimeoutError)
        with pytest.raises(HTTPException, match="unconfirmed"):
            await tasks.steer(record["id"], "uncertain", "uncertain-request", "native-turn")
        with pytest.raises(HTTPException, match="unconfirmed"):
            await tasks.steer(record["id"], "uncertain", "uncertain-request", "native-turn")
        assert run.rpc.await_count == 1
    finally:
        tasks.runs.pop(record["id"], None)


def test_history_search_reaches_beyond_latest_hundred():
    for i in range(110):
        db.put(
            "agent_tasks",
            str(i),
            {
                "id": str(i),
                "title": f"Task {i:03}",
                "project_id": "p",
                "items": ["huge transcript"],
                "request_signatures": {"secret": "private"},
            },
        )
    assert len(tasks.summaries()) == 100
    assert tasks.summaries(query="Task 000")[0]["id"] == "0"
    assert len(tasks.summaries(offset=100)) == 10
    assert "items" not in tasks.summaries()[0] and "request_signatures" not in tasks.summaries()[0]


@pytest.mark.asyncio
async def test_rejected_regeneration_preserves_history_and_settings(monkeypatch):
    tid = thread()
    original = studio_db.list_chat_messages(tid)
    answer = {
        "id": "old-answer",
        "threadId": tid,
        "role": "assistant",
        "content": [{"type": "text", "text": "Original useful response"}],
        "createdAt": original[-1]["createdAt"] + 1,
    }
    studio_db.sync_chat_messages(tid, [answer])
    original = studio_db.list_chat_messages(tid)
    payload = {
        "message_id": "new-answer",
        "request": {},
        "messages": original[:1],
        "expected_message_ids": [m["id"] for m in original],
        "settings": {"thinking": False},
    }
    monkeypatch.setattr(
        proxy,
        "forward",
        AsyncMock(return_value=JSONResponse({"error": "template rejected"}, status_code=400)),
    )
    run = chat_runs.start(tid, payload)
    await chat_runs._tasks[tid]
    assert chat_runs.current(tid)["state"] == "failed"
    assert studio_db.list_chat_messages(tid) == original
    assert chat_runs.start(tid, payload)["id"] == run["id"]
    with pytest.raises(HTTPException):
        chat_runs.start(tid, {**payload, "request": {"temperature": 0.5}})
    with pytest.raises(HTTPException):
        chat_runs.start(tid, {**payload, "message_id": "stale", "expected_message_ids": []})


@pytest.mark.asyncio
async def test_accepted_generation_commits_once_and_thinking_reaches_agent(
    project, model, monkeypatch, tmp_path
):
    (tmp_path / "chat_template.jinja").write_text("{% if enable_thinking %}<think>{% endif %}")
    model["profile"]["model_path"] = str(tmp_path)
    db.put("engine", "active", model)
    tid = thread()
    original = studio_db.list_chat_messages(tid)

    async def events():
        yield 'data: {"choices":[{"delta":{"content":"new answer"}}]}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(proxy, "forward", AsyncMock(return_value=StreamingResponse(events())))
    payload = {
        "message_id": "new-answer",
        "request": {},
        "messages": original,
        "expected_message_ids": [m["id"] for m in original],
        "settings": {"thinking": True},
    }
    chat_runs.start(tid, payload)
    await chat_runs._tasks[tid]
    assert chat_runs.current(tid)["pending_commit"] is False
    assert studio_db.list_chat_messages(tid)[-1]["content"][0]["text"] == "new answer"
    assert studio_db.get_chat_thread(tid)["settings"]["thinking"] is True
    seen = []

    async def fake(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            stream=FakeStream(
                {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}
            ),
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(fake), **kw)
    )
    for thinking in [True, False]:
        record = {
            "id": "observe",
            "model": "local",
            "model_calls": 0,
            "engine_port": 1,
            "profile_id": "test-profile",
            "engine_instance": model["instance_id"],
            "thinking": thinking,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "missing_calls": 0,
                "cache_missing_calls": 0,
            },
        }
        run = tasks.Run(record, project, "hello")
        _ = [chunk async for chunk in run.infer({"messages": []})]
        assert seen[-1]["chat_template_kwargs"] == {"enable_thinking": thinking}


def test_model_picker_only_loads_matching_downloaded_target(tmp_path, monkeypatch):
    from onecat import engine

    path = tmp_path / "target"
    path.mkdir()
    (path / "config.json").write_text("{}")
    db.put(
        "models",
        "target",
        {
            "id": "target",
            "name": "Model",
            "state": "downloaded",
            "path": str(path),
            "role": "target",
        },
    )
    db.put(
        "models",
        "draft",
        {"id": "draft", "name": "Draft", "state": "downloaded", "path": str(path), "role": "draft"},
    )
    db.put("profiles", "bad-legacy", {"id": "bad-legacy"})
    db.put(
        "profiles",
        "good",
        {"id": "good", "name": "Ready recipe", "model_path": str(path), "tool_calling": True},
    )
    db.put(
        "profiles",
        "foreign",
        {
            "id": "foreign",
            "name": "Wrong model",
            "model_path": str(tmp_path / "other"),
            "tool_calling": True,
        },
    )
    validate = __import__("unittest.mock", fromlist=["Mock"]).Mock(return_value=({}, {}))
    monkeypatch.setattr(engine, "validate_profile", validate)
    db.put("engine", "active", {"state": "stopped", "profile": None})
    assert [m["id"] for m in model_controls.choices(True)["items"]] == ["target"]
    assert not model_controls.choices()["items"][0]["active"]
    assert model_controls.resolve_profile("target", agent=True)["id"] == "good"
    validate.assert_called_once()
    with pytest.raises(HTTPException):
        model_controls.resolve_profile("target", "foreign")
    with pytest.raises(HTTPException):
        model_controls.resolve_profile("draft")
    (path / "config.json").unlink()
    assert not model_controls.choices()["items"][0]["can_load"]


@pytest.mark.asyncio
async def test_request_conflict_and_old_turn_count(project, model, monkeypatch):
    from onecat.agent import runtime

    monkeypatch.setattr(runtime, "info", lambda: {"installed": True, "sandbox_ready": True})

    async def hold(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(tasks.Run, "execute", hold)
    record = {
        "id": "legacy",
        "project_id": project["id"],
        "codex_version": runtime.VERSION,
        "state": "completed",
        "request_ids": [],
        "items": [{"type": "userMessage", "text": "one"}, {"type": "userMessage", "text": "two"}],
        "model_calls": 7,
    }
    db.put("agent_tasks", "legacy", record)
    result = tasks.start(project["id"], "three", "same-request", task_id="legacy")
    assert result["turn_count"] == 3
    assert result["timing_missing_calls"] == 7
    assert tasks.start(project["id"], "three", "same-request", task_id="legacy")["id"] == "legacy"
    with pytest.raises(HTTPException):
        tasks.start(project["id"], "different", "same-request", task_id="legacy")
    await tasks.cancel("legacy")


@pytest.mark.asyncio
async def test_upload_prevents_concurrent_task_start(project, monkeypatch):
    from onecat.agent.api import upload_file
    from starlette.datastructures import UploadFile
    import io

    upload = UploadFile(filename="file.txt", file=io.BytesIO(b"hello"))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def read(n):
        entered.set()
        await release.wait()
        return b"hello"

    monkeypatch.setattr(upload, "read", read)
    worker = asyncio.create_task(upload_file(project["id"], upload))
    await entered.wait()
    with pytest.raises(HTTPException, match="uploading"):
        tasks.require_idle(project["id"])
    release.set()
    await worker
    tasks.require_idle(project["id"])
    assert projects.read(project["id"], "file.txt") == b"hello"


@pytest.mark.asyncio
async def test_empty_or_early_stream_error_keeps_original_reply(monkeypatch):
    tid = thread()
    original = studio_db.list_chat_messages(tid)
    for index, events_text in enumerate(
        ["data: [DONE]\n\n", 'data: {"error":{"message":"early stream failure"}}\n\n']
    ):

        async def events():
            yield events_text

        monkeypatch.setattr(proxy, "forward", AsyncMock(return_value=StreamingResponse(events())))
        chat_runs.start(
            tid,
            {
                "message_id": f"error-{index}",
                "request": {},
                "messages": original,
                "expected_message_ids": [m["id"] for m in original],
            },
        )
        await chat_runs._tasks[tid]
        assert chat_runs.current(tid)["state"] == "failed"
        assert studio_db.list_chat_messages(tid) == original
