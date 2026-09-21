# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from . import db, engine
from .config import state_root
from .jobs import TERMINAL, list_jobs
from .runtimes import extract_archive


def restore_backup(path: str):
    from .creative.services import instance

    if any(instance(s["id"]).get("state") in {"ready", "loading", "stopping"} for s in db.all_records("creative_services") if s["kind"] == "h3-local"):
        raise ValueError("Stop owned creative model services before restoring a backup")
    if engine.status().get("state") != "stopped" or engine.active_requests():
        raise ValueError("Stop the model before restoring a backup")
    if any(j["state"] not in TERMINAL for j in list_jobs()):
        raise ValueError("Wait for background tasks to finish before restoring")
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError("Backup archive not found")
    with tempfile.TemporaryDirectory(prefix="restore-", dir=state_root() / "backups") as tmp:
        folder = Path(tmp)
        extract_archive(source, folder)
        manifest = json.loads((folder / "manifest.json").read_text())
        if manifest.get("format") != "onecat-backup-v1":
            raise ValueError("Unsupported backup format")
        for name in ("onecat.db", "studio.db"):
            candidate = folder / name
            if not candidate.is_file():
                if name == "onecat.db":
                    raise ValueError("Backup has no management database")
                continue
            with sqlite3.connect(candidate) as check:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Backup database failed integrity validation")
        for name in ("onecat.db", "studio.db"):
            candidate = folder / name
            if not candidate.is_file():
                if name == "onecat.db":
                    raise ValueError("Backup has no management database")
                continue
            with sqlite3.connect(candidate) as check:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Backup database failed integrity validation")
            destination = state_root() / name
            db.backup_database(destination, state_root() / "backups" / (name + ".before-restore"))
            with sqlite3.connect(candidate) as src, sqlite3.connect(destination) as dst:
                src.backup(dst)
        if (folder / "attachments").is_dir():
            destination = state_root() / "attachments"
            if destination.exists():
                old = state_root() / "backups" / ("attachments-before-restore-" + db.uid())
                destination.rename(old)
            shutil.copytree(folder / "attachments", destination)
        from .migrations import upgrade

        upgrade()
        # Historical PIDs and in-flight operations cannot be resurrected by restoring settings.
        with db.connect() as conn:
            conn.execute("DELETE FROM credentials WHERE scope='admin'")
            conn.execute("DELETE FROM records WHERE bucket IN ('engine','jobs','hardware','creative_instances')")
            conn.execute("UPDATE records SET data=json_set(data,'$.state','failed','$.error','Backup restored; check the original backend task before retrying') WHERE bucket='creative_runs' AND json_extract(data,'$.state') NOT IN ('completed','failed','cancelled')")
            conn.execute(
                "UPDATE requests SET status=499,error='Backup restored' WHERE status IS NULL"
            )
    return {"ok": True, "login_required": True}
