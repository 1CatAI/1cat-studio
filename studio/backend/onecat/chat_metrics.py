# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Keep persisted/exported chat metrics aligned with the completed request record."""

import json
import sqlite3

from . import db
from .config import state_root


def hydrate(message):
    if message.get("role") not in {None, "assistant"} or (
        message.get("metadata") is not None and not isinstance(message["metadata"], dict)
    ):
        return message
    metadata = dict(message.get("metadata") or {})
    with db.connect() as conn:
        if metadata.get("request_id"):
            row = conn.execute(
                "SELECT * FROM requests WHERE id=?", (metadata["request_id"],)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM requests WHERE json_extract(metrics,'$.chat_message_id')=? AND json_extract(metrics,'$.chat_thread_id')=? ORDER BY started DESC LIMIT 1",
                (message.get("id"), message.get("threadId")),
            ).fetchone()
    if row and row["metrics"]:
        metrics = json.loads(row["metrics"])
        usage = {**(metadata.get("usage") or {})}
        for key in ("prompt_tokens", "completion_tokens"):
            if row[key] is not None:
                usage[key] = row[key]
        metadata.update({"request_id": row["id"], "timing": metrics, "usage": usage})
        message["metadata"] = metadata
    return message


def commit(request_id, metrics):
    id, thread = metrics.get("chat_message_id"), metrics.get("chat_thread_id")
    path = state_root() / "studio.db"
    if not id or not thread or not path.exists():
        return
    with sqlite3.connect(path, timeout=20) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT metadata_json FROM chat_messages WHERE id=? AND thread_id=? AND role='assistant'",
            (id, thread),
        ).fetchone()
        if not row:
            return
        metadata = json.loads(row[0] or "{}")
        if metadata.get("request_id") not in {None, request_id}:
            return
        updated = hydrate(
            {"id": id, "threadId": thread, "metadata": {**metadata, "request_id": request_id}}
        )
        conn.execute(
            "UPDATE chat_messages SET metadata_json=? WHERE id=? AND thread_id=?",
            (json.dumps(updated["metadata"]), id, thread),
        )
