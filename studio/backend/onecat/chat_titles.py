# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Summarize a completed first turn without holding its event stream open."""
import asyncio
import json
import logging
import re

from fastapi import HTTPException, Request

from . import chat_store, proxy

_pending: dict[str, asyncio.Task] = {}
logger = logging.getLogger(__name__)


def user_text(message):
    content = message.get("content", [])
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content if p.get("type") == "text")


def excerpt(text, limit):
    if len(text) <= limit:
        return text
    head = limit * 3 // 4
    return text[:head] + "…" + text[-(limit - head):]


def clean_title(text):
    if not isinstance(text, str):
        return ""
    text = re.sub(r"<think>.*?(?:</think>|$)", "", text, flags=re.S | re.I).strip()
    if not text:
        return ""
    line = re.sub(r"^(?:标题|Title)\s*[:：]\s*", "", text.splitlines()[0], flags=re.I)
    return line.strip(" \t#*「」『』《》“”\"'`。")[:12].rstrip()


async def generate(thread, model, question, answer):
    request = Request({"type": "http", "method": "POST", "path": "/v1/chat/completions",
                       "headers": [(b"x-onecat-thread-id", thread.encode())]})
    request._json = {
        "model": model, "stream": False, "max_tokens": 64,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": "为下面这轮对话起一个简洁具体的中文标题，目标10个字，最多12个字。保留必要的专名。只输出标题，不加引号、前缀或解释。"},
            {"role": "user", "content": json.dumps({"用户": excerpt(question, 1600),
                "回复": excerpt(answer, 1400)}, ensure_ascii=False)},
        ],
    }
    try:
        response = await asyncio.wait_for(proxy.forward(request, "/v1/chat/completions", "chat_title"), 90)
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code}")
        title = clean_title(json.loads(response.body)["choices"][0]["message"]["content"])
        if title:
            chat_store.set_title_if_empty(thread, title)
    except Exception as error:
        logger.warning("Could not summarize chat %s (%s)", thread, type(error).__name__)


def schedule(thread, model=None):
    if thread in _pending:
        return
    current = chat_store.get_chat_thread(thread)
    if current is None or current["title"]:
        return
    alias = model or current["modelId"]
    if not alias:
        return
    history = chat_store.list_chat_messages(thread)
    users = {m["id"]: user_text(m) or "图片消息" for m in history if m["role"] == "user"}
    question = None
    for message in history:
        if message["role"] == "user":
            question = users[message["id"]]
        elif message["role"] == "assistant" and (message.get("metadata") or {}).get("generation_state") == "completed":
            question = users.get(message.get("parentId"), question)
            if question is None:
                continue
            task = asyncio.create_task(generate(thread, alias, question, user_text(message)))
            _pending[thread] = task
            task.add_done_callback(lambda _: _pending.pop(thread, None))
            return


async def read(thread):
    current = chat_store.get_chat_thread(thread)
    if current is None:
        raise HTTPException(404, "Conversation not found")
    if current["title"]:
        return {"title": current["title"]}
    # Also resumes a summary interrupted by a manager restart.
    schedule(thread)
    task = _pending.get(thread)
    if task:
        await asyncio.shield(task)
    saved = chat_store.get_chat_thread(thread)
    return {"title": saved["title"] if saved else ""}


async def shutdown():
    jobs = list(_pending.values())
    for job in jobs:
        job.cancel()
    if jobs:
        await asyncio.gather(*jobs, return_exceptions=True)
