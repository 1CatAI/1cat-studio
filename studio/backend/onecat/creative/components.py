# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
import threading
import time
from pathlib import Path

from modelscope_hub import ProgressCallback

from .. import db, jobs
from ..config import state_root
from ..jobs import Job
from ..models import hub, retained_bytes


def catalog() -> list[dict]:
    return json.loads(Path(__file__).with_name("components.json").read_text())["items"]


def schedule_download(identity: str) -> tuple[dict, bool]:
    """Share an in-flight transfer/verification across setup and generation."""
    if identity not in {c["id"] for c in catalog()}:
        raise ValueError("Unknown ModelScope creative component")
    with (state_root() / "creative-download.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for record in jobs.list_jobs():
            if (
                record["kind"] == "creative_download"
                and record.get("component_id") == identity
                and record["state"] not in jobs.TERMINAL
                and not record.get("cancel_requested")
            ):
                return record, False
        record = jobs.create_job("creative_download", {"component_id": identity})
        return {k: v for k, v in record.items() if k != "payload"}, True


def destination(component):
    identity = hashlib.sha256(json.dumps(component["files"], sort_keys=True).encode()).hexdigest()
    installed = db.get("creative_components", component["id"])
    if installed and installed.get("manifest_sha256") == identity:
        path = Path(installed["path"]).expanduser().resolve()
        if component["role"] == "base":
            return path, identity
        relative = Path(component["files"][0]["path"])
        if path.parts[-len(relative.parts) :] == relative.parts:
            return path.parents[len(relative.parts) - 1], identity
    return (
        Path(db.settings()["model_directory"]).expanduser().resolve()
        / "creative"
        / (component["id"] + "-" + identity[:12]),
        identity,
    )


def listed() -> list[dict]:
    result = []
    for component in catalog():
        installed = db.get("creative_components", component["id"])
        available = bool(installed)
        if installed:
            root = Path(installed["path"])
            for item in component["files"]:
                path = root / item["path"] if component["role"] == "base" else root
                try:
                    available = path.is_file() and path.stat().st_size == item["bytes"]
                except OSError:
                    available = False
                if not available:
                    break
        result.append(
            {
                **{k: v for k, v in component.items() if k != "files"},
                "installed": installed if available else None,
                "needs_repair": bool(installed and not available),
                "download_bytes": 0
                if available
                else max(
                    0,
                    component["bytes"]
                    - retained_bytes(destination(component)[0], component["files"]),
                ),
            }
        )
    return result


def download(job: Job) -> dict:
    item = next((c for c in catalog() if c["id"] == job.payload["component_id"]), None)
    if not item:
        raise ValueError("Unknown ModelScope creative component")
    manifest = item["files"]
    api = hub()
    names = [f["path"] for f in manifest]
    folder, identity = destination(item)
    folder.mkdir(parents=True, exist_ok=True)

    def verify_listing():
        fresh = {
            f.path: {"path": f.path, "bytes": f.size, "sha256": f.sha256}
            for f in api.list_repo_files(
                item["repo_id"], "model", revision=item["revision"], recursive=True
            )
            if not f.is_dir
        }
        if any(fresh.get(f["path"]) != f for f in manifest):
            raise ValueError(
                "ModelScope component changed since catalog verification; update the catalog before downloading"
            )

    verify_listing()
    expected = item["bytes"]
    present = retained_bytes(folder, manifest)
    if shutil.disk_usage(folder).free < max(0, expected - present) + 1024**3:
        raise ValueError("Not enough free space for this component and temporary files")
    latest = [time.monotonic(), present]
    lock = threading.Lock()
    job.update(
        "downloading",
        0,
        expected_bytes=expected,
        downloaded_bytes=present,
        component_id=item["id"],
        provider="modelscope",
    )

    class Progress(ProgressCallback):
        def update(self, size):
            job.check_cancelled()
            with lock:
                now = time.monotonic()
                if now - latest[0] >= 0.5:
                    done = retained_bytes(folder, manifest)
                    job.update(
                        "downloading",
                        min(99, done / expected * 100),
                        downloaded_bytes=done,
                        bytes_per_second=max(0, done - latest[1]) / (now - latest[0]),
                    )
                    latest[:] = [now, done]

        def end(self):
            job.check_cancelled()

    api.download_repo(
        item["repo_id"],
        "model",
        revision=item["revision"],
        local_dir=folder,
        allow_patterns=names,
        max_workers=2,
        progress_callbacks=[Progress],
    )
    job.update(
        "checking_model", bytes_per_second=0, phase_progress={"done": 0, "total": len(manifest)}
    )
    for index, entry in enumerate(manifest):
        job.check_cancelled()
        path = (folder / entry["path"]).resolve()
        if (
            not path.is_relative_to(folder)
            or not path.is_file()
            or path.stat().st_size != entry["bytes"]
        ):
            raise ValueError("Missing or invalid component file")
        with path.open("rb") as stream:
            digest = hashlib.sha256()
            while chunk := stream.read(8 * 1024**2):
                job.check_cancelled()
                digest.update(chunk)
        if digest.hexdigest() != entry["sha256"]:
            raise ValueError("Component SHA256 verification failed")
        job.update("checking_model", phase_progress={"done": index + 1, "total": len(manifest)})
    verify_listing()
    value = {
        "id": item["id"],
        "path": str(folder if item["role"] == "base" else folder / names[0]),
        "manifest_sha256": identity,
        "source": "modelscope",
        "bytes": expected,
    }
    db.put("creative_components", item["id"], value)
    job.update(
        "checking_model", downloaded_bytes=expected, expected_bytes=expected, bytes_per_second=0
    )
    return value
