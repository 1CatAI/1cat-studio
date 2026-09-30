# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Pinned, unmodified OMP binary and per-task native configuration."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from ..config import state_root
from . import runtime

UPSTREAM_COMMIT = "061f21ef011c72df891678ce489b02edee676738"
VERSION = f"18.3.2+{UPSTREAM_COMMIT[:12]}"
PROTOCOL = "omp-rpc-ndjson"
PROTOCOL_VERSION = 2
SOURCE = f"https://github.com/can1357/oh-my-pi/tree/{UPSTREAM_COMMIT}"
_tokens: dict[str, dict] = {}
_verified: tuple | None = None


def component_root() -> Path:
    return Path(__file__).resolve().parents[3] / "vendor/oh-my-pi" / UPSTREAM_COMMIT


def binary_path() -> str | None:
    # PATH executables have no verifiable connection to the audited commit.
    path = component_root() / "omp"
    return str(path) if path.is_file() else None


def info() -> dict:
    global _verified
    root, binary = component_root(), binary_path()
    error = None
    installed = False
    try:
        manifest = json.loads((root / "onecat-component.json").read_text())
        if not binary or manifest.get("upstream_commit") != UPSTREAM_COMMIT:
            raise ValueError("PI pinned commit mismatch")
        if manifest.get("protocol_version") != PROTOCOL_VERSION:
            raise ValueError("PI RPC protocol version mismatch")
        path = Path(binary)
        stat = path.stat()
        identity = (str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns, manifest.get("sha256"))
        if not _verified or _verified[0] != identity:
            with path.open("rb") as stream:
                valid = hashlib.file_digest(stream, "sha256").hexdigest() == manifest.get("sha256")
            _verified = (identity, valid)
        if not _verified[1] or not os.access(path, os.X_OK):
            raise ValueError("PI binary checksum or execute permission mismatch")
        installed = True
    except (OSError, ValueError, TypeError) as exc:
        error = f"PI 固定版本尚未就绪 / Pinned PI runtime unavailable: {exc}"
    sandbox = runtime.info()
    sandbox_ready = sandbox["sandbox_ready"]
    return {
        "engine": "pi", "version": VERSION, "runtime_version": VERSION,
        "upstream_commit": UPSTREAM_COMMIT, "protocol": PROTOCOL,
        "protocol_version": PROTOCOL_VERSION, "client_version": "omp-rpc 0.1.0",
        "installed": installed, "ready": installed and sandbox_ready,
        "sandbox_ready": sandbox_ready, "sandbox_error": sandbox.get("sandbox_error"),
        "error": error or sandbox.get("sandbox_error"), "binary": binary,
        "source": SOURCE, "network": "isolated",
        "collaboration_modes": ["single", "auto", "swarm"],
        "capabilities": {"rpc": True, "resume": True, "subagents": True,
                         "progress": True, "batch": True, "native_concurrency": True},
    }


def issue_model_token(task_id: str, project_id: str, swarm_id: str | None = None,
                      *, engine_identity=None) -> str:
    now = time.time()
    for key in list(_tokens):
        if _tokens[key]["expires_at"] <= now:
            _tokens.pop(key, None)
    token = secrets.token_urlsafe(32)
    _tokens[hashlib.sha256(token.encode()).hexdigest()] = {
        "task_id": task_id, "project_id": project_id, "swarm_id": swarm_id,
        "engine": "pi", "engine_identity": engine_identity, "expires_at": now + 3600,
    }
    return token


def model_context(token: str | None) -> dict | None:
    if not token:
        return None
    key = hashlib.sha256(token.encode()).hexdigest()
    context = _tokens.get(key)
    if not context:
        return None
    if context["expires_at"] <= time.time():
        _tokens.pop(key, None)
        return None
    return dict(context)


def revoke_model_token(token: str | None) -> None:
    if token:
        _tokens.pop(hashlib.sha256(token.encode()).hexdigest(), None)


