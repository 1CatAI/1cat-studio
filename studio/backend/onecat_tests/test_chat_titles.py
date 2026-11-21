import asyncio
import json

import pytest
from starlette.responses import JSONResponse

from onecat import chat_store, chat_titles, proxy


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "renamed", "deleted"])
async def test_title_after_first_success_preserves_later_user_changes(monkeypatch, change):
    thread = chat_store.upsert_chat_thread({"id": "sample", "title": "", "modelId": "served-alias"})
    release, entered = asyncio.Event(), asyncio.Event()
    calls = []

    async def summarize(request, endpoint, source):
        body = await request.json()
        assert body["model"] == thread["modelId"] and source == "chat_title"
        assert not body["stream"] and endpoint == "/v1/chat/completions"
        turns = json.loads(body["messages"][-1]["content"])
        assert turns == {"用户": "比较两种前缀缓存方案", "回复": "两种方案的差别在显存与延迟。"}
        calls.append(body)
        entered.set()
        await release.wait()
        return JSONResponse({"choices": [{"message": {"content": "「前缀缓存方案对比」"}}]})

    monkeypatch.setattr(proxy, "forward", summarize)
    history = [
        {"id": "old", "role": "user", "content": [{"type": "text", "text": "撤回的请求"}]},
        {"id": "cancelled", "role": "assistant", "metadata": {"generation_state": "cancelled"}},
        {"id": "question", "role": "user", "content": [{"type": "text", "text": "比较两种前缀缓存方案"}]},
    ]
    chat_store.sync_chat_messages("sample", history)
    assert await chat_titles.read("sample") == {"title": ""}
    assert not calls
    chat_store.sync_chat_messages("sample", [{"id": "answer", "role": "assistant",
        "metadata": {"generation_state": "completed"}, "content": [
            {"type": "reasoning", "text": "internal reasoning"},
            {"type": "text", "text": "两种方案的差别在显存与延迟。"}]}])
    chat_titles.schedule("sample")
    await asyncio.wait_for(entered.wait(), 2)
    assert chat_store.get_chat_thread("sample")["title"] == ""
    waiter = asyncio.create_task(chat_titles.read("sample"))
    await asyncio.sleep(0)
    if change == "renamed":
        chat_store.update_chat_thread("sample", {"title": "我的标题"})
    elif change == "deleted":
        chat_store.delete_chat_threads(["sample"])
    release.set()
    assert (await waiter)["title"] == {"none": "前缀缓存方案对比", "renamed": "我的标题", "deleted": ""}[change]
    assert len(calls) == 1
    if change == "deleted":
        assert chat_store.get_chat_thread("sample") is None
    else:
        chat_titles.schedule("sample")
        assert len(calls) == 1
