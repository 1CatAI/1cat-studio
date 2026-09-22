# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException

from onecat import chat_runs, chat_store, db, model_controls
from onecat_tests.test_chat_runs import fixture, thread
from onecat_tests.test_agent import project as project, model as model, require_runtime, FakeStream


@pytest.fixture
def profile(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "chat_template.jinja").write_text("""
{% if enable_thinking %}
{% set resolved_reasoning_effort = reasoning_effort|default('xhigh') %}
{% if resolved_reasoning_effort not in ('xhigh', 'medium', 'low') %}
{{ raise_exception('unsupported') }}
{% endif %}
{% endif %}
""")
    return {"name": "custom preset", "model_path": str(tmp_path), "served_model_name": "local"}


def test_native_template_levels_and_toggle_only_models(profile, tmp_path):
    support = model_controls.thinking_support(profile)
    assert support["efforts"] == ["low", "medium", "xhigh"]
    assert support["default_effort"] == "xhigh"
    assert model_controls.thinking_kwargs(profile, True, "low") == {
        "enable_thinking": True,
        "reasoning_effort": "low",
    }
    # "high" is not silently sent to a template which explicitly rejects it.
    with pytest.raises(HTTPException):
        model_controls.thinking_kwargs(profile, True, "high")
    assert model_controls.thinking_kwargs(profile, False, "xhigh") == {"enable_thinking": False}
    (tmp_path / "chat_template.jinja").write_text("{% if enable_thinking %}plain toggle{% endif %}")
    assert model_controls.thinking_support(profile)["efforts"] == []
    assert model_controls.thinking_kwargs(profile, True) == {"enable_thinking": True}


def test_picker_and_loading_identity_use_registered_repository(client, profile, monkeypatch):
    from onecat import engine

    db.put(
        "models",
        "model",
        {
            "id": "model",
            "state": "downloaded",
            "name": "Qwen3.8-27B-NVFP4",
            "path": profile["model_path"],
            "repo_id": "unsloth/Qwen3.8-27B-NVFP4",
            "source": "modelscope",
        },
    )
    db.put("profiles", "preset", {"id": "preset", **profile})
    before = db.all_records("models")
    item = client.get("/api/inference/models").json()["items"][0]
    assert item["repo_id"] == "unsloth/Qwen3.8-27B-NVFP4"
    monkeypatch.setattr(engine, "status", lambda: {"state": "loading", "profile": profile})
    current = client.get("/api/inference/status").json()["model"]
    assert current["repo_id"] == item["repo_id"]
    assert current["name"] == item["name"]
    assert db.all_records("models") == before  # Labels do not alter launch/catalog records.


@pytest.mark.asyncio
async def test_chat_strength_reaches_inference_and_persists(profile, monkeypatch):
    db.put("engine", "active", {"state": "ready", "profile": profile})
    calls = fixture(monkeypatch, length=1)
    tid = thread()
    messages = chat_store.list_chat_messages(tid)
    settings = {"thinking": True, "thinking_effort": "medium"}
    chat_runs.start(
        tid,
        {
            "message_id": "answer",
            "request": {"model": "local", "messages": []},
            "messages": messages,
            "expected_message_ids": [m["id"] for m in messages],
            "settings": settings,
        },
    )
    await asyncio.wait_for(chat_runs._tasks[tid], 5)
    assert calls[0]["chat_template_kwargs"] == {
        "enable_thinking": True,
        "reasoning_effort": "medium",
    }
    assert chat_store.get_chat_thread(tid)["settings"]["thinking_effort"] == "medium"


@pytest.mark.asyncio
async def test_agent_strength_reaches_model_and_survives_continue(
    profile, project, model, monkeypatch, tmp_path  # noqa: F811 - shared pytest fixtures
):
    from onecat.agent import tasks

    require_runtime()
    model["profile"].update(profile)
    db.put("engine", "active", model)
    seen = []

    async def fake(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            stream=FakeStream(
                {"choices": [{"delta": {"content": "Done"}, "finish_reason": "stop"}]}
            ),
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(fake), **kw)
    )
    task = tasks.start(
        project["id"], "hello", "strength-first", thinking=True, thinking_effort="low"
    )
    await asyncio.wait_for(tasks.workers[task["id"]], 30)
    assert tasks.get(task["id"])["state"] == "completed"
    assert seen[-1]["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "low"}
    tasks.start(project["id"], "continue", "strength-next", task_id=task["id"])
    await asyncio.wait_for(tasks.workers[task["id"]], 30)
    assert tasks.get(task["id"])["thinking_effort"] == "low"
    assert seen[-1]["chat_template_kwargs"]["reasoning_effort"] == "low"
    # A retry is still the same turn; changing its strength is a conflicting request.
    assert (
        tasks.start(project["id"], "hello", "strength-first", thinking=True, thinking_effort="low")[
            "id"
        ]
        == task["id"]
    )
    with pytest.raises(HTTPException):
        tasks.start(
            project["id"], "hello", "strength-first", thinking=True, thinking_effort="medium"
        )
    # Continuing on a toggle-only model must not retain an unsupported level.
    (tmp_path / "chat_template.jinja").write_text("{% if enable_thinking %}plain toggle{% endif %}")
    tasks.start(project["id"], "continue on this model", "strength-toggle", task_id=task["id"])
    await asyncio.wait_for(tasks.workers[task["id"]], 30)
    assert tasks.get(task["id"])["thinking_effort"] is None
    assert seen[-1]["chat_template_kwargs"] == {"enable_thinking": True}
