# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Idempotent data upgrades, with SQLite snapshots before changing user data."""

import json
import sqlite3
import time

from . import db
from .config import state_root


def upgrade():
    if db.get("migrations", "studio-v2"):
        upgrade_output_mode()
        upgrade_catalog_context()
        return
    folder = state_root() / "backups" / ("before-studio-v2-" + str(time.time_ns()))
    folder.mkdir(parents=True, mode=0o700)
    for name in ("onecat.db", "studio.db"):
        db.backup_database(state_root() / name, folder / name)
    for profile in db.all_records("profiles"):
        sampling = profile.get("default_sampling", {})
        if sampling.get("max_tokens") == 1024:
            sampling["max_tokens"] = None
            profile["default_sampling"] = sampling
        # Bring previously adopted parser flags under the structured field owner.
        args, remaining = profile.get("extra_args", []), []
        i = 0
        while i < len(args):
            flag = args[i].split("=", 1)[0]
            if flag == "--enable-auto-tool-choice":
                profile["tool_calling"] = True
            elif flag == "--tool-call-parser":
                if "=" in args[i]:
                    profile["tool_parser"] = args[i].split("=", 1)[1]
                elif i + 1 < len(args):
                    i += 1
                    profile["tool_parser"] = args[i]
            else:
                remaining.append(args[i])
            i += 1
        profile["extra_args"] = remaining
        db.put("profiles", profile["id"], profile)
    path = state_root() / "studio.db"
    if path.exists():
        with sqlite3.connect(path) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='chat_threads'").fetchone():
                for id, raw in conn.execute(
                    "SELECT id,settings_json FROM chat_threads WHERE settings_json IS NOT NULL"
                ).fetchall():
                    try:
                        settings = json.loads(raw)
                        if settings.get("max_tokens") == 1024:
                            settings["max_tokens"] = None
                            conn.execute(
                                "UPDATE chat_threads SET settings_json=? WHERE id=?",
                                (json.dumps(settings), id),
                            )
                    except (ValueError, AttributeError):
                        continue
    db.put("migrations", "studio-v2", {"completed_at": time.time(), "backup": str(folder)})
    upgrade_output_mode()
    upgrade_catalog_context()


def upgrade_output_mode():
    if db.get("migrations", "chat-output-mode-v1"):
        return
    from .chat_settings import normalize

    folder = state_root() / "backups" / ("before-output-mode-" + str(time.time_ns()))
    folder.mkdir(parents=True, mode=0o700)
    for name in ("onecat.db", "studio.db"):
        db.backup_database(state_root() / name, folder / name)
    path = state_root() / "studio.db"
    if path.exists():
        with sqlite3.connect(path) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='chat_threads'").fetchone():
                for id, raw in conn.execute("SELECT id,settings_json FROM chat_threads").fetchall():
                    settings = normalize(json.loads(raw or "{}"))
                    conn.execute(
                        "UPDATE chat_threads SET settings_json=? WHERE id=?",
                        (json.dumps(settings), id),
                    )
    db.put(
        "migrations", "chat-output-mode-v1", {"completed_at": time.time(), "backup": str(folder)}
    )


def upgrade_catalog_context():
    """Move untouched Qwen catalog defaults from the old 32K placeholder to 256K."""
    if db.get("migrations", "qwen-catalog-context-v1"):
        return
    folder = state_root() / "backups" / ("before-qwen-context-" + str(time.time_ns()))
    folder.mkdir(parents=True, mode=0o700)
    db.backup_database(state_root() / "onecat.db", folder / "onecat.db")
    updated = []
    for profile in db.all_records("profiles"):
        if (
            profile.get("source") == "catalog-default"
            and str(profile.get("catalog_id", "")).startswith(
                ("QuantTrio/Qwen3.6-", "Qwen/Qwen3.6-", "Qwen/Qwen3.8-")
            )
            and profile.get("max_model_len") == 32768
        ):
            profile["max_model_len"] = 262144
            db.put("profiles", profile["id"], profile)
            updated.append(profile["id"])
    db.put(
        "migrations",
        "qwen-catalog-context-v1",
        {"completed_at": time.time(), "backup": str(folder), "updated_profiles": updated},
    )
