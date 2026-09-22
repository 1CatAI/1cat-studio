# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""VPS release checks and an independently supervised OTA transaction."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from . import __version__, db, update_protocol as protocol
from .config import state_root

DEFAULT_CHANNEL = "http://156.226.174.161:8089/onecat/studio/latest.json"
APP_ROOT = Path(__file__).resolve().parents[3]
CACHE_SECONDS = 900
_cache: dict = {}
TERMINAL = {"installed", "rolled_back", "failed"}


def channel_url() -> str:
    return protocol.validate_url(
        str(
            db.settings().get("update_channel")
            or os.environ.get("ONECAT_UPDATE_CHANNEL")
            or DEFAULT_CHANNEL
        )
    )


def prefix() -> Path:
    if configured := os.environ.get("ONECAT_STUDIO_PREFIX"):
        return Path(configured).expanduser().resolve()
    if APP_ROOT.parent.name == "releases":
        return APP_ROOT.parent.parent
    return (Path.home() / ".local/share/onecat-studio-app").resolve()


def deployment() -> dict:
    """Describe the running code, not an abandoned installation's current link."""
    path = APP_ROOT / "manifest.json"
    if path.is_file():
        manifest = json.loads(path.read_text())
        if manifest.get("format") == "onecat-studio-linux-v1":
            return {
                "mode": "package",
                "version": manifest["version"],
                "source_commit": manifest.get("source_commit"),
                "sequence": manifest.get("sequence", 0),
            }
    sha = None
    try:
        result = subprocess.run(
            ["git", "-C", str(APP_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        sha = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "mode": "source",
        "version": __version__ + ("-dev." + sha[:8] if sha else "-dev"),
        "source_commit": sha,
        "sequence": 0,
    }


def installed_version() -> str:
    return deployment()["version"]


def updates_root() -> Path:
    root = state_root() / "updates"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def read_status() -> dict:
    try:
        return json.loads((updates_root() / "status.json").read_text())
    except (OSError, ValueError):
        return {}


def activating() -> bool:
    # Interrupted activation remains closed to writes until recovery completes.
    return (state_root() / "updates/activation.json").is_file()


def write_status(**fields) -> dict:
    value = {"updated_at": time.time(), **fields}
    protocol.atomic_json(updates_root() / "status.json", value)
    return value


def fetch(force: bool = False) -> dict:
    url = channel_url()
    if not force and _cache.get("url") == url and time.time() - _cache.get("at", 0) < CACHE_SECONDS:
        return _cache["manifest"]
    request = Request(
        url, headers={"User-Agent": "onecat-studio-ota/1", "Cache-Control": "no-cache"}
    )
    with urlopen(request, timeout=15) as response:
        manifest = protocol.verify(protocol.parse_manifest(response.read(65537)))
    ledger_path = updates_root() / "channel-history.json"
    with (updates_root() / "channel.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
        previous = ledger.get(url, {})
        identity = protocol.release_id(manifest)
        if manifest["sequence"] < previous.get("sequence", 0):
            raise ValueError("The update channel returned an older release")
        if manifest["sequence"] == previous.get("sequence") and identity != previous.get(
            "release_id"
        ):
            raise ValueError("An existing release was changed; publish a new sequence")
        ledger[url] = {"sequence": manifest["sequence"], "release_id": identity}
        protocol.atomic_json(ledger_path, ledger)
    _cache.update(url=url, at=time.time(), manifest=manifest)
    return manifest


def busy_reason() -> str | None:
    from .ota import busy_reason as inspect

    return inspect(state_root())


def check(force: bool = False) -> dict:
    current = deployment()
    result = {
        "current": current["version"],
        "deployment": current,
        "latest": None,
        "available": False,
        "release_id": None,
        "notes": "",
        "size": None,
        "published": None,
        "verified": False,
        "status": read_status(),
        "error": None,
        "blocked_reason": busy_reason(),
        "supported": protocol.supported_host(),
    }
    try:
        manifest = fetch(force)
        result.update(
            latest=manifest["version"],
            release_id=protocol.release_id(manifest),
            available=manifest["sequence"] > current["sequence"]
            and manifest["version"] != current["version"],
            notes=manifest.get("notes", ""),
            size=manifest["size"],
            published=manifest.get("release_date"),
            verified=True,
        )
    except (OSError, ValueError, KeyError) as error:
        result["error"] = str(error)[:300]
    return result


def systemd_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def start(manifest: dict, *, port: int, switch_to_release: bool = False) -> dict:
    manifest = protocol.verify(manifest)
    if not protocol.supported_host():
        raise ValueError("OTA currently supports Linux x86_64")
    current = deployment()
    if current["mode"] == "source" and not switch_to_release:
        raise ValueError("Confirm switching from source to a published release")
    if manifest["version"] == current["version"] or manifest["sequence"] <= current["sequence"]:
        raise ValueError("This release is already installed or older than the running release")
    if reason := busy_reason():
        raise ValueError(reason)
    root = updates_root()
    with (root / "request.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = read_status()
        if old.get("stage") and old["stage"] not in TERMINAL:
            raise ValueError("A Studio update is already in progress")
        if activating():
            raise ValueError("An interrupted Studio update needs recovery")
        operation = uuid.uuid4().hex
        work = root / "operations" / operation
        worker = work / "onecat_ota"
        worker.mkdir(parents=True, mode=0o700)
        (worker / "__init__.py").write_text("")
        for name in ("ota.py", "update_protocol.py"):
            shutil.copy2(Path(__file__).with_name(name), worker / name)
        (worker / "data").mkdir()
        shutil.copy2(protocol.KEYS, worker / "data/update-keys.json")
        protocol.atomic_json(work / "release.json", manifest)
        manager = os.environ.get("ONECAT_STUDIO_SERVICE", "onecat-studio.service")
        if not re.fullmatch(r"onecat-studio(?:-[a-z0-9-]+)?\.service", manager):
            raise ValueError("Invalid Studio service name")
        try:
            mode = subprocess.run(
                ["systemctl", "--user", "show", manager, "-p", "KillMode", "--value"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError("The Studio user service is unavailable") from error
        if mode != "process":
            raise ValueError(
                "Studio service needs KillMode=process before OTA can preserve inference"
            )
        unit_name = manager.removesuffix(".service") + "-update.service"
        command = [
            sys.executable,
            "-m",
            "onecat_ota.ota",
            "--manifest",
            str(work / "release.json"),
            "--prefix",
            str(prefix()),
            "--state",
            str(state_root()),
            "--port",
            os.environ.get("ONECAT_STUDIO_LISTEN_PORT", str(port)),
            "--host",
            os.environ.get("ONECAT_STUDIO_LISTEN_HOST", db.settings()["host"]),
            "--service",
            manager,
            "--operation",
            operation,
        ]
        unit = Path.home() / ".config/systemd/user" / unit_name
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(
            "[Unit]\nDescription=1Cat Studio OTA\nAfter=network-online.target\n\n[Service]\n"
            "Type=simple\nExecStart=" + " ".join(systemd_quote(x) for x in command) + "\n"
            "Environment=" + systemd_quote("PYTHONPATH=" + str(work)) + "\n"
            "Environment=PYTHONUNBUFFERED=1\nRestart=on-failure\nRestartSec=5\n"
            "UMask=0077\nTimeoutStopSec=60\n\n[Install]\nWantedBy=default.target\n"
        )
        write_status(
            stage="queued",
            version=manifest["version"],
            operation=operation,
            ok=None,
            release_id=protocol.release_id(manifest),
            service=manager,
        )
        try:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"], check=True, capture_output=True
            )
            subprocess.run(
                ["systemctl", "--user", "enable", unit_name], check=True, capture_output=True
            )
            subprocess.run(
                ["systemctl", "--user", "restart", unit_name], check=True, capture_output=True
            )
        except (OSError, subprocess.SubprocessError) as error:
            write_status(
                stage="failed",
                operation=operation,
                ok=False,
                detail="Could not start the OTA worker",
            )
            raise ValueError("Could not start the Studio update service") from error
        return {"version": manifest["version"], "operation": operation, "unit": unit_name}
