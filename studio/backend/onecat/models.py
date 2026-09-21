# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import shutil
import threading
import time
from pathlib import Path

from modelscope_hub import HubApi, HubConfig, ProgressCallback

from . import db
from .config import state_root
from .jobs import Job


def hub() -> HubApi:
    return HubApi(
        HubConfig(
            endpoint=db.settings()["modelscope_endpoint"],
            token=db.get("secrets", "modelscope", {}).get("token"),
            cache_dir=state_root() / "cache" / "modelscope",
            config_dir=state_root() / "modelscope-config",
        )
    )


def search(query: str, runtime_id=None, all_verified=False) -> list[dict]:
    from .catalog import search as verified_search

    return verified_search(query, runtime_id, all_verified)


def inspect_model(path: str, source: str = "local", repo_id: str | None = None) -> dict:
    folder = Path(path).expanduser().resolve()
    config_file = folder / "config.json"
    if not config_file.is_file():
        raise ValueError("Select a model directory containing config.json")
    if config_file.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Model configuration is unexpectedly large")
    config = json.loads(config_file.read_text())
    weights = list(folder.glob("*.safetensors")) or list(folder.glob("*.bin"))
    if not weights:
        raise ValueError("No safetensors or PyTorch model weights found")
    existing = next((m for m in db.all_records("models") if m["path"] == str(folder)), None)
    quant = config.get("quantization_config") or {}
    info = {
        "verified": bool(existing and existing.get("verified")),
        "catalog_id": existing.get("catalog_id") if existing else None,
        "default_profile_id": existing.get("default_profile_id") if existing else None,
        "id": existing["id"] if existing else db.uid(),
        "name": folder.name,
        "path": str(folder),
        "source": source,
        "repo_id": repo_id,
        "model_type": config.get("model_type"),
        "architectures": config.get("architectures", []),
        "role": "draft" if "DFlash2DraftModel" in config.get("architectures", []) else "target",
        "quantization": quant.get("quant_method", config.get("torch_dtype", "unknown")),
        "max_context": config.get("max_position_embeddings")
        or config.get("text_config", {}).get("max_position_embeddings"),
        "bytes": sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()),
        "state": "downloaded",
        "added_at": time.time(),
    }
    db.put("models", info["id"], info)
    return info


def retained_bytes(folder: Path, manifest: list[dict]) -> int:
    """Count unique file bytes, including resumable parts, not callback replays."""
    total = 0
    for item in manifest:
        path = folder / item["path"]
        spans = []
        candidates = [(path, 0, item["bytes"])] + [
            (path.with_suffix(path.suffix + suffix), 0, item["bytes"])
            for suffix in (".incomplete", ".parallel_tmp")
        ]
        for part in path.parent.glob(path.name + "_*"):
            match = re.fullmatch(re.escape(path.name) + r"_(\d+)_(\d+)", part.name)
            if match:
                start, end = map(int, match.groups())
                candidates.append((part, start, min(end + 1, item["bytes"])))
        for candidate, start, end in candidates:
            try:
                length = candidate.stat().st_size
            except FileNotFoundError:
                continue
            if length and 0 <= start < end:
                spans.append((start, min(start + length, end)))
        covered = 0
        for start, end in sorted(spans):
            total += max(0, end - max(start, covered))
            covered = max(covered, end)
    return total


