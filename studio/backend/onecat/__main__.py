# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="1Cat Studio Linux inference workbench")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--state-dir", type=Path)
    args = parser.parse_args()
    if args.state_dir:
        os.environ["ONECAT_STUDIO_HOME"] = str(args.state_dir.expanduser().resolve())
    from . import db
    from .config import initialize_paths

    root = initialize_paths()
    with (root / "manager.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("A Studio manager already owns this state directory")
        import uvicorn

        from .app import create_app

        settings = db.settings()
        uvicorn.run(
            create_app(),
            host=args.host or settings["host"],
            port=args.port or settings["port"],
            log_level="info",
            proxy_headers=False,
            timeout_graceful_shutdown=10,
        )


if __name__ == "__main__":
    main()
