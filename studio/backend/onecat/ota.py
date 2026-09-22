# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Durable OTA worker, also copied into an independent package before activation.

Prepare a complete release before pausing the manager. Keep inference processes
and model/runtime directories intact. Recover the previous manager and database
snapshot if activation fails or this process is interrupted.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from . import update_protocol as protocol

TERMINAL = {"completed", "failed", "cancelled"}


def busy_reason(state: Path) -> str | None:
    path = state / "onecat.db"
    if not path.exists():
        return None
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if (
            "requests" in tables
            and conn.execute("SELECT count(*) FROM requests WHERE status IS NULL").fetchone()[0]
        ):
            return "请等待当前对话完成 / Wait for active requests to finish"
        if "records" not in tables:
            return None
        for bucket, raw in conn.execute(
            "SELECT bucket,data FROM records WHERE bucket IN ('jobs','agent_tasks','creative_runs','chat_runs')"
        ):
            item = json.loads(raw)
            if bucket == "agent_tasks":
                active = item.get("state") in {"starting", "running", "waiting", "stopping"}
                active = active or item.get("settled") is False
            else:
                active = item.get("state") not in TERMINAL
            if active:
                return "请先完成或停止后台任务 / Finish or stop active tasks before updating"
    return None


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(manifest: dict, target: Path, progress) -> None:
    """Resume an immutable signed asset; HTTP 200 restarts instead of appending."""
    total = manifest["size"]
    if target.exists() and target.stat().st_size == total and digest(target) == manifest["sha256"]:
        progress(downloaded_bytes=total, total_bytes=total, bytes_per_second=0)
        return
    partial = target.with_suffix(target.suffix + ".part")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if partial.exists() and partial.stat().st_size >= total:
        if partial.stat().st_size == total and digest(partial) == manifest["sha256"]:
            partial.replace(target)
            progress(downloaded_bytes=total, total_bytes=total, bytes_per_second=0)
            return
        partial.unlink()
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "onecat-studio-ota/1", "Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    try:
        response = urlopen(Request(manifest["url"], headers=headers), timeout=30)
    except HTTPError as error:
        if error.code != 416 or not offset:
            raise
        partial.unlink()
        return download(manifest, target, progress)
    with response:
        if response.status == 206:
            expected = f"bytes {offset}-{total - 1}/{total}"
            if response.headers.get("Content-Range") != expected:
                raise ValueError("Update server returned an invalid Content-Range")
        elif response.status == 200:
            offset = 0
        else:
            raise ValueError("Unexpected update download response")
        started, last, count = time.monotonic(), 0.0, offset
        with partial.open("ab" if offset else "wb") as stream:
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > total:
                    raise ValueError("Update bundle exceeds its signed size")
                stream.write(chunk)
                now = time.monotonic()
                if now - last >= 0.5:
                    progress(
                        downloaded_bytes=count,
                        total_bytes=total,
                        bytes_per_second=(count - offset) / max(now - started, 0.001),
                    )
                    last = now
            stream.flush()
            os.fsync(stream.fileno())
    if count != total:
        raise ValueError("Update download was interrupted; retry to resume")
    if digest(partial) != manifest["sha256"]:
        partial.unlink()
        raise ValueError("Update bundle SHA256 mismatch")
    partial.replace(target)
    progress(downloaded_bytes=total, total_bytes=total, bytes_per_second=0)


def require_space(path: Path, needed: int) -> None:
    existing = path
    while not existing.exists():
        existing = existing.parent
    if shutil.disk_usage(existing).free < needed + 128 * 1024 * 1024:
        raise ValueError("Insufficient disk space for the Studio update")