def _write(path: Path, value: dict):
    # JSON is valid YAML and preserves model identifiers containing punctuation.
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)


def prepare(project: dict, task: dict, socket_path: Path) -> tuple[list[str], dict, str]:
    status = info()
    if not status["ready"]:
        raise ValueError(status["error"] or "PI runtime unavailable")
    workspace = state_root() / "agent/projects" / project["id"] / "workspace"
    home = state_root() / "agent/tasks" / task["id"] / "pi"
    session = home / "sessions"
    config_dir = home / ".omp/agent"
    session.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    task["pi_session_dir"] = "/pi-home/sessions"
    token = issue_model_token(task["id"], project["id"], task.get("swarm_id"),
                              engine_identity=task.get("engine_identity"))
    try:
        mode = task.get("collaboration", "single")
        selector = "onecat/" + task["model"]
        _write(config_dir / "models.yml", {"providers": {"onecat": {
            "baseUrl": "http://127.0.0.1:9467/internal/agent/v1",
            "api": "openai-completions", "apiKey": token,
            "models": [{"id": task["model"], "name": task["model"],
                        "contextWindow": int(task.get("context_window") or 32768),
                        "maxTokens": min(8192, int(task.get("context_window") or 32768)),
                        "compat": {"supportsDeveloperRole": False, "maxTokensField": "max_tokens"}}],
        }}})
        # Mode defaults sit below PI project/explicit config. In particular, no
        # maxConcurrency, recursion, isolation, or merge setting is generated.
        native_defaults = {"task": {"batch": True}, "async": {"enabled": True}} if mode == "swarm" else {}
        _write(config_dir / "config.yml", native_defaults)
        mode_config = {"modelRoles": {"default": selector}, "startup": {"checkUpdate": False}}
        if mode != "single":
            mode_config["task"] = {"eager": "always" if mode == "swarm" else "preferred"}
        _write(home / "studio.yml", mode_config)
        readonly = task.get("execution_permission") == "read-only"
        args = runtime.namespace_command() + [
            "--ro-bind" if readonly else "--bind", str(workspace), "/workspace",
            "--bind", str(home), "/pi-home",
            "--ro-bind", str(component_root()), "/opt/pi",
            "--dir", "/bridge", "--ro-bind", str(socket_path), "/bridge/model.sock",
            "--ro-bind", str(Path(__file__).with_name("relay.py")), "/opt/onecat-relay.py",
            "--ro-bind", str(config_dir / "models.yml"), "/pi-home/.omp/agent/models.yml",
            "--ro-bind", str(home / "studio.yml"), "/studio.yml",
            "--chdir", "/workspace",
        ]
        # Users manage PI's native settings here or in the project's .omp config.
        native = state_root() / "agent/pi/config.yml"
        configs = []
        if native.is_file():
            args += ["--ro-bind", str(native), "/pi-native.yml"]
            configs += ["--config", "/pi-native.yml"]
        extra = []
        if mode == "single":
            args += ["--ro-bind", str(Path(__file__).with_name("pi_single.ts")), "/opt/pi-single.ts"]
            extra += ["--trusted-extension", "/opt/pi-single.ts", "--append-system-prompt",
                      "This task uses single-agent mode. Subagent spawning is disabled (spawns: false)."]
        for filename in ("/etc/ld.so.cache", "/etc/localtime"):
            if Path(filename).is_file():
                args += ["--ro-bind", filename, filename]
        args += ["--clearenv", "--setenv", "HOME", "/pi-home",
                 "--setenv", "LANG", "C.UTF-8", "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
                 "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
                 "/usr/bin/python3", "/opt/onecat-relay.py", "--pi",
                 "/opt/pi/omp", "--mode", "rpc", "--session-dir", "/pi-home/sessions",
                 "--no-ui", "--cwd", "/workspace", *configs, "--config", "/studio.yml",
                 "--provider", "onecat", "--model", task["model"], *extra]
        return args, {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, token
    except BaseException:
        revoke_model_token(token)
        raise