def download(job: Job):
    repo_id = job.payload["repo_id"]
    revision = job.payload.get("revision") or "master"
    from .catalog import require_download

    approved = require_download(repo_id, revision, job.payload.get("runtime_id"))
    api = hub()
    job.update("resolving_model", 0)
    files = api.list_repo_files(repo_id, "model", revision=revision)
    ignored = ["*.gguf", "*.onnx", "*.h5", "*.msgpack", "*.ot"]
    files = [
        f for f in files if not f.is_dir and not any(fnmatch.fnmatch(f.path, p) for p in ignored)
    ]
    if not files or any(not f.sha256 or len(f.sha256) != 64 for f in files):
        raise ValueError("ModelScope did not return a complete SHA256 file manifest")
    manifest = [
        {"path": f.path, "bytes": f.size, "sha256": f.sha256}
        for f in sorted(files, key=lambda f: f.path)
    ]
    pinned = sorted(approved["files"], key=lambda f: f["path"])
    if manifest != pinned:
        raise ValueError("ModelScope files changed since verification; catalog update required")
    if not any(f["path"].endswith((".safetensors", ".bin")) for f in pinned):
        raise ValueError("Verified checkpoint has no runnable weights")
    identity = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    model_root = Path(db.settings()["model_directory"]).expanduser().resolve()
    model_root.mkdir(parents=True, exist_ok=True)
    folder = model_root / (repo_id.replace("/", "--") + "--" + identity[:12])
    expected = sum(f.size for f in files)
    present = retained_bytes(folder, manifest)
    if shutil.disk_usage(model_root).free < max(0, expected - present):
        raise ValueError("Not enough free disk space for this model")
    job.update(
        "downloading",
        0,
        expected_bytes=expected,
        downloaded_bytes=min(present, expected),
        destination=str(folder),
        revision=revision,
        manifest_sha256=identity,
        provider="modelscope",
    )
    latest = [time.monotonic(), present]
    lock = threading.Lock()

    class Progress(ProgressCallback):
        def update(self, size):
            job.check_cancelled()
            with lock:
                now = time.monotonic()
                if now - latest[0] >= 0.5:
                    # The SDK replays existing offsets on resume and retries.
                    # Disk extents distinguish retained bytes from new transfer.
                    done = retained_bytes(folder, manifest)
                    rate = max(0, done - latest[1]) / max(0.01, now - latest[0])
                    job.update(
                        "downloading",
                        min(97, done / max(1, expected) * 100),
                        downloaded_bytes=done,
                        expected_bytes=expected,
                        bytes_per_second=rate,
                    )
                    latest[0] = now
                    latest[1] = done

        def end(self):
            job.check_cancelled()

    api.download_repo(
        repo_id,
        "model",
        revision=revision,
        local_dir=folder,
        ignore_patterns=ignored,
        max_workers=2,
        progress_callbacks=[Progress],
    )
    job.update(
        "checking_model", 98, downloaded_bytes=retained_bytes(folder, manifest), bytes_per_second=0
    )
    # A branch can move during a resumed download. Freeze its entire initial file
    # manifest and verify every file, rejecting a mixed-version snapshot.
    for item in manifest:
        job.check_cancelled()
        path = (folder / item["path"]).resolve()
        if not path.is_relative_to(folder.resolve()) or not path.is_file():
            raise ValueError("Invalid or missing file in ModelScope snapshot")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != item["sha256"]:
            raise ValueError(
                "ModelScope revision changed or file checksum failed; retry with a stable revision"
            )
    fresh = api.list_repo_files(repo_id, "model", revision=revision)
    fresh_manifest = [
        {"path": f.path, "bytes": f.size, "sha256": f.sha256}
        for f in sorted(fresh, key=lambda f: f.path)
        if not f.is_dir and not any(fnmatch.fnmatch(f.path, p) for p in ignored)
    ]
    if fresh_manifest != manifest:
        raise ValueError(
            "ModelScope revision changed during download; retry to obtain a consistent snapshot"
        )
    (folder / "onecat-model-manifest.json").write_text(
        json.dumps(
            {
                "provider": "modelscope",
                "repo_id": repo_id,
                "revision": revision,
                "manifest_sha256": identity,
                "files": manifest,
            },
            indent=2,
        )
    )
    record = inspect_model(str(folder), "modelscope", repo_id)
    record.update(
        {
            "catalog_id": approved["id"],
            "name": approved.get("name", record["name"]),
            "verified": True,
            "manifest": manifest,
            "revision": revision,
        }
    )
    record["file_identity"] = {
        f["path"]: [(folder / f["path"]).stat().st_size, (folder / f["path"]).stat().st_mtime_ns]
        for f in manifest
    }
    db.put("models", record["id"], record)
    db.patch("models", record["id"], {"revision": revision, "manifest_sha256": identity})
    result = {"model_id": record["id"], "path": str(folder), "provider": "modelscope"}
    if approved.get("role") == "target":
        from .catalog import create_default_profile

        try:
            result["profile_id"] = create_default_profile(record["id"])["id"]
        except ValueError as error:
            # The weights are complete even if the runtime/hardware is not ready.
            result["profile_pending_reason"] = str(error)
    return result


def remove(id: str, remove_files: bool = False):
    record = db.get("models", id)
    if not record:
        raise ValueError("Model not found")
    from .engine import private_state

    current = private_state()
    if (
        current.get("state") != "stopped"
        and current.get("profile", {}).get("model_path") == record["path"]
    ):
        raise ValueError("Stop this model before removing it")
    if remove_files:
        folder = Path(record["path"]).resolve()
        managed = Path(db.settings()["model_directory"]).expanduser().resolve()
        if (
            record["source"] != "modelscope"
            or folder == managed
            or not folder.is_relative_to(managed)
        ):
            raise ValueError("Imported model files are user-owned; only remove their library entry")
        shutil.rmtree(folder)
    db.delete("models", id)
