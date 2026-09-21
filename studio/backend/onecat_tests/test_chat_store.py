# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Persisted conversation compatibility and isolation at the storage boundary."""
import sqlite3

import pytest

from onecat import chat_store as store


def test_existing_database_survives_upgrade_and_preserves_unknown_data(state):
    path = state / "studio.db"
    # On-disk schema contract, independent of either implementation.
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE chat_threads (id TEXT PRIMARY KEY, title TEXT, model_type TEXT,
                model_id TEXT, archived INTEGER, created_at INTEGER, updated_at INTEGER,
                settings_json TEXT, legacy_extension TEXT);
            CREATE TABLE chat_messages (id TEXT PRIMARY KEY, thread_id TEXT, parent_id TEXT,
                role TEXT, content_json TEXT, attachments_json TEXT, metadata_json TEXT,
                created_at INTEGER);
            CREATE TABLE unrelated_training_data (payload TEXT);
            INSERT INTO unrelated_training_data VALUES ('keep');
            INSERT INTO chat_threads VALUES ('old', '旧对话', 'text', 'model', 0, 100, 101,
                '{"temperature":0.7}', 'extension');
            INSERT INTO chat_messages VALUES ('answer', 'old', NULL, 'assistant',
                '[{"type":"text","text":"中文🙂"}]', NULL, '{"timing":{"decode":2}}', 101);
        """)
    assert store.get_chat_thread("old")["settings"] == {"temperature": .7}
    assert store.get_chat_message("old", "answer")["content"][0]["text"] == "中文🙂"
    assert store.update_chat_thread("old", {"title": "改名"})["title"] == "改名"
    store.sync_chat_messages("old", [{"id": "answer", "content": [{"type": "text", "text": "continued"}]}])
    assert store.get_chat_message("old", "answer")["metadata"] == {"timing": {"decode": 2}}
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT legacy_extension FROM chat_threads").fetchone()[0] == "extension"
        assert conn.execute("SELECT payload FROM unrelated_training_data").fetchone()[0] == "keep"
    store.delete_chat_threads(["old"])
    assert store.list_chat_messages("old") == []


def test_existing_deletion_tombstones_are_respected(state):
    with sqlite3.connect(state / "studio.db") as conn:
        conn.execute("CREATE TABLE chat_thread_tombstones (id TEXT PRIMARY KEY, deleted_at INTEGER)")
        conn.execute("INSERT INTO chat_thread_tombstones VALUES ('deleted-before-upgrade', 123)")
    with pytest.raises(ValueError, match="deleted"):
        store.upsert_chat_thread({"id": "deleted-before-upgrade", "title": "stale"})


def test_cross_thread_collision_rolls_back_entire_replacement():
    for id in ["first", "second"]:
        store.upsert_chat_thread({"id": id, "title": id})
        store.sync_chat_messages(id, [{"id": id + "-message", "role": "user", "content": []}])
    before = store.list_chat_messages("first")
    with pytest.raises(ValueError, match="another conversation"):
        store.sync_chat_messages("first", [
            {"id": "new-message", "role": "user", "content": []},
            {"id": "second-message", "role": "assistant", "content": []},
        ], prune_missing=True)
    assert store.list_chat_messages("first") == before
    assert store.get_chat_message("second", "second-message")["role"] == "user"


def test_delete_prevents_stale_writer_resurrection_and_cascades_messages():
    store.upsert_chat_thread({"id": "deleted", "title": "old"})
    store.sync_chat_messages("deleted", [{"id": "message", "role": "user", "content": []}])
    store.delete_chat_threads(["deleted"])
    assert store.list_chat_messages("deleted") == []
    with pytest.raises(ValueError, match="deleted"):
        store.upsert_chat_thread({"id": "deleted", "title": "stale"})
    with pytest.raises(ValueError, match="not found"):
        store.sync_chat_messages("deleted", [{"id": "late", "role": "assistant", "content": []}])
