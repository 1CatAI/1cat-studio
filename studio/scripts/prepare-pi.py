#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Build the audited OMP commit without modifying its source, for offline bundles."""
import argparse
import base64
import hashlib
import json
import platform
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import requests

COMMIT = "061f21ef011c72df891678ce489b02edee676738"
NATIVE_VERSION = "18.3.2"
NATIVE_COMMIT = "7853b4e499936f9dcc13c9b64adb55f6b342aabf"
NATIVE_INTEGRITY = "bS7BeDKwhV7W3lnKv3ZuhzAjZJjc9lR6muTLC8BWDGVzd2LCiQuwyJchfxs0qOXqkZFMinNmBu2qciLwhxfWJg=="
NATIVE_URL = "https://registry.npmjs.org/@oh-my-pi/pi-natives-linux-x64/-/pi-natives-linux-x64-18.3.2.tgz"
STUDIO = Path(__file__).resolve().parents[1]
TARGET = STUDIO / "vendor/oh-my-pi" / COMMIT


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare_native(source: Path):
    # Published native code has identical crates, Cargo.lock and Bazel inputs
    # to COMMIT. Pin the archive as well as its version; never use npm @latest.
    cache = Path.home() / ".cache/onecat-studio-build"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"pi-natives-linux-x64-{NATIVE_VERSION}.tgz"
    if not archive.is_file():
        with tempfile.TemporaryDirectory(dir=cache) as temporary:
            downloaded = Path(temporary) / "native.tgz"
            with requests.get(NATIVE_URL, stream=True, timeout=(15, 180)) as response:
                response.raise_for_status()
                with downloaded.open("wb") as stream:
                    for chunk in response.iter_content(1024 * 1024):
                        stream.write(chunk)
            verify_native(downloaded)
            downloaded.replace(archive)
    verify_native(archive)
    with tarfile.open(archive) as package:
        for variant in ("baseline", "modern"):
            name = f"pi_natives.linux-x64-{variant}.node"
            member = package.getmember("package/" + name)
            if not member.isfile():
                raise ValueError("Unexpected PI native addon layout")
            with package.extractfile(member) as stream, (source / "packages/natives/native" / name).open("wb") as target:
                shutil.copyfileobj(stream, target)


def verify_native(archive: Path):
    with archive.open("rb") as stream:
        actual = base64.b64encode(hashlib.file_digest(stream, "sha512").digest()).decode()
    if actual != NATIVE_INTEGRITY:
        raise ValueError("Pinned PI native archive SHA512 mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Clean checkout of the exact audited commit")
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("This Studio bundle supports Linux x86_64")
    if TARGET.exists():
        data = json.loads((TARGET / "onecat-component.json").read_text())
        if (data.get("upstream_commit") != COMMIT or data.get("protocol_version") != 2
                or digest(TARGET / "omp") != data.get("sha256")):
            raise SystemExit("Existing PI component differs; inspect it before replacing")
        print(TARGET)
        return
    bun = shutil.which("bun")
    if not bun:
        raise SystemExit("Bun >= 1.4 is required to build pinned PI; install Bun, then rerun scripts/prepare-pi.py")
    bun_version = subprocess.check_output([bun, "--version"], text=True).strip()
    if tuple(int(part) for part in bun_version.split(".")[:2]) < (1, 4):
        raise SystemExit("Bun >= 1.4 is required; found " + bun_version)
    with tempfile.TemporaryDirectory(prefix="onecat-build-pi-") as temporary:
        root = Path(temporary)
        source = args.source.resolve() if args.source else root / "source"
        if not args.source:
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "fetch", "--depth=1", "https://github.com/can1357/oh-my-pi.git", COMMIT], check=True)
            subprocess.run(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"], check=True)
        head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"], text=True).strip()
        if head != COMMIT or dirty:
            raise SystemExit("PI source must be a clean checkout of " + COMMIT)
        subprocess.run([bun, "install", "--frozen-lockfile"], cwd=source, check=True)
        prepare_native(source)
        subprocess.run([bun, "run", "build"], cwd=source / "packages/coding-agent", check=True)
        package = root / "package"
        package.mkdir()
        shutil.copy2(source / "packages/coding-agent/dist/omp", package / "omp")
        shutil.copy2(source / "LICENSE", package / "LICENSE")
        for candidate in (source / "THIRD-PARTY-NOTICES.txt", source / "packages/coding-agent/dist/THIRD-PARTY-NOTICES.txt", source / "packages/natives/THIRD-PARTY-NOTICES.txt"):
            if candidate.is_file():
                shutil.copy2(candidate, package / "THIRD-PARTY-NOTICES.txt")
                break
        manifest = {"name": "Oh My Pi", "upstream_commit": COMMIT, "protocol_version": 2,
                    "sha256": digest(package / "omp"), "source": "https://github.com/can1357/oh-my-pi/tree/" + COMMIT,
                    "bun_version": bun_version, "native_version": NATIVE_VERSION,
                    "native_source_commit": NATIVE_COMMIT, "native_archive_integrity": "sha512-" + NATIVE_INTEGRITY}
        (package / "onecat-component.json").write_text(json.dumps(manifest, indent=2) + "\n")
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(package), TARGET)
    print(TARGET)


if __name__ == "__main__":
    main()
