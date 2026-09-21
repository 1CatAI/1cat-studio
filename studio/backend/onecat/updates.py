# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Remote updates: check a channel manifest, then apply a published Studio bundle.

Applying an update restarts the service that is running it, so the work happens in a
detached systemd unit. This module only resolves the channel, downloads and verifies
the bundle, and reports what the applier recorded. Installation itself stays with the
installer that created this deployment, which keeps the pre-upgrade database snapshot,
the ``previous`` symlink and the rollback script authoritative.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from . import db
from .config import state_root

FORMAT = "onecat-studio-update-v1"
DEFAULT_CHANNEL = "http://156.226.174.161:8089/onecat/studio/latest.json"
CACHE_SECONDS = 900
HEALTH_TIMEOUT = 600
_cache: dict = {"at": 0.0, "url": "", "manifest": None}


def channel_url() -> str:
    """The configured channel, so a customer can point at staging or at a mirror."""
    return str(
        db.settings().get("update_channel")
        or os.environ.get("ONECAT_UPDATE_CHANNEL")
        or DEFAULT_CHANNEL
    )


def prefix() -> Path:
    """The app prefix that holds ``releases``, ``current`` and ``previous``."""
    override = os.environ.get("ONECAT_STUDIO_PREFIX")
    if override:
        return Path(override).expanduser().resolve()
    for entry in (os.environ.get("PYTHONPATH") or "").split(os.pathsep):
        parts = Path(entry).parts if entry else ()
        if len(parts) >= 4 and parts[-3:] == ("studio", "backend") or (
            len(parts) >= 4 and parts[-3] == "studio" and parts[-2] == "backend"
        ):
            candidate = Path(*parts[:-3])
            if (candidate / "current").exists() or (candidate / "releases").exists():
                return candidate.resolve()
    return (Path.home() / ".local/share/onecat-studio-app").resolve()


def updates_root() -> Path:
    root = state_root() / "updates"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def status_path() -> Path:
    return updates_root() / "status.json"


def installed_version() -> str | None:
    manifest = prefix() / "current" / "manifest.json"
    try:
        return str(json.loads(manifest.read_text()).get("version"))
    except (OSError, ValueError):
        return None


def write_status(**fields) -> dict:
    record = {"updated_at": time.time(), **fields}
    status_path().write_text(json.dumps(record, ensure_ascii=False, indent=1))
    return record


def read_status() -> dict:
    try:
        return json.loads(status_path().read_text())
    except (OSError, ValueError):
        return {}


def fetch(force: bool = False) -> dict:
    """Read the channel manifest, cached so the UI stays responsive and offline-safe."""
    url = channel_url()
    fresh = (
        not force
        and _cache["manifest"] is not None
        and _cache["url"] == url
        and time.time() - _cache["at"] < CACHE_SECONDS
    )
    if fresh:
        return _cache["manifest"]
    from urllib.request import Request, urlopen

    request = Request(url, headers={"User-Agent": "onecat-studio"})
    with urlopen(request, timeout=30) as response:
        manifest = json.loads(response.read().decode())
    if manifest.get("format") != FORMAT:
        raise ValueError("Not a 1Cat Studio update channel")
    if not manifest.get("version") or not manifest.get("url"):
        raise ValueError("Update channel has no version or bundle URL")
    if manifest.get("sha256") and len(str(manifest["sha256"])) != 64:
        raise ValueError("Update channel sha256 is malformed")
    _cache.update({"at": time.time(), "url": url, "manifest": manifest})
    return manifest


def check(force: bool = False) -> dict:
    """Never raise for the UI: report the reason instead of failing the request."""
    current = installed_version()
    result = {
        "channel": channel_url(),
        "current": current,
        "latest": None,
        "available": False,
        "notes": "",
        "size": None,
        "published": None,
        "status": read_status(),
        "error": None,
    }
    try:
        manifest = fetch(force=force)
    except Exception as error:  # noqa: BLE001 - the channel is operator input
        result["error"] = "%s: %s" % (type(error).__name__, str(error)[:300])
        return result
    result.update(
        {
            "latest": manifest.get("version"),
            "notes": manifest.get("notes") or manifest.get("changelog") or "",
            "size": manifest.get("size"),
            "published": manifest.get("release_date"),
            "available": bool(manifest.get("version") and manifest["version"] != current),
        }
    )
    minimum = manifest.get("min_version")
    if result["available"] and minimum and current and current < str(minimum):
        result["error"] = "This release needs at least %s; update in steps" % minimum
        result["available"] = False
    return result


def applier_script() -> Path:
    """The detached applier is installed next to the status file, not in the release."""
    script = updates_root() / "apply-update.py"
    source = Path(__file__).resolve().parents[3] / "scripts" / "apply-update.py"
    if source.is_file():
        script.write_bytes(source.read_bytes())
    if not script.is_file():
        raise ValueError("The updater script is missing from this installation")
    return script


def start(manifest: dict, *, port: int) -> dict:
    """Hand the installation to a unit that outlives the restart it performs.

    The unit downloads the bundle itself: a download owned by the Studio would die
    when the installer stops the Studio halfway through.
    """
    version = str(manifest["version"])
    script = applier_script()
    unit = "onecat-studio-update"
    subprocess.run(["systemctl", "--user", "reset-failed", unit + ".service"], capture_output=True)
    environment = [
        "--setenv=ONECAT_STUDIO_HOME=" + str(state_root()),
        "--setenv=PATH=" + os.environ.get("PATH", "/usr/bin:/bin"),
    ]
    if os.environ.get("ONECAT_STUDIO_PREFIX"):
        environment.append("--setenv=ONECAT_STUDIO_PREFIX=" + os.environ["ONECAT_STUDIO_PREFIX"])
    argv = [
        "systemd-run",
        "--user",
        "--collect",
        "--unit",
        unit,
        "--description",
        "1Cat Studio update to " + version,
        "--property",
        "Type=oneshot",
        *environment,
        sys.executable,
        str(script),
        "--url",
        str(manifest["url"]),
        "--sha256",
        str(manifest.get("sha256") or ""),
        "--version",
        version,
        "--prefix",
        str(prefix()),
        "--state",
        str(state_root()),
        "--health-url",
        "http://127.0.0.1:%d/api/health" % port,
    ]
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout).strip()[-500:])
    write_status(stage="queued", version=version, ok=None, detail="update unit started")
    return {"version": version, "unit": unit + ".service"}


def _job():
    from .jobs import Job, create_job

    record = create_job("install_update", {})
    return Job(record["id"])


def bundle_url(manifest: dict) -> str:
    return urljoin(manifest.get("url", ""), urlsplit(manifest.get("url", "")).path.split("/")[-1])
