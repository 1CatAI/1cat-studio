# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Install the bundled, bounded GPU helper through system authorization."""

import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import db, gpu
from .jobs import TERMINAL, Job

# Bumped whenever the installed helper and Studio must agree on a new binding
# policy shape. Studio refuses to drive an older helper rather than writing it a
# policy it cannot enforce.
HELPER_PROTOCOL = 3
POLICY = Path("/etc/onecat-studio/gpu-helper.json")


def installer():
    root = Path(__file__).resolve().parents[2]
    candidates = [
        root / "scripts/configure-gpu-helper.py",
        root.parent / "scripts/configure-gpu-helper.py",
    ]
    return next((path for path in candidates if path.is_file()), candidates[0])


def helper_check():
    if not gpu.helper_path().is_file():
        return {}
    try:
        result = subprocess.run(
            ["sudo", "-n", str(gpu.helper_path()), "check"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def stored_binding():
    """This user's on-disk policy, readable without elevation for diagnostics."""
    try:
        users = json.loads(POLICY.read_text())["users"]
        return users.get(getpass.getuser())
    except (OSError, ValueError, KeyError, TypeError):
        return None


def pinned_uuids(policy):
    if isinstance(policy, list):
        return list(policy)
    if isinstance(policy, dict) and policy.get("mode") == "uuids":
        return list(policy.get("uuids") or [])
    return []


def binding_options(devices):
    """The bindings the wizard can offer, derived from the boards present."""
    classes = {}
    for device in devices:
        capability = device.get("compute_capability")
        if not capability:
            continue
        group = classes.setdefault(
            tuple(capability), {"compute_capability": list(capability), "count": 0, "names": []}
        )
        group["count"] += 1
        if device.get("name") and device["name"] not in group["names"]:
            group["names"].append(device["name"])
    return {
        "all_gpus": len(devices),
        "compute_classes": [
            {**group, "name": " / ".join(group["names"])}
            for _, group in sorted(classes.items())
        ],
    }


def status():
    helper = helper_check()
    devices = gpu.snapshot().get("gpus", [])
    allowed = set(helper.get("allowed_uuids", []))
    jobs = [j for j in db.all_records("jobs") if j.get("kind") == "configure_gpu_helper"]
    latest = jobs[0] if jobs else None
    pending = latest if latest and latest["state"] not in TERMINAL else None
    stored = stored_binding()
    absent = [u for u in pinned_uuids(stored) if u not in {d["uuid"] for d in devices}]
    available = bool(helper.get("available")) and helper.get("protocol", 0) >= HELPER_PROTOCOL
    upgrade_required = bool(helper.get("available")) and helper.get("protocol", 0) < HELPER_PROTOCOL
    bundled = installer().is_file()
    can_authorize = bool(shutil.which("sudo") or shutil.which("pkexec"))
    value = {
        "bundled": bundled,
        "installed": gpu.helper_path().is_file(),
        "available": available,
        "upgrade_required": upgrade_required,
        "protocol": helper.get("protocol", 0),
        "required_protocol": HELPER_PROTOCOL,
        "gpu_count": len(devices),
        "allowed_gpu_count": sum(d["uuid"] in allowed for d in devices),
        "restricted": bool(stored) and len(allowed) < len(devices),
        "binding_mode": helper.get("mode")
        or (stored.get("mode") if isinstance(stored, dict) else "uuids" if stored else None),
        "missing_gpu_count": len(absent),
        "hardware_changed": bool(absent),
        "can_authorize": can_authorize,
        "desktop_authorization": bool(
            shutil.which("pkexec")
            and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        ),
        "options": binding_options(devices),
        "devices": [
            {
                "uuid": d.get("uuid"),
                "index": d.get("index"),
                "name": d.get("name"),
                "memory_total_mib": d.get("memory_total_mib"),
                "compute_capability": d.get("compute_capability"),
                "bound": d.get("uuid") in allowed,
            }
            for d in devices
        ],
        "job": {k: pending.get(k) for k in ("id", "state", "stage")} if pending else None,
        "error": latest.get("error")
        if latest and latest["state"] == "failed" and not available
        else None,
    }
    value["stage"] = setup_stage(value, helper)
    return value


def setup_stage(state, helper):
    """The single step the wizard must show next, so the UI cannot guess."""
    if not state["bundled"]:
        return "unsupported"
    if not state["gpu_count"]:
        return "no_gpus"
    if state["job"]:
        return "authorizing"
    if state["available"]:
        return "ready"
    if state["upgrade_required"]:
        return "upgrade"
    if state["hardware_changed"] and not state["allowed_gpu_count"]:
        return "changed"
    return "unbound" if state["can_authorize"] else "manual"


def binding_request(payload):
    """Validate the wizard's choice before it reaches the elevated installer."""
    if payload.keys() - {"mode", "uuids", "rebind"}:
        raise ValueError("Unknown GPU binding field")
    mode = payload.get("mode") or "class"
    if mode not in {"class", "all", "uuids"}:
        raise ValueError("Unknown GPU binding mode")
    request = {"mode": mode}
    if mode == "uuids":
        uuids = payload.get("uuids")
        if not isinstance(uuids, list) or not uuids:
            raise ValueError("选择至少一张显卡 / Select at least one GPU")
        if any(
            not isinstance(u, str) or not re.fullmatch(r"GPU-[a-fA-F0-9-]{36}", u) for u in uuids
        ):
            raise ValueError("Use GPU UUIDs from the device list")
        present = {d["uuid"] for d in gpu.snapshot()["gpus"]}
        if any(u not in present for u in uuids):
            raise ValueError("选择本机存在的显卡 / Select GPUs present on this machine")
        request["uuids"] = sorted(set(uuids))
    if payload.get("rebind"):
        request["rebind"] = True
    return request


def configure(job: Job):
    script = installer()
    if not script.is_file():
        raise ValueError(
            "安装包缺少 GPU 控制组件，请更新 Studio / GPU component missing; update Studio"
        )
    request = job.payload if isinstance(job.payload, dict) else {}
    options = []
    mode = str(request.get("mode") or "class")
    if mode not in {"class", "all", "uuids"}:
        raise ValueError("Unknown GPU binding mode")
    options += ["--mode", mode]
    if request.get("rebind"):
        options.append("--rebind")
    uuids = [str(u) for u in request.get("uuids") or []]
    if mode == "uuids" and not uuids:
        raise ValueError("选择至少一张显卡 / Select at least one GPU")
    job.update("awaiting_system_authorization")
    process = subprocess.Popen(
        [sys.executable, str(script), "--authorize", *options, *uuids],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    deadline = time.monotonic() + 180
    try:
        while process.poll() is None:
            job.check_cancelled()
            if time.monotonic() > deadline:
                raise ValueError("系统授权超时，请重试 / System authorization timed out; retry")
            time.sleep(0.2)
        stdout, stderr = process.communicate()
        if process.returncode:
            # No password is ever accepted, stored or forwarded by the web API.
            detail = stderr.decode(errors="replace").strip()
            if process.returncode not in {3, 126, 127} and not any(
                word in detail.lower() for word in ("authenticat", "authoriz", "agent", "password")
            ):
                raise ValueError("GPU 控制配置失败 / GPU control setup failed: " + detail[-1200:])
            raise ValueError(
                "系统授权未完成。请在服务器桌面的授权窗口输入系统管理员密码后重试；无桌面的服务器需要管理员在安装时完成授权。 / "
                "System authorization was not completed. Approve the server desktop dialog; headless servers need administrator authorization during installation."
            )
        job.update("checking_gpu_control")
        checked = helper_check()
        if not checked.get("available"):
            raise ValueError(
                checked.get("reason")
                or "管理员策略尚未允许当前用户控制 GPU / GPU access is restricted by the administrator policy"
            )
        if checked.get("protocol", 0) < HELPER_PROTOCOL:
            raise ValueError("GPU 控制组件更新未完成，请重试 / GPU component update did not complete; retry")
        from .gpu_control import reconcile_saved

        reconcile_saved()
        return {"available": True, "allowed_uuids": checked.get("allowed_uuids", [])}
    finally:
        if process.poll() is None:
            import signal

            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
