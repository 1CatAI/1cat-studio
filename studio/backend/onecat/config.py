# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import os
from pathlib import Path


def state_root() -> Path:
    root = Path(os.environ.get("ONECAT_STUDIO_HOME", "~/.local/share/onecat-studio"))
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def initialize_paths() -> Path:
    root = state_root()
    os.environ["ONECAT_STUDIO_HOME"] = str(root)
    for name in ("logs", "jobs", "models", "runtimes", "backups", "benchmarks", "uploads"):
        (root / name).mkdir(exist_ok=True, mode=0o700)
    return root


def backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def frontend_dist() -> Path:
    override = os.environ.get("ONECAT_FRONTEND_DIST")
    return Path(override) if override else backend_root().parent / "frontend" / "dist"


def automatic_gpu_actions() -> bool:
    """Operators can deploy UI/manager updates without automatic hardware actions."""
    return os.environ.get("ONECAT_AUTO_GPU_ACTIONS", "1") != "0"
