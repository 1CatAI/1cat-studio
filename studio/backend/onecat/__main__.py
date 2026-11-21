# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import argparse
import copy
import fcntl
import logging
import os
from pathlib import Path


class QuietPolling(logging.Filter):
    """Hide successful GETs, which are the UI's polling; keep writes and errors."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        return not (
            isinstance(args, tuple) and len(args) == 5 and args[1] == "GET" and args[4] < 400
        )


def log_config() -> dict:
    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    config["filters"] = {"quiet_polling": {"()": QuietPolling}}
    config["loggers"]["uvicorn.access"]["filters"] = ["quiet_polling"]
    return config


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
        host = args.host or "0.0.0.0"
        os.environ["ONECAT_STUDIO_BIND_MODE"] = "explicit" if args.host else "auto"
        port = args.port or settings["port"]
        os.environ["ONECAT_STUDIO_LISTEN_HOST"] = host
        os.environ["ONECAT_STUDIO_LISTEN_PORT"] = str(port)
        app = create_app()
        app.state.lan_api_only = args.host is None and settings["host"] == "127.0.0.1"
        app.state.listen_host = host
        app.state.listen_port = port
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_config=log_config(),
            log_level="info",
            proxy_headers=False,
            timeout_graceful_shutdown=10,
        )
        server = uvicorn.Server(config)
        if os.environ.get("ONECAT_LOCAL_DESKTOP") == "1":
            import socket
            from .agent.desktop import ADDRESS, PORT
            with config.bind_socket() as primary, socket.create_server((ADDRESS, PORT)) as local:
                server.run(sockets=[primary, local])
        else:
            server.run()


if __name__ == "__main__":
    main()
