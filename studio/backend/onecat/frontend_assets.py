# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Keep open browser tabs usable across an installed-release switch."""

import json
import re
from pathlib import Path


def retained_asset(dist: Path, path: str) -> Path | None:
    # Only immutable, public JS/CSS chunks. Never search retired releases for
    # documents, source archives, credentials, arbitrary paths or symlink targets.
    if not re.fullmatch(r"assets/[\w.-]+-[\w-]{8,}\.(?:js|css)", path, flags=re.ASCII):
        return None
    if len(dist.parents) < 4 or dist.parts[-3:] != ("studio", "frontend", "dist"):
        return None
    releases = dist.parents[3]
    if releases.name != "releases":
        return None
    for release in releases.iterdir():
        if release.is_symlink() or not release.is_dir():
            continue
        try:
            manifest = json.loads((release / "manifest.json").read_text())
            if not isinstance(manifest, dict) or manifest.get("format") != "onecat-studio-linux-v1":
                continue
            root = release / "studio/frontend/dist"
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root / "assets") and candidate.is_file():
                return candidate
        except (OSError, ValueError):
            continue
    return None