def prepare_release(archive: Path, prefix: Path, manifest: dict, state: Path, service: str) -> Path:
    release = prefix / "releases" / manifest["version"]
    marker = release / ".ota-sha256"
    if release.exists():
        if marker.is_file() and marker.read_text().strip() == manifest["sha256"]:
            return release
        owner = release / ".ota-preparing"
        if owner.is_file() and owner.read_text().strip() == manifest["sha256"]:
            shutil.rmtree(release)  # Our interrupted preparation, before activation.
        else:
            raise ValueError("An incomplete or different copy of this release already exists")
    require_space(prefix, manifest["unpacked_size"])
    release.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ota-extract-", dir=release.parent) as temporary:
        extracted = Path(temporary)
        with tarfile.open(archive) as bundle:
            members = bundle.getmembers()
            if sum(m.size for m in members) != manifest["unpacked_size"]:
                raise ValueError("Update bundle unpacked size differs from its signed manifest")
            if any(m.isdev() or m.isfifo() for m in members):
                raise ValueError("Update bundle contains a special file")
            bundle.extractall(extracted, filter="data")
        entries = list(extracted.iterdir())
        if len(entries) != 1 or not entries[0].is_dir():
            raise ValueError("Update bundle must contain one release directory")
        candidate = entries[0]
        packaged = json.loads((candidate / "manifest.json").read_text())
        if packaged.get("format") != "onecat-studio-linux-v1" or any(
            packaged.get(k) != manifest[k] for k in ("version", "source_commit", "sequence")
        ):
            raise ValueError("Bundle identity does not match the selected signed release")
        (candidate / ".ota-preparing").write_text(manifest["sha256"] + "\n")
        candidate.rename(release)
    try:
        old = packaged["original_prefix"]
        python_home = (release / packaged["python_home"]).resolve()
        if not python_home.is_relative_to(release.resolve()):
            raise ValueError("Bundled Python points outside the release")
        cfg = release / "env/pyvenv.cfg"
        cfg.write_text(cfg.read_text().replace(old, str(release)))
        python = release / "env/bin/python"
        python.unlink(missing_ok=True)
        python.symlink_to(os.path.relpath(python_home / "bin/python3.12", python.parent))
        for script in (release / "env/bin").iterdir():
            if script.is_file() and not script.is_symlink() and script.stat().st_size < 1024 * 1024:
                content = script.read_bytes()
                if content.startswith(b"#!"):
                    script.write_bytes(content.replace(old.encode(), str(release).encode()))
        launcher = release / "run"
        variables = {
            "ONECAT_STUDIO_HOME": str(state),
            "ONECAT_STUDIO_PREFIX": str(prefix),
            "ONECAT_STUDIO_SERVICE": service,
            "PYTHONPATH": str(release / "studio/backend"),
            "ONECAT_FRONTEND_DIST": str(release / "studio/frontend/dist"),
        }
        launcher.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            + "".join(f"export {key}={shlex.quote(value)}\n" for key, value in variables.items())
            + f'export PATH={shlex.quote(str(release))}:"$PATH"\n'
            + f'exec {shlex.quote(str(python))} -m onecat "$@"\n'
        )
        launcher.chmod(0o755)
        with tempfile.TemporaryDirectory(prefix=".ota-probe-", dir=prefix) as probe:
            env = {
                **os.environ,
                **variables,
                "ONECAT_STUDIO_HOME": probe,
                "ONECAT_AUTO_GPU_ACTIONS": "0",
            }
            subprocess.run(
                [str(python), "-c", "from onecat.app import create_app; create_app()"],
                env=env,
                check=True,
                timeout=90,
                capture_output=True,
            )
        marker.write_text(manifest["sha256"] + "\n")
    except BaseException:
        shutil.rmtree(release)
        raise
    return release


def link(path: Path, target: str | Path | None) -> None:
    if target is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(path.name + ".ota-new")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    temporary.replace(path)


