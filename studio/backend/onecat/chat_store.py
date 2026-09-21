# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""1Cat conversation persistence, compatible with existing studio.db records.

The SQL column names and JSON response fields are the on-disk and HTTP data
contracts. This module owns its transactions and does not import the previous
Studio storage package. Unknown legacy tables and columns are left in place.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from .config import state_root


THREAD_FIELDS = {
    "id": "id", "title": "title", "modelType": "model_type",
    "modelId": "model_id", "modelGgufVariant": "model_gguf_variant",
    "pairId": "pair_id", "projectId": "project_id", "archived": "archived",
    "createdAt": "created_at", "updatedAt": "updated_at",
    "openaiCodeExecContainerId": "openai_code_exec_container_id",
    "anthropicCodeExecContainerId": "anthropic_code_exec_container_id",
    "forkedFromThreadId": "forked_from_thread_id",
    "forkedFromMessageId": "forked_from_message_id", "settings": "settings_json",
}
MESSAGE_FIELDS = {
    "id": "id", "threadId": "thread_id", "parentId": "parent_id",
    "role": "role", "content": "content_json", "attachments": "attachments_json",
    "metadata": "metadata_json", "createdAt": "created_at",
}
JSON_FIELDS = {"settings", "content", "attachments", "metadata"}


@contextmanager
def connection():
    conn = sqlite3.connect(state_root() / "studio.db", timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS chat_threads (
            id TEXT PRIMARY KEY NOT NULL, title TEXT NOT NULL, model_type TEXT NOT NULL,
            model_id TEXT, model_gguf_variant TEXT, pair_id TEXT, project_id TEXT,
            archived INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
            updated_at INTEGER, openai_code_exec_container_id TEXT,
            anthropic_code_exec_container_id TEXT, forked_from_thread_id TEXT,
            forked_from_message_id TEXT, settings_json TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
            id TEXT PRIMARY KEY NOT NULL,
            thread_id TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
            parent_id TEXT, role TEXT NOT NULL, content_json TEXT NOT NULL,
            attachments_json TEXT, metadata_json TEXT, created_at INTEGER NOT NULL)""")
        conn.execute("CREATE TABLE IF NOT EXISTS onecat_chat_deleted (id TEXT PRIMARY KEY)")
        conn.execute("CREATE INDEX IF NOT EXISTS onecat_message_order ON chat_messages(thread_id, created_at)")
        with conn:
            yield conn
    finally:
        conn.close()


def now():
    return int(time.time() * 1000)


def decode(row, fields):
    if row is None:
        return None
    result = {}
    for key, column in fields.items():
        value = row[column] if column in row.keys() else None
        if key in JSON_FIELDS and value is not None:
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                value = [] if key == "content" else None
        result[key] = value
    if fields is THREAD_FIELDS:
        result["archived"] = bool(result["archived"])
        result["modelId"] = result["modelId"] or ""
        result["updatedAt"] = result["updatedAt"] if result["updatedAt"] is not None else result["createdAt"]
    else:
        for key in ("attachments", "metadata"):
            if result[key] is None:
                del result[key]
    return result


def write(conn, table, record, fields):
    available = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    keys = [key for key in fields if key in record and fields[key] in available]
    columns = [fields[key] for key in keys]
    values = [json.dumps(record[key], ensure_ascii=False) if key in JSON_FIELDS and record[key] is not None
              else int(record[key]) if key == "archived" else record[key] for key in keys]
    updates = ",".join(f"{column}=excluded.{column}" for column in columns if column != "id")
    conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in keys)}) "
                 f"ON CONFLICT(id) DO UPDATE SET {updates}", values)


def get_chat_thread(id: str):
    with connection() as conn:
        return decode(conn.execute("SELECT * FROM chat_threads WHERE id=?", (id,)).fetchone(), THREAD_FIELDS)


def list_chat_threads(model_type=None, include_archived=False, **_):
    clauses, values = [], []
    if model_type is not None:
        clauses.append("model_type=?")
        values.append(model_type)
    if not include_archived:
        clauses.append("archived=0")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connection() as conn:
        return [decode(row, THREAD_FIELDS) for row in conn.execute(
            "SELECT * FROM chat_threads" + where + " ORDER BY COALESCE(updated_at,created_at) DESC, id", values)]


def upsert_chat_thread(thread: dict):
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM onecat_chat_deleted WHERE id=?", (thread["id"],)).fetchone():
            raise ValueError("Conversation has been deleted")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_thread_tombstones'").fetchone():
            if conn.execute("SELECT 1 FROM chat_thread_tombstones WHERE id=?", (thread["id"],)).fetchone():
                raise ValueError("Conversation has been deleted")
        existing = decode(conn.execute("SELECT * FROM chat_threads WHERE id=?", (thread["id"],)).fetchone(), THREAD_FIELDS)
        value = {"title": "", "modelType": "text", "archived": False, "createdAt": now(),
                 "modelId": "", **(existing or {}), **thread}
        write(conn, "chat_threads", value, THREAD_FIELDS)
        return decode(conn.execute("SELECT * FROM chat_threads WHERE id=?", (thread["id"],)).fetchone(), THREAD_FIELDS)


def update_chat_thread(id: str, patch: dict):
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = decode(conn.execute("SELECT * FROM chat_threads WHERE id=?", (id,)).fetchone(), THREAD_FIELDS)
        if current is None:
            return None
        current.update({**patch, "id": id})
        if "updatedAt" not in patch:
            current["updatedAt"] = now()
        write(conn, "chat_threads", current, THREAD_FIELDS)
        return decode(conn.execute("SELECT * FROM chat_threads WHERE id=?", (id,)).fetchone(), THREAD_FIELDS)


def list_chat_messages(thread_id: str):
    with connection() as conn:
        return messages(conn, thread_id)


def messages(conn, thread_id):
    return [decode(row, MESSAGE_FIELDS) for row in conn.execute(
        "SELECT * FROM chat_messages WHERE thread_id=? ORDER BY created_at, rowid", (thread_id,))]


def get_chat_message(thread_id: str, message_id: str):
    with connection() as conn:
        return decode(conn.execute("SELECT * FROM chat_messages WHERE thread_id=? AND id=?",
                                   (thread_id, message_id)).fetchone(), MESSAGE_FIELDS)


def sync_chat_messages(thread_id: str, records: list[dict], prune_missing=False):
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM chat_threads WHERE id=?", (thread_id,)).fetchone():
            raise ValueError("Conversation not found")
        ids = [item["id"] for item in records]
        if len(ids) != len(set(ids)):
            raise ValueError("Message IDs must be unique")
        for item in records:
            previous = decode(conn.execute("SELECT * FROM chat_messages WHERE id=?", (item["id"],)).fetchone(), MESSAGE_FIELDS)
            if previous and previous["threadId"] != thread_id:
                raise ValueError("Message belongs to another conversation")
            if item.get("threadId", thread_id) != thread_id:
                raise ValueError("Message belongs to another conversation")
            value = {"parentId": None, "createdAt": now(), "content": [], **(previous or {}),
                     **item, "threadId": thread_id}
            write(conn, "chat_messages", value, MESSAGE_FIELDS)
        if prune_missing:
            keep = set(ids)
            for row in conn.execute("SELECT id FROM chat_messages WHERE thread_id=?", (thread_id,)).fetchall():
                if row["id"] not in keep:
                    conn.execute("DELETE FROM chat_messages WHERE id=?", (row["id"],))
        return messages(conn, thread_id)


def delete_chat_threads(ids: list[str]):
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        deleted = []
        for id in ids:
            conn.execute("INSERT OR IGNORE INTO onecat_chat_deleted(id) VALUES (?)", (id,))
            conn.execute("DELETE FROM chat_messages WHERE thread_id=?", (id,))
            if conn.execute("DELETE FROM chat_threads WHERE id=?", (id,)).rowcount:
                deleted.append(id)
        return deleted
