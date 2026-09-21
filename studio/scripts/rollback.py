#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Roll back a manager release and its pre-upgrade database snapshot."""

import json
import fcntl
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

prefix = (
    Path(sys.argv[1]).expanduser().resolve()
    if len(sys.argv) > 1
    else Path.home() / ".local/share/onecat-studio-app"
)
previous = (prefix / "previous").resolve()
deployment_lock = (prefix / "upgrade.lock").open("a")
try:
    fcntl.flock(deployment_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("Another Studio upgrade or rollback is in progress")
if not previous.is_dir():
    raise SystemExit("No previous release is available")
info = json.loads((prefix / "installation.json").read_text())
state = Path(info["state"])
backup = Path(info["backup"])
with sqlite3.connect(state / "onecat.db") as conn:
    engine = conn.execute(
        "SELECT data FROM records WHERE bucket='engine' AND id='active'"
    ).fetchone()
    jobs = [json.loads(r[0]) for r in conn.execute("SELECT data FROM records WHERE bucket='jobs'")]
    agents = [json.loads(r[0]) for r in conn.execute("SELECT data FROM records WHERE bucket='agent_tasks'")]
    if any(t["state"] in {"starting", "running", "waiting", "stopping"} or t.get("settled") is False for t in agents):
        raise SystemExit("Stop Agent tasks before rolling back; saved task files remain available")
    if (engine and json.loads(engine[0]).get("state") != "stopped") or any(
        j["state"] not in ("failed", "cancelled", "completed") for j in jobs
    ):
        raise SystemExit("Stop the model and background tasks before rolling back")
if info.get("service", True):
    subprocess.run(["systemctl", "--user", "stop", "onecat-studio.service"], check=True)
# Preserve work created after the upgrade before restoring the older schema.
checkpoint = state / "backups" / ("before-rollback-" + str(time.time_ns()))
checkpoint.mkdir(parents=True)
for name in ("onecat.db", "studio.db"):
    if (state / name).is_file():
        with sqlite3.connect(state / name) as src, sqlite3.connect(checkpoint / name) as dst:
            src.backup(dst)
        (checkpoint / name).chmod(0o600)
for name in ("onecat.db", "studio.db"):
    if (backup / name).is_file():
        with sqlite3.connect(backup / name) as src, sqlite3.connect(state / name) as dst:
            src.backup(dst)
current = (prefix / "current").resolve()
temp = prefix / "current.new"
temp.unlink(missing_ok=True)
temp.symlink_to(previous)
temp.replace(prefix / "current")
(prefix / "previous").unlink()
(prefix / "previous").symlink_to(current)
if info.get("service", True):
    subprocess.run(["systemctl", "--user", "start", "onecat-studio.service"], check=True)
print("Restored " + str(previous))
