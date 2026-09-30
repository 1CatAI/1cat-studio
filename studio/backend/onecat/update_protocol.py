# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Signed, immutable release metadata shared by the publisher and OTA client."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

FORMAT = "onecat-studio-update-v1"  # Old package clients can bootstrap to the OTA client.
UPDATER_VERSION = 1
KEYS = Path(__file__).with_name("data") / "update-keys.json"
VERSION = re.compile(r"\d+\.\d+\.\d+-[a-f0-9]{10}\Z")


def canonical(manifest: dict) -> bytes:
    return json.dumps(
        {key: value for key, value in manifest.items() if key != "signature"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def release_id(manifest: dict) -> str:
    return hashlib.sha256(canonical(manifest)).hexdigest()


def parse_manifest(data: bytes) -> dict:
    if len(data) > 65536:
        raise ValueError("Update manifest is too large")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate update manifest field: " + key)
            result[key] = value
        return result

    value = json.loads(data, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("Invalid update manifest")
    return value


def validate_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(c.isspace() for c in value)
    ):
        raise ValueError("Invalid update URL")
    _ = parsed.port
    return value


def verify(manifest: dict, keys: dict | None = None) -> dict:
    """HTTP mirrors are allowed: authenticity comes from the pinned release key."""
    if manifest.get("format") != FORMAT:
        raise ValueError("Not a 1Cat Studio update channel")
    if not manifest.get("signature"):
        raise ValueError("This channel has no signed OTA release yet")
    trusted = keys if keys is not None else json.loads(KEYS.read_text())
    try:
        public = base64.b64decode(trusted[manifest["key_id"]], validate=True)
        signature = base64.b64decode(manifest["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(public).verify(signature, canonical(manifest))
    except (KeyError, TypeError, ValueError, InvalidSignature) as error:
        raise ValueError("Update signature verification failed") from error
    if not isinstance(manifest.get("version"), str) or not VERSION.fullmatch(manifest["version"]):
        raise ValueError("Invalid Studio release version")
    if not re.fullmatch(r"[a-f0-9]{64}", str(manifest.get("sha256", ""))):
        raise ValueError("Update SHA256 is required")
    if not re.fullmatch(r"[a-f0-9]{40}", str(manifest.get("source_commit", ""))):
        raise ValueError("Update source commit is required")
    for field in ("sequence", "size", "unpacked_size", "min_updater"):
        if type(manifest.get(field)) is not int or manifest[field] <= 0:
            raise ValueError("Invalid update " + field)
    if manifest["min_updater"] > UPDATER_VERSION:
        raise ValueError("This release requires a newer OTA client")
    if manifest.get("target") != "linux-x86_64":
        raise ValueError("Unsupported update platform")
    if manifest.get("channel") not in {"stable", "preview"}:
        raise ValueError("Unknown update channel")
    validate_url(manifest.get("url", ""))
    return manifest


def supported_host() -> bool:
    return platform.system() == "Linux" and platform.machine() in {"x86_64", "AMD64"}


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)