def backup_databases(state: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("onecat.db", "studio.db"):
        if (state / name).exists():
            with sqlite3.connect(state / name) as src, sqlite3.connect(destination / name) as dst:
                src.backup(dst)
            (destination / name).chmod(0o600)


def restore_databases(backup: Path, state: Path) -> None:
    for name in ("onecat.db", "studio.db"):
        if (backup / name).exists():
            with sqlite3.connect(backup / name) as src, sqlite3.connect(state / name) as dst:
                src.backup(dst)


class Services:
    def run(self, *args):
        return subprocess.run(
            ["systemctl", "--user", *args], check=True, capture_output=True, text=True, timeout=60
        )

    def health(self, port: int, version: str | None, timeout: float = 90) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
                    data = json.load(response)
                if data.get("status") == "ok" and (not version or data.get("release") == version):
                    return True
            except (OSError, ValueError):
                pass
            time.sleep(1)
        return False


class Operation:
    def __init__(self, args, services=None):
        self.args = args
        self.state, self.prefix = args.state.resolve(), args.prefix.resolve()
        self.work = args.manifest.resolve().parent
        self.root = self.state / "updates"
        self.gate = self.root / "activation.json"
        self.journal_path = self.work / "transaction.json"
        self.services = services or Services()
        self.manifest = protocol.verify(protocol.parse_manifest(args.manifest.read_bytes()))
        self.status = {
            "operation": args.operation,
            "version": self.manifest["version"],
            "release_id": protocol.release_id(self.manifest),
            "ok": None,
        }
        self.dropin = (
            Path.home() / ".config/systemd/user" / (args.service + ".d") / "99-onecat-ota.conf"
        )

    def progress(self, **fields):
        self.status.update(fields, updated_at=time.time())
        protocol.atomic_json(self.root / "status.json", self.status)
        print(
            json.dumps(
                {
                    k: v
                    for k, v in self.status.items()
                    if k in {"stage", "version", "detail", "downloaded_bytes"}
                }
            ),
            flush=True,
        )

    def save(self, journal, **fields):
        journal.update(fields)
        protocol.atomic_json(self.journal_path, journal)

    def snapshot(self):
        return {
            "stage": "prepared",
            "backup_ready": False,
            "dropin": self.dropin.read_text() if self.dropin.exists() else None,
            "links": {
                name: os.readlink(self.prefix / name) if (self.prefix / name).is_symlink() else None
                for name in ("current", "previous")
            },
            "installation": (self.prefix / "installation.json").read_text()
            if (self.prefix / "installation.json").exists()
            else None,
        }

    def recover(self, journal):
        self.progress(stage="rolling_back", detail="Restoring the previous Studio release")
        self.services.run("stop", self.args.service)
        if journal.get("backup_ready"):
            backup_databases(self.state, self.work / "before-rollback")
            restore_databases(self.work / "backup", self.state)
        if journal["dropin"] is None:
            self.dropin.unlink(missing_ok=True)
        else:
            self.dropin.parent.mkdir(parents=True, exist_ok=True)
            self.dropin.write_text(journal["dropin"])
        for name, target in journal["links"].items():
            link(self.prefix / name, target)
        installation = self.prefix / "installation.json"
        if journal["installation"] is None:
            installation.unlink(missing_ok=True)
        else:
            installation.write_text(journal["installation"])
        self.services.run("daemon-reload")
        self.services.run("start", self.args.service)
        if not self.services.health(self.args.port, None):
            raise RuntimeError("Previous Studio service did not recover; retrying recovery")
        self.save(journal, stage="rolled_back")
        self.gate.unlink(missing_ok=True)
        self.progress(
            stage="rolled_back",
            ok=False,
            detail="Update failed; the previous Studio version was restored",
        )

    def activate(self, release):
        # Block new work before the final busy check. Existing requests/jobs are
        # never terminated to make an OTA update fit.
        with (self.root / "admission.lock").open("a") as admission:
            try:
                fcntl.flock(admission, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("A request is still active; retry the update shortly") from error
            protocol.atomic_json(self.gate, {"operation": self.args.operation})
            if reason := busy_reason(self.state):
                self.gate.unlink(missing_ok=True)
                raise ValueError(reason)
        journal = self.snapshot()
        self.save(journal)
        self.progress(stage="installing", detail="Activating the prepared release")
        self.services.run("stop", self.args.service)
        backup_databases(self.state, self.work / "backup")
        self.save(journal, backup_ready=True)
        self.dropin.parent.mkdir(parents=True, exist_ok=True)
        command = str(self.prefix / "current/run")
        quoted = '"' + command.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
        self.dropin.write_text(
            "[Service]\nExecStart=\nExecStart="
            + quoted
            + ' --host "'
            + self.args.host.replace("%", "%%")
            + '" --port '
            + str(self.args.port)
            + "\nWorkingDirectory="
            + str(release).replace("%", "%%")
            + "\nKillMode=process\n"
        )
        if journal["links"]["current"]:
            link(self.prefix / "previous", journal["links"]["current"])
        link(self.prefix / "current", release)
        protocol.atomic_json(
            self.prefix / "installation.json",
            {
                "state": str(self.state),
                "backup": str(self.work / "backup"),
                "service": True,
                "ota_operation": self.args.operation,
            },
        )
        self.save(journal, stage="activated")
        self.progress(stage="restarting", detail="Waiting for the updated Studio")
        self.services.run("daemon-reload")
        self.services.run("start", self.args.service)
        if not self.services.health(self.args.port, self.manifest["version"]):
            raise RuntimeError("The updated Studio failed its version/health check")
        self.save(journal, stage="installed")
        self.gate.unlink(missing_ok=True)
        self.progress(stage="installed", ok=True, detail="Studio update completed")

    def run(self) -> int:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.prefix.mkdir(parents=True, exist_ok=True)
        with contextlib.ExitStack() as stack:
            for path in (self.root / "worker.lock", self.prefix / "upgrade.lock"):
                lock = stack.enter_context(path.open("a"))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.journal_path.exists():
                journal = json.loads(self.journal_path.read_text())
                if journal["stage"] in {"installed", "rolled_back"}:
                    self.gate.unlink(missing_ok=True)
                    self.progress(stage=journal["stage"], ok=journal["stage"] == "installed")
                    return 0
                self.recover(journal)
                return 0
            try:
                if reason := busy_reason(self.state):
                    raise ValueError(reason)
                self.progress(stage="downloading", detail="Downloading the Studio release")
                archive = self.root / "downloads" / (self.manifest["sha256"] + ".tar.gz")
                combined = self.manifest["size"]
                if self.root.stat().st_dev == self.prefix.stat().st_dev:
                    combined += self.manifest["unpacked_size"]
                require_space(self.root, combined)
                require_space(self.prefix, self.manifest["unpacked_size"])
                download(self.manifest, archive, self.progress)
                self.progress(stage="preparing", detail="Verifying and preparing the new release")
                release = prepare_release(
                    archive, self.prefix, self.manifest, self.state, self.args.service
                )
                self.activate(release)
            except Exception as error:
                self.progress(detail=str(error)[:400])
                if self.journal_path.exists():
                    self.recover(json.loads(self.journal_path.read_text()))
                else:
                    self.gate.unlink(missing_ok=True)
                    self.progress(stage="failed", ok=False, detail=str(error)[:400])
            return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--service", default="onecat-studio.service")
    parser.add_argument("--operation", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"onecat-studio(?:-[a-z0-9-]+)?\.service", args.service):
        parser.error("Invalid Studio service")
    result = Operation(args).run()
    # Leave the unit enabled: terminal journal replay is a no-op. Disabling here
    # could race the next request enabling this same unit with a new operation.
    return result


if __name__ == "__main__":
    sys.exit(main())
