#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Fetch the pinned official Codex package for the Studio offline bundle."""

import hashlib
import json
import shutil
import tarfile
import tempfile
from pathlib import Path

import requests

VERSION = "0.153.4"
SHA256 = "a822187e1a2420c61c5926721bfbd878701ed95547c9bb0d4de4498a16ba1821"
URL = "https://api.github.com/repos/openai/codex/releases/assets/545043416"
TARGET = Path(__file__).resolve().parents[1] / "vendor/codex" / VERSION


def main():
    manifest = TARGET / "onecat-component.json"
    if (
        manifest.is_file()
        and (TARGET / "LICENSE").is_file()
        and (TARGET / "NOTICE").is_file()
        and json.loads(manifest.read_text()).get("archive_sha256") == SHA256
    ):
        print(TARGET)
        return
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TARGET.parent) as temporary:
        root = Path(temporary)
        archive = root / "codex.tar.gz"
        with requests.get(
            URL, headers={"Accept": "application/octet-stream"}, stream=True, timeout=(15, 90)
        ) as response:
            response.raise_for_status()
            with archive.open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    stream.write(chunk)
        with archive.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != SHA256:
                raise ValueError("Official Codex archive SHA256 mismatch")
        extracted = root / "package"
        with tarfile.open(archive) as tar:
            tar.extractall(extracted, filter="data")
        binaries = list(extracted.rglob("bin/codex"))
        if len(binaries) != 1:
            raise ValueError("Unexpected Codex package layout")
        package = binaries[0].parent.parent
        license_response = requests.get(
            f"https://raw.githubusercontent.com/openai/codex/rust-v{VERSION}/LICENSE", timeout=30
        )
        license_response.raise_for_status()
        if "Apache License" not in license_response.text:
            raise ValueError("Missing Codex Apache license")
        (package / "LICENSE").write_text(license_response.text)
        notice = requests.get(
            f"https://raw.githubusercontent.com/openai/codex/rust-v{VERSION}/NOTICE", timeout=30
        )
        notice.raise_for_status()
        (package / "NOTICE").write_text(notice.text)
        data = {
            "name": "Codex",
            "version": VERSION,
            "archive_sha256": SHA256,
            "source": f"https://github.com/openai/codex/releases/tag/rust-v{VERSION}",
        }
        (package / "onecat-component.json").write_text(json.dumps(data, indent=2))
        if TARGET.exists():
            raise ValueError(
                "Existing Codex component differs; preserve it and inspect before replacing"
            )
        shutil.move(str(package), TARGET)
    print(TARGET)


if __name__ == "__main__":
    main()
