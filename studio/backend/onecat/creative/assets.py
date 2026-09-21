# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import hashlib
import json
import subprocess
import warnings
from pathlib import Path

from PIL import Image, ImageOps

from .. import db
from ..config import state_root
from .store import require

MAX_BYTES = 256 * 1024**2
IMAGE_BYTES = 30 * 1024**2
MIME_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
}


def root() -> Path:
    path = state_root() / "attachments" / "creative"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def path_for(identity: str, thumbnail=False) -> tuple[dict, Path]:
    asset = require("creative_assets", identity)
    name = asset.get("thumbnail") if thumbnail else asset["filename"]
    path = (root() / (name or asset["filename"])).resolve()
    if not path.is_relative_to(root()) or not path.is_file():
        raise ValueError("Saved asset is unavailable; restore the original media backup")
    return asset, path


def ingest(source: Path, mime: str, name: str, *, run_id: str | None = None) -> dict:
    mime = mime.split(";")[0].strip().lower()
    if mime == "audio/x-wav":
        mime = "audio/wav"
    if mime not in MIME_SUFFIX:
        raise ValueError("Use PNG, JPEG, WebP, MP4, WebM, WAV or MP3")
    size = source.stat().st_size
    if not size or size > MAX_BYTES or (mime.startswith("image/") and size > IMAGE_BYTES):
        raise ValueError("Empty or oversized media (images: 30 MiB; video/audio: 256 MiB)")
    identity = db.uid()
    record = {
        "id": identity,
        "kind": mime.split("/")[0],
        "mime": mime,
        "bytes": size,
        "name": Path(name).name[:160],
        "filename": identity + MIME_SUFFIX[mime],
        "origin_run": run_id,
    }
    thumb = None
    if record["kind"] == "image":
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(source) as image:
                    if (
                        Image.MIME.get(image.format) != mime
                        or image.width * image.height > 40_000_000
                    ):
                        raise ValueError("Invalid image format or image exceeds 40 megapixels")
                    image.verify()
                with Image.open(source) as image:
                    image = ImageOps.exif_transpose(image)
                    record.update(width=image.width, height=image.height)
                    image.thumbnail((640, 640))
                    thumb = image.convert("RGB")
        except (
            OSError,
            SyntaxError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as error:
            raise ValueError(
                "Unreadable or oversized image; upload a valid PNG, JPEG or WebP"
            ) from error
    else:
        # Decode metadata only; no CUDA and no generated code execution.
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-show_entries",
                    "format=format_name,duration:stream=codec_type,width,height",
                    "-of",
                    "json",
                    str(source),
                ],
                capture_output=True,
                timeout=15,
                check=True,
            )
            data = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise ValueError(
                "Media is unreadable, or FFprobe is missing on the Studio server"
            ) from error
        streams = data.get("streams", [])
        if not any(s.get("codec_type") == record["kind"] for s in streams):
            raise ValueError("Media does not contain the expected audio/video stream")
        containers = data.get("format", {}).get("format_name", "").split(",")
        allowed = {
            "video/mp4": {"mov", "mp4"},
            "video/webm": {"matroska", "webm"},
            "audio/wav": {"wav"},
            "audio/mpeg": {"mp3"},
        }
        if not set(containers) & allowed[mime]:
            raise ValueError("Media container does not match its content type")
        record["duration"] = float(data.get("format", {}).get("duration", 0))
        if not 0 < record["duration"] < 3601:
            raise ValueError("Media duration must be between zero and one hour")
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        record.update({k: video[k] for k in ("width", "height") if k in video})
    with source.open("rb") as file:
        record["sha256"] = hashlib.file_digest(file, "sha256").hexdigest()
    target = root() / record["filename"]
    try:
        source.replace(target)
        target.chmod(0o600)
        if thumb:
            record["thumbnail"] = identity + "-thumb.jpg"
            thumb.save(root() / record["thumbnail"], "JPEG", quality=85)
            (root() / record["thumbnail"]).chmod(0o600)
        db.put("creative_assets", identity, record)
    except BaseException:
        target.unlink(missing_ok=True)
        (root() / (identity + "-thumb.jpg")).unlink(missing_ok=True)
        raise
    return record


def public(record: dict) -> dict:
    return {
        **{k: v for k, v in record.items() if k not in {"filename", "thumbnail"}},
        "url": f"/api/creative/assets/{record['id']}/content",
        "thumbnail_url": f"/api/creative/assets/{record['id']}/thumbnail"
        if record.get("thumbnail")
        else None,
    }
