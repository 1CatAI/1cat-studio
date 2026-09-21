# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from ..config import state_root

VERSION = "0.153.4"
BWRAP = Path("/usr/local/libexec/onecat-agent-bwrap")
_sandbox_checked = None


def component_root() -> Path:
    # Both source and offline bundle keep the same studio/vendor layout.
    return Path(__file__).resolve().parents[3] / "vendor/codex" / VERSION


def namespace_command() -> list[str]:
    binary = BWRAP if BWRAP.is_file() else component_root() / "codex-resources/bwrap"
    return [
        str(binary),
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/sbin",
        "/sbin",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/home",
        "--dir",
        "/etc",
    ]


def info() -> dict:
    global _sandbox_checked
    manifest = component_root() / "onecat-component.json"
    installed = manifest.is_file() and (component_root() / "bin/codex").is_file()
    # AppArmor policy can change without replacing the binary. Bound the cache
    # so installation recovery does not require restarting Studio.
    binary = BWRAP if BWRAP.is_file() else component_root() / "codex-resources/bwrap"
    identity = (str(binary), binary.stat().st_mtime_ns) if binary.is_file() else None
    if (
        not _sandbox_checked
        or _sandbox_checked[0] != identity
        or time.monotonic() - _sandbox_checked[2] >= 30
    ):
        try:
            result = subprocess.run(
                namespace_command() + ["/usr/bin/true"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            error = result.stderr.strip()[:400] if result.returncode else None
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = str(exc)[:400]
        _sandbox_checked = (identity, error, time.monotonic())
    return {
        "version": VERSION,
        "installed": installed,
        "sandbox_ready": _sandbox_checked[1] is None,
        "sandbox_error": _sandbox_checked[1],
        "source": f"https://github.com/openai/codex/releases/tag/rust-v{VERSION}",
        "network": "isolated",
        "model_protocol": "responses-to-chat",
        "model_verified": False,
    }


def prepare(project: dict, task: dict, socket_path: Path) -> tuple[list[str], dict]:
    status = info()
    if not status["installed"]:
        raise ValueError("Codex 组件未安装，请更新完整 Studio 安装包 / Codex component is missing")
    if not status["sandbox_ready"]:
        raise ValueError(
            "Agent 沙箱尚未就绪 / Agent sandbox is unavailable: " + status["sandbox_error"]
        )
    workspace = state_root() / "agent/projects" / project["id"] / "workspace"
    home = state_root() / "agent/tasks" / task["id"] / "codex"
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    context = task.get("context_window") or 32768
    config = {
        "model": task["model"],
        "model_provider": "onecat",
        "approval_policy": "on-request",
        "web_search": "disabled",
        "model_context_window": context,
        "model_auto_compact_token_limit": max(1024, int(context * 0.75)),
        "model_supports_reasoning_summaries": False,
        "check_for_update_on_startup": False,
        "project_doc_max_bytes": 16384,
    }
    lines = [f"{key} = {json.dumps(value)}" for key, value in config.items()]
    lines += [
        "[model_providers.onecat]",
        'name = "1Cat Studio local model"',
        'base_url = "http://127.0.0.1:9467/v1"',
        'wire_api = "responses"',
        "requires_openai_auth = false",
        "supports_websockets = false",
        "request_max_retries = 0",
        "stream_max_retries = 0",
        "[features]",
        "multi_agent = false",
        "apps = false",
        "memories = false",
        "shell_snapshot = false",
        "unified_exec = false",
        "enable_request_compression = false",
        "[shell_environment_policy]",
        'inherit = "none"',
        "[shell_environment_policy.set]",
        'PATH = "/usr/local/bin:/usr/bin:/bin"',
        'HOME = "/tmp/home"',
        'LANG = "C.UTF-8"',
        'PYTHONDONTWRITEBYTECODE = "1"',
    ]
    (home / "config.toml").write_text("\n".join(lines) + "\n")
    args = namespace_command() + [
        "--ro-bind"
        if task.get("permission") == "read-only"
        or task.get("mode") == "plan"
        or task.get("operation") in {"review", "skills", "compact"}
        else "--bind",
        str(workspace),
        "/workspace",
        "--bind",
        str(home),
        "/codex-home",
        "--dir",
        "/bridge",
        "--ro-bind",
        str(socket_path),
        "/bridge/model.sock",
        "--ro-bind",
        str(component_root()),
        "/opt/codex",
        "--ro-bind",
        str(Path(__file__).with_name("relay.py")),
        "/opt/onecat-relay.py",
        "--dir",
        "/tmp/home",
        "--chdir",
        "/workspace",
    ]
    for filename in ("/etc/ld.so.cache", "/etc/localtime"):
        if Path(filename).is_file():
            args += ["--ro-bind", filename, filename]
    args += [
        "--clearenv",
        "--setenv",
        "HOME",
        "/tmp/home",
        "--setenv",
        "CODEX_HOME",
        "/codex-home",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "PATH",
        "/opt/codex/codex-path:/usr/local/bin:/usr/bin:/bin",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "/usr/bin/python3",
        "/opt/onecat-relay.py",
    ]
    return args, {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
