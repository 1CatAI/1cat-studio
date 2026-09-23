#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Configure the bundled GPU helper. Elevation happens only during installation."""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HELPER = Path("/usr/local/libexec/onecat-gpu-helper")
CONFIG = Path("/etc/onecat-studio/gpu-helper.json")
SUDOERS = Path("/etc/sudoers.d/onecat-studio")
SOURCE = Path(__file__).resolve().with_name("onecat-gpu-helper.py")
SMI = "/usr/bin/nvidia-smi"
UUID = re.compile(r"GPU-[a-fA-F0-9-]{36}")
POLICY_FIELDS = {"mode", "uuids", "compute_capability", "min_memory_mib"}


def detected_gpus():
    """Every GPU present, with the facts a machine-independent binding needs."""
    fields = "uuid,compute_cap,memory.total,power.min_limit,name"
    try:
        result = subprocess.run(
            [SMI, "--query-gpu=" + fields, "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("NVIDIA driver is unavailable: " + str(error)[:300])
    if result.returncode:
        raise ValueError("NVIDIA driver is unavailable: " + result.stderr.strip()[:300])
    devices = []
    seen = set()
    for values in csv.reader(result.stdout.splitlines()):
        values = [value.strip() for value in values]
        if len(values) < 5 or not UUID.fullmatch(values[0]) or values[0] in seen:
            continue
        seen.add(values[0])
        try:
            capability = [int(part) for part in values[1].split(".")]
        except ValueError:
            capability = None
        try:
            memory = int(float(values[2]))
        except ValueError:
            memory = None
        devices.append(
            {
                "uuid": values[0],
                "compute_capability": capability,
                "memory_mib": memory,
                "manageable": _reports_power(values[3]),
                "name": values[4],
            }
        )
    return devices


def _reports_power(value):
    try:
        float(value)
        return True
    except ValueError:
        return False


def class_filter(policy):
    """Mirrors the installed helper so Studio can show the same resolution."""
    capability = policy.get("compute_capability") or []
    classes = (
        [tuple(part) for part in capability]
        if capability and isinstance(capability[0], list)
        else [tuple(capability)]
        if capability
        else []
    )
    floor = policy.get("min_memory_mib") or 0

    def matches(facts):
        if not facts["manageable"]:
            return False
        if classes and tuple(facts["compute_capability"] or ()) not in classes:
            return False
        return (facts["memory_mib"] or 0) >= floor

    return matches


def class_policy(devices):
    """A binding that means the same thing on any machine of these classes."""
    usable = [d for d in devices if d["compute_capability"] and d["manageable"]]
    if not usable:
        return {"mode": "all"}
    # A small display adapter must not join the accelerator control group.
    best = max(usable, key=lambda d: (d["memory_mib"] or 0, d["compute_capability"]))
    return {
        "mode": "class",
        "compute_capability": best["compute_capability"],
        "min_memory_mib": min(15360, best["memory_mib"] or 0),
    }


def valid_capability(value):
    def parts(items):
        return bool(items) and all(
            isinstance(p, int) and not isinstance(p, bool) and p >= 0 for p in items
        )

    if not isinstance(value, list) or not value:
        return False
    return parts(value) or all(isinstance(c, list) and parts(c) for c in value)


def valid_policy(policy):
    if isinstance(policy, list):
        return all(isinstance(u, str) and UUID.fullmatch(u) for u in policy)
    if not isinstance(policy, dict) or policy.keys() - POLICY_FIELDS:
        return False
    mode = policy.get("mode")
    if mode == "uuids":
        pinned = policy.get("uuids")
        return isinstance(pinned, list) and all(
            isinstance(u, str) and UUID.fullmatch(u) for u in pinned
        )
    if mode == "all":
        return True
    if mode == "class":
        if not valid_capability(policy.get("compute_capability")):
            return False
        floor = policy.get("min_memory_mib")
        return floor is None or (isinstance(floor, int) and not isinstance(floor, bool) and floor >= 0)
    return False


def policy_is_empty(policy):
    """A policy grants sudo rules only when it can resolve to real GPUs."""
    if isinstance(policy, list):
        return not policy
    if not isinstance(policy, dict):
        return True
    if policy.get("mode") == "uuids":
        return not policy.get("uuids")
    return policy.get("mode") not in {"class", "all"}


def pinned(policy):
    """True when a policy names exact GPUs, which are specific to one machine."""
    if isinstance(policy, list):
        return bool(policy)
    return isinstance(policy, dict) and policy.get("mode") == "uuids" and bool(policy.get("uuids"))


def resolved_uuids(policy, devices):
    if isinstance(policy, list):
        policy = {"mode": "uuids", "uuids": policy}
    present = {d["uuid"] for d in devices}
    if policy.get("mode") == "uuids":
        return [u for u in policy.get("uuids") or [] if u in present]
    if policy.get("mode") == "all":
        return sorted(d["uuid"] for d in devices if d["manageable"])
    if policy.get("mode") != "class":
        return []
    matches = class_filter(policy)
    return sorted(d["uuid"] for d in devices if matches(d))


def trusted_directory(path):
    """Never place a privileged executable beneath a writable/symlinked directory."""
    if path == path.parent:
        return
    trusted_directory(path.parent)
    if path.is_symlink():
        raise ValueError("Installation directory must not be a symlink: " + str(path))
    path.mkdir(mode=0o755, exist_ok=True)
    stat = path.stat()
    if stat.st_uid != 0 or stat.st_mode & 0o022:
        raise ValueError("Installation directory must be root-owned: " + str(path))


def replace_file(path, data, mode):
    fd, temporary = tempfile.mkstemp(prefix=".onecat-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def configure(user, requested, mode="auto", rebind=False):
    if os.geteuid() != 0:
        raise ValueError("System authorization is required")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user) or pwd.getpwnam(user).pw_uid == 0:
        raise ValueError("Select a non-root Studio user")
    caller = os.environ.get("PKEXEC_UID")
    if caller and int(caller) != 0 and pwd.getpwuid(int(caller)).pw_name != user:
        raise ValueError("The authorized user must match the Studio user")
    devices = detected_gpus()
    if not devices:
        return {"state": "no_gpus", "allowed_uuids": [], "binding": None}
    present = {d["uuid"] for d in devices}
    if requested and (
        len(set(requested)) != len(requested) or any(u not in present for u in requested)
    ):
        raise ValueError("Requested GPUs must be unique UUIDs present on this machine")
    if requested and mode in {"class", "all"}:
        raise ValueError("Choose either explicit GPU UUIDs or a machine-independent binding")
    if mode == "auto":
        # Pre-installation cannot know the target machine, so the default binds a
        # compute class rather than the serial numbers of the build host.
        mode = "uuids" if requested else "class"
    if mode == "uuids" and not requested:
        raise ValueError("Explicit UUID binding needs at least one GPU UUID")
    for path in (HELPER.parent, CONFIG.parent, SUDOERS.parent):
        trusted_directory(path)
    lock_path = CONFIG.parent / "install.lock"
    if lock_path.is_symlink():
        raise ValueError("Invalid installation lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        config = {"users": {}}
        if CONFIG.exists() or CONFIG.is_symlink():
            if CONFIG.is_symlink() or CONFIG.stat().st_uid != 0 or CONFIG.stat().st_mode & 0o022:
                raise ValueError("Existing helper policy has unsafe ownership or permissions")
            config = json.loads(CONFIG.read_text())
        users = config.get("users")
        if not isinstance(users, dict):
            raise ValueError("Invalid existing GPU policy")
        existing = users.get(user)
        if mode == "uuids":
            policy = {"mode": "uuids", "uuids": list(requested)}
        elif mode == "all":
            policy = {"mode": "all"}
        else:
            policy = class_policy(devices)
        # An explicit administrator pin is machine-specific and survives upgrades
        # untouched, which is what makes a deliberate restriction stick. A
        # machine-independent binding is instead re-derived from the live host on
        # every run, so re-imaging or replacing cards re-binds automatically
        # instead of leaving the machine pointed at hardware it never had.
        if existing is None or rebind or mode != "uuids" or not pinned(existing):
            users[user] = policy
        config["version"] = 3
        for name, value in users.items():
            if not re.fullmatch(r"[a-z_][a-z0-9_-]*", name) or not valid_policy(value):
                raise ValueError("Invalid existing GPU policy")
        rules = "# Managed by 1Cat Studio; only the bounded GPU helper is allowed.\n"
        for name, value in sorted(users.items()):
            if not policy_is_empty(value):
                commands = ", ".join(
                    str(HELPER) + " " + action for action in ("check", "snapshot", "apply")
                )
                rules += f"{name} ALL=(root) NOPASSWD: {commands}\n"
        with tempfile.NamedTemporaryFile(dir=SUDOERS.parent, prefix=".onecat-check-") as check:
            check.write(rules.encode())
            check.flush()
            subprocess.run(
                ["/usr/sbin/visudo", "-cf", check.name], check=True, capture_output=True, timeout=15
            )
        source = SOURCE.read_bytes()
        if not source.startswith(b"#!/usr/bin/python3 -I\n"):
            raise ValueError("Bundled GPU helper is invalid")
        changes = [
            (HELPER, source, 0o755),
            (CONFIG, json.dumps(config, indent=2).encode(), 0o644),
            (SUDOERS, rules.encode(), 0o440),
        ]
        previous = []
        for path, _, _ in changes:
            if path.is_symlink():
                raise ValueError("Installed files must not be symlinks")
            previous.append(
                (path, path.read_bytes(), path.stat().st_mode & 0o777)
                if path.exists()
                else (path, None, None)
            )
        try:
            for path, data, mode in changes:
                replace_file(path, data, mode)
        except BaseException:
            for path, data, mode in reversed(previous):
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    replace_file(path, data, mode)
            raise
    binding = users[user]
    return {
        "state": "installed",
        "allowed_uuids": resolved_uuids(binding, devices),
        "binding": binding,
        "detected_gpu_count": len(devices),
        "clock_policy_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description="Configure Studio's bundled GPU controls")
    parser.add_argument(
        "--auto", action="store_true", help="Ask through sudo when attached to a terminal"
    )
    parser.add_argument(
        "--authorize", action="store_true", help="Allow the system desktop authorization dialog"
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "class", "all", "uuids"),
        default="auto",
        help="Bind a compute class (default for pre-install), every GPU, or exact UUIDs",
    )
    parser.add_argument(
        "--rebind",
        action="store_true",
        help="Replace this user's existing binding, including an explicit UUID pin",
    )
    parser.add_argument("--user")
    parser.add_argument("uuids", nargs="*")
    args = parser.parse_args()
    user = (
        args.user
        or os.environ.get("SUDO_USER")
        or pwd.getpwuid(int(os.environ.get("PKEXEC_UID", os.getuid()))).pw_name
    )
    options = ["--mode", args.mode]
    if args.rebind:
        options.append("--rebind")
    if os.geteuid() == 0:
        print(json.dumps(configure(user, args.uuids, args.mode, args.rebind)))
        return 0
    if not Path(SMI).is_file():
        print(json.dumps({"state": "no_gpus", "allowed_uuids": [], "binding": None}))
        return 0
    command = [
        "/usr/bin/python3",
        "-I",
        str(Path(__file__).resolve()),
        "--user",
        user,
        *options,
        *args.uuids,
    ]
    sudo = shutil.which("sudo")
    if (
        sudo
        and HELPER.is_file()
        and not HELPER.is_symlink()
        and HELPER.stat().st_uid == 0
        and not HELPER.stat().st_mode & 0o022
        and HELPER.read_bytes() == SOURCE.read_bytes()
    ):
        result = subprocess.run(
            [sudo, "-n", str(HELPER), "check"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            checked = json.loads(result.stdout)
            print(
                json.dumps(
                    {
                        "state": "installed",
                        "allowed_uuids": checked.get("allowed_uuids", []),
                        "binding": checked.get("binding"),
                        "clock_policy_changed": False,
                    }
                )
            )
            return 0
    authorized = (
        sudo
        and subprocess.run([sudo, "-n", "true"], capture_output=True, timeout=10).returncode == 0
    )
    if authorized:
        command = [sudo, "-n", *command]
    elif sys.stdin.isatty():
        command = [sudo, *command] if sudo else []
    elif (
        args.authorize
        and shutil.which("pkexec")
        and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    ):
        command = [shutil.which("pkexec"), "--disable-internal-agent", *command]
    else:
        command = []
    if not command:
        print(
            json.dumps(
                {
                    "state": "needs_authorization",
                    "message": "GPU controls are bundled. Complete system authorization in Studio Settings.",
                }
            )
        )
        return 3
    return subprocess.run(command, check=False, timeout=180).returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
