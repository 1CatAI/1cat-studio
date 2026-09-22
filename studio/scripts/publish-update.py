#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Sign an immutable OTA release; atomically promote latest.json last.

Run against a local staging directory, then upload the bundle and version manifest
before atomically renaming latest.json on the VPS. Keep the private key offline.
"""

import argparse
import base64
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from onecat import update_protocol as protocol


def publish(
    bundle: Path,
    out: Path,
    base_url: str,
    key_path: Path,
    key_id: str,
    channel: str = "stable",
    notes: str = "",
) -> dict:
    with tarfile.open(bundle) as archive:
        members = archive.getmembers()
        headers = [
            m for m in members if m.name.count("/") == 1 and m.name.endswith("/manifest.json")
        ]
        if len(headers) != 1:
            raise ValueError("Bundle must have one top-level manifest")
        packaged = json.load(archive.extractfile(headers[0]))
        unpacked_size = sum(m.size for m in members)
    if packaged.get("format") != "onecat-studio-linux-v1":
        raise ValueError("Not a Studio Linux bundle")
    version = packaged["version"]
    name = f"onecat-studio-{version}-linux-x86_64.tar.gz"
    if bundle.name != name:
        raise ValueError("Bundle filename does not match its manifest")
    with bundle.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = {
        "format": protocol.FORMAT,
        "channel": channel,
        "version": version,
        "source_commit": packaged["source_commit"],
        "sequence": packaged["sequence"],
        "url": base_url.rstrip("/") + "/" + name,
        "sha256": sha,
        "size": bundle.stat().st_size,
        "unpacked_size": unpacked_size,
        "target": "linux-x86_64",
        "min_updater": 1,
        "key_id": key_id,
        "release_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "notes": notes,
    }
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("An Ed25519 release signing key is required")
    manifest["signature"] = base64.b64encode(key.sign(protocol.canonical(manifest))).decode()
    protocol.verify(manifest)  # The shipped client must trust this signing key.
    out.mkdir(parents=True, exist_ok=True)
    version_path = out / (version + ".json")
    if version_path.exists():
        raise ValueError("Release already published; never change an existing release")
    latest = out / "latest.json"
    if latest.exists():
        previous = json.loads(latest.read_text())
        if manifest["sequence"] <= previous.get("sequence", 0):
            raise ValueError("Release sequence must increase")
    target = out / name
    if target.resolve() != bundle.resolve():
        if target.exists():
            raise ValueError("Release asset already exists")
        fd, temp = tempfile.mkstemp(prefix=".upload-", dir=out)
        try:
            with os.fdopen(fd, "wb") as destination, bundle.open("rb") as source:
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            os.chmod(temp, 0o644)
            os.replace(temp, target)
        finally:
            Path(temp).unlink(missing_ok=True)
    protocol.atomic_json(version_path, manifest)
    version_path.chmod(0o644)
    protocol.atomic_json(latest, manifest)
    latest.chmod(0o644)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--channel", choices=["stable", "preview"], default="stable")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()
    print(
        json.dumps(
            publish(
                args.bundle.resolve(),
                args.out.resolve(),
                args.base_url,
                args.signing_key.expanduser(),
                args.key_id,
                args.channel,
                args.notes,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
