# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Management records; conversations are stored by onecat.chat_store."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .config import state_root

_schema_lock = threading.Lock()
_schema_files = {}


def uid() -> str:
    return uuid.uuid4().hex


@contextlib.contextmanager
def connect():
    db = state_root() / "onecat.db"
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    stat = db.stat()
    identity = (stat.st_dev, stat.st_ino)
    with _schema_lock:
        if _schema_files.get(str(db)) != identity:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
              CREATE TABLE IF NOT EXISTS records (
                bucket TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
                updated REAL NOT NULL, PRIMARY KEY(bucket,id));
              CREATE TABLE IF NOT EXISTS credentials (
                digest TEXT PRIMARY KEY, name TEXT NOT NULL, scope TEXT NOT NULL,
                expires REAL, created REAL NOT NULL, prefix TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, started REAL NOT NULL, model TEXT, status INTEGER,
                elapsed REAL, ttft REAL, prompt_tokens INTEGER, completion_tokens INTEGER,
                error TEXT, source TEXT);
              PRAGMA user_version=1;
            """)
            columns = {r[1] for r in conn.execute("PRAGMA table_info(requests)")}
            for name, definition in (
                ("metrics", "TEXT"),
                ("overlapped", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE requests ADD COLUMN {name} {definition}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS requests_chat_message ON requests(json_extract(metrics,'$.chat_message_id'),json_extract(metrics,'$.chat_thread_id'))"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS requests_started ON requests(started)")
            conn.execute("PRAGMA user_version=2")
            conn.commit()
            _schema_files[str(db)] = identity
            db.chmod(0o600)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def get(bucket: str, id: str, default=None):
    with connect() as conn:
        row = conn.execute(
            "SELECT data FROM records WHERE bucket=? AND id=?", (bucket, id)
        ).fetchone()
    return json.loads(row[0]) if row else default


def put(bucket: str, id: str, data: dict) -> dict:
    with connect() as conn:
        conn.execute(
            "INSERT INTO records VALUES(?,?,?,?) ON CONFLICT(bucket,id) "
            "DO UPDATE SET data=excluded.data, updated=excluded.updated",
            (bucket, id, json.dumps(data, ensure_ascii=False, allow_nan=False), time.time()),
        )
    return data


def patch(bucket: str, id: str, changes: dict) -> dict:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM records WHERE bucket=? AND id=?", (bucket, id)
        ).fetchone()
        data = json.loads(row[0]) if row else {"id": id}
        data.update(changes)
        conn.execute(
            "INSERT OR REPLACE INTO records VALUES(?,?,?,?)",
            (bucket, id, json.dumps(data, ensure_ascii=False, allow_nan=False), time.time()),
        )
    return data


def all_records(bucket: str) -> list[dict]:
    with connect() as conn:
        return [
            json.loads(r[0])
            for r in conn.execute(
                "SELECT data FROM records WHERE bucket=? ORDER BY updated DESC", (bucket,)
            )
        ]


def delete(bucket: str, id: str):
    with connect() as conn:
        conn.execute("DELETE FROM records WHERE bucket=? AND id=?", (bucket, id))


def settings() -> dict:
    stored = get("settings", "main", {})
    stored.pop("hf_endpoint", None)
    return {
        "model_directory": str(state_root() / "models"),
        "modelscope_endpoint": "https://modelscope.cn",
        "idle_unload_minutes": 0,
        "autostart_profile": None,
        "update_channel": None,
        "host": "127.0.0.1",
        "port": 8888,
        "locale": "zh-CN",
        "theme": "system",
        **stored,
    }


def backup_database(source: Path, destination: Path):
    if not source.is_file():
        return
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
    destination.chmod(0o600)
