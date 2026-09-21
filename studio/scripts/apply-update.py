#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Apply a downloaded Studio bundle, verify it, and roll back if it does not come up.

The installer stops and restarts the Studio service, so this script must run from a
detached unit that outlives that restart. It writes <state>/updates/status.json so the
Studio that comes back can report what happened, including an automatic rollback.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

HEALTH_INTERVAL = 5
HEALTH_TIMEOUT = 900


def stamp() -> str:
    return time.strftime("%H:%M:%S")


def log(message: str) -> None:
    print("%s %s" % (stamp(), message), flush=True)


def write_status(state: Path, **fields) -> None:
    root = state / "updates"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = {"updated_at": time.time(), **fields}
    (root / "status.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))


def healthy(url: str, *, timeout: float = HEALTH_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                if response.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - the new service is still starting
            pass
        time.sleep(HEALTH_INTERVAL)
    return False


def extract(source: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with tarfile.open(source) as archive:
        for member in archive.getmembers():
            path = (destination / member.name).resolve()
            if not path.is_relative_to(destination.resolve()):
                raise ValueError("Bundle contains a path outside the release")
            if member.isdev() or member.isfifo():
                raise ValueError("Bundle contains a special device file")
        archive.extractall(destination, filter="data")
    entries = [entry for entry in destination.iterdir() if entry.is_dir()]
    if len(entries) == 1 and (entries[0] / "scripts/install.py").is_file():
        return entries[0]
    if (destination / "scripts/install.py").is_file():
        return destination
    raise ValueError("Bundle does not contain scripts/install.py")


def download(url: str, target: Path, sha256: str) -> None:
    """Resumable download; the partial file keeps a dropped link from starting over."""
    import hashlib

    partial = target.with_suffix(target.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(url, headers={"User-Agent": "onecat-studio"})
    if offset:
        request.add_header("Range", "bytes=%d-" % offset)
    log("downloading %s from %d bytes" % (target.name, offset))
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("ab") as stream:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            stream.write(chunk)
    digest = hashlib.sha256()
    with partial.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 22), b""):
            digest.update(block)
    if sha256 and digest.hexdigest() != sha256:
        partial.unlink(missing_ok=True)
        raise ValueError("bundle sha256 mismatch")
    partial.replace(target)


def stop_engine() -> None:
    """The installer refuses to upgrade while a model is loaded, so stop it here."""
    units = subprocess.run(
        ["systemctl", "--user", "list-units", "--plain", "--no-legend", "onecat-studio-engine-*.service"],
        capture_output=True,
        text=True,
    ).stdout.split()
    for unit in [name for name in units if name.endswith(".service")]:
        log("stopping " + unit)
        subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True)


def installer(python: str, bundle: Path, prefix: Path, state: Path, logfile: Path) -> int:
    argv = [python, str(bundle / "scripts/install.py"), str(bundle), str(prefix), str(state), "1"]
    log("running installer for " + bundle.name)
    with logfile.open("w") as stream:
        result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, check=False)
    return result.returncode


def rollback(python: str, prefix: Path, logfile: Path) -> int:
    script = prefix / "current" / "scripts" / "rollback.py"
    if not script.is_file():
        log("no rollback script in the installed release")
        return 1
    log("rolling back to the previous release")
    with logfile.open("a") as stream:
        result = subprocess.run(
            [python, str(script), str(prefix)], stdout=stream, stderr=subprocess.STDOUT, check=False
        )
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", default="")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--health-url", required=True)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()

    bundle = Path(args.state) / "updates" / (args.version + ".tar.gz")
    prefix = Path(args.prefix)
    state = Path(args.state)
    work = state / "updates" / args.version
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    logfile = work / "install.log"
    write_status(state, stage="downloading", version=args.version, ok=None, detail="")

    try:
        download(args.url, bundle, args.sha256)
    except Exception as error:  # noqa: BLE001
        write_status(state, stage="failed", version=args.version, ok=False, detail=str(error)[:500])
        log("download failed: " + str(error)[:300])
        return 1

    stop_engine()
    write_status(state, stage="extracting", version=args.version, ok=None, detail="")

    try:
        release = extract(bundle, work / "bundle")
    except Exception as error:  # noqa: BLE001 - report instead of dying silently
        write_status(state, stage="failed", version=args.version, ok=False, detail=str(error)[:500])
        log("extract failed: " + str(error)[:300])
        return 1

    code = installer(args.python, release, prefix, state, logfile)
    if code:
        detail = "installer exited %d; see %s" % (code, logfile)
        write_status(state, stage="failed", version=args.version, ok=False, detail=detail)
        log(detail)
        return code

    write_status(state, stage="restarting", version=args.version, ok=None, detail="")
    if healthy(args.health_url):
        write_status(
            state, stage="installed", version=args.version, ok=True, detail="service healthy"
        )
        log("update to %s is healthy" % args.version)
        return 0

    log("service did not come up; rolling back")
    code = rollback(args.python, prefix, logfile)
    back = healthy(args.health_url)
    write_status(
        state,
        stage="rolled_back" if back else "broken",
        version=args.version,
        ok=False,
        detail="rollback exit %d; service %s" % (code, "healthy" if back else "unhealthy"),
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
