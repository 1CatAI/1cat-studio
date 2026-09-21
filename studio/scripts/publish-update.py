#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Publish a Studio bundle to an update channel directory.

The channel is static: one manifest per release plus `latest.json` for clients.
Copy the directory to the web root of the update host afterwards.
"""

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

FORMAT = "onecat-studio-update-v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, help="onecat-studio-<version>-linux-x86_64.tar.gz")
    parser.add_argument("--out", required=True, help="channel directory, e.g. /var/www/onecat-studio-updates/studio")
    parser.add_argument("--base-url", required=True, help="public URL prefix that serves --out")
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--notes", default="")
    parser.add_argument("--min-version", default=None)
    args = parser.parse_args()

    bundle = Path(args.bundle).expanduser().resolve()
    if not bundle.is_file():
        raise SystemExit("bundle not found: %s" % bundle)
    name = bundle.name
    version = name[len("onecat-studio-") : -len("-linux-x86_64.tar.gz")]
    if not version:
        raise SystemExit("bundle name does not carry a version")

    digest = hashlib.sha256()
    with bundle.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 22), b""):
            digest.update(block)

    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundle, out / name)

    manifest = {
        "format": FORMAT,
        "channel": args.channel,
        "version": version,
        "url": args.base_url.rstrip("/") + "/" + name,
        "sha256": digest.hexdigest(),
        "size": bundle.stat().st_size,
        "release_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "notes": args.notes,
        "min_version": args.min_version,
    }
    for target in (out / (version + ".json"), out / "latest.json"):
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
