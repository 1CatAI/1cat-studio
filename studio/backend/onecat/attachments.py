# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Authenticated image storage; inference receives bytes, never internal URLs."""

import base64
import io
import re
import time
import warnings

from PIL import Image, UnidentifiedImageError

from . import db
from .config import state_root

MAX_BYTES = 10 * 1024 * 1024
FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def store(data: bytes, name="image"):
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Each image must be at most 10 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                mime = FORMATS.get(image.format)
                if not mime or image.width * image.height > 40_000_000:
                    raise ValueError("Use PNG, JPEG or WebP with at most 40 megapixels")
                width, height = image.size
                image.verify()
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as error:
        raise ValueError("Invalid or oversized image") from error
    id = db.uid()
    folder = state_root() / "attachments"
    folder.mkdir(exist_ok=True, mode=0o700)
    target = folder / id
    target.write_bytes(data)
    target.chmod(0o600)
    record = {
        "id": id,
        "name": str(name)[:160],
        "mime": mime,
        "bytes": len(data),
        "width": width,
        "height": height,
        "created_at": time.time(),
    }
    db.put("attachments", id, record)
    return record


def read(id):
    if not isinstance(id, str) or not re.fullmatch(r"[a-f0-9]{32}", id):
        raise ValueError("Invalid attachment ID")
    record = db.get("attachments", id)
    path = state_root() / "attachments" / id
    if not record or not path.is_file():
        raise ValueError("Chat image is missing; upload it again")
    return record, path


def data_url(id):
    record, path = read(id)
    return "data:" + record["mime"] + ";base64," + base64.b64encode(path.read_bytes()).decode()


def validate_content(content, *, check_images=True):
    if not isinstance(content, list):
        raise ValueError("Message content must be an array")
    images = 0
    for part in content:
        if not isinstance(part, dict):
            raise ValueError("Invalid message part")
        if part.get("type") == "image":
            if not isinstance(part.get("attachment_id"), str) or not part["attachment_id"]:
                raise ValueError("Image requires an attachment ID")
            if check_images:
                read(part["attachment_id"])
            images += 1
        elif part.get("type") not in {"text", "reasoning"}:
            raise ValueError("Unsupported message content type")
        elif not isinstance(part.get("text", ""), str):
            raise ValueError("Message text must be a string")
    if images > 4:
        raise ValueError("At most four images are allowed per message")


def prepare_messages(messages, profile):
    if not isinstance(messages, list):
        raise ValueError("messages must be an array")
    result = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            validate_content(content)
            count = sum(p.get("type") == "image" for p in content)
            if count and not profile.get("vision_enabled"):
                raise ValueError("This model profile does not have image understanding enabled")
            if count > min(4, profile.get("max_images", 4)):
                raise ValueError("This message exceeds the model's image limit")
            content = [
                {"type": "image_url", "image_url": {"url": data_url(p["attachment_id"])}}
                if p["type"] == "image"
                else {"type": "text", "text": p.get("text", "")}
                for p in content
                if p["type"] != "reasoning"
            ]
        result.append({**message, "content": content})
    return result


def export_images(messages):
    ids = {p["attachment_id"] for m in messages for p in m["content"] if p.get("type") == "image"}
    return {id: {"name": read(id)[0]["name"], "data_url": data_url(id)} for id in ids}


def import_images(messages, supplied):
    if not isinstance(supplied, dict):
        raise ValueError("Imported attachments must be an object")
    mapping = {}
    try:
        for message in messages:
            for part in message.get("content", []) if isinstance(message.get("content"), list) else []:
                if part.get("type") != "image":
                    continue
                old = part.get("attachment_id")
                if old not in mapping:
                    source = supplied.get(old, {})
                    raw = source.get("data_url", "") if isinstance(source, dict) else None
                    if (
                        not isinstance(raw, str)
                        or len(raw) > MAX_BYTES * 4 // 3 + 100
                        or not re.match(r"^data:image/(png|jpeg|webp);base64,", raw)
                    ):
                        raise ValueError("Imported conversation has a missing or invalid image")
                    try:
                        data = base64.b64decode(raw.split(",", 1)[1], validate=True)
                    except ValueError as error:
                        raise ValueError("Invalid image encoding") from error
                    mapping[old] = store(data, source.get("name", "image"))["id"]
                part["attachment_id"] = mapping[old]
            if isinstance(message.get("content"), list):
                validate_content(message["content"])
        return list(mapping.values())
    except Exception:
        discard_images(mapping.values())
        raise


def discard_images(ids):
    """Roll back only the new attachments owned by an unsuccessful import."""
    for id in ids:
        (state_root() / "attachments" / id).unlink(missing_ok=True)
        db.delete("attachments", id)
