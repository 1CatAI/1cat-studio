#!/usr/bin/python3 -I
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Root-owned, policy-bound NVIDIA power/clock helper. No arbitrary commands.

The administrator policy binds a Studio user to GPUs rather than to the machine
the package happened to be installed on: a binding may name exact UUIDs, every
GPU present, or a compute class resolved against the driver at call time.
"""

from __future__ import annotations

import csv
import fcntl
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG = Path("/etc/onecat-studio/gpu-helper.json")
STATE = Path("/var/lib/onecat-studio")
SMI = "/usr/bin/nvidia-smi"
PROTOCOL = 3


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".gpu-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o644)
        os.replace(name, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


class ControlError(ValueError):
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


def rollback(previous, ledger, ledger_path, journal_path, current_boot):
    results = {}
    for uuid, old in reversed(list(previous.items())):
        try:
            values = {k: old[k] for k in ("power_limit_w", "graphics_clock_mhz", "reset_clocks")}
            set_device(uuid, values)
            actual = device(uuid)
            if abs(actual["power_limit_w"] - old["power_limit_w"]) > 0.6:
                raise ValueError("Restored power limit did not match readback")
            ledger[uuid] = {"graphics_clock_mhz": old["graphics_clock_mhz"]}
            results[uuid] = {"state": "restored", "restored_exact": old["clock_policy_known"]}
        except BaseException as error:
            ledger.pop(uuid, None)
            results[uuid] = {"state": "restore_failed", "error": str(error)}
    atomic_json(ledger_path, {"boot_id": current_boot, "devices": ledger})
    if all(r["state"] == "restored" for r in results.values()):
        journal_path.unlink(missing_ok=True)
    return results


def smi(*args):
    result = subprocess.run(
        [SMI, *args],
        capture_output=True,
        text=True,
        timeout=15,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def number_field(value, name):
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"The driver reports no usable {name} for this GPU") from None


def gpu_inventory():
    """uuid -> board facts, read live so a binding survives card replacement."""
    fields = "uuid,compute_cap,memory.total,power.min_limit"
    rows = smi("--query-gpu=" + fields, "--format=csv,noheader,nounits")
    inventory = {}
    for values in csv.reader(rows.splitlines()):
        values = [value.strip() for value in values]
        if len(values) < 4:
            continue
        try:
            capability = [int(part) for part in values[1].split(".")]
        except ValueError:
            capability = None
        try:
            memory = int(number_field(values[2], "memory size"))
        except ValueError:
            memory = None
        try:
            number_field(values[3], "minimum power limit")
            manageable = True
        except ValueError:
            # Display adapters and older boards report no power limit; binding
            # them would only turn every later readback into a failed operation.
            manageable = False
        inventory[values[0]] = {
            "compute_capability": capability,
            "memory_mib": memory,
            "manageable": manageable,
        }
    return inventory


def class_filter(policy):
    """A class binding as a predicate over one inventory entry."""
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


def resolve_policy(config, user, inventory):
    """Resolve one user's binding against the GPUs present right now."""
    policy = config.get("users", {}).get(user)
    if isinstance(policy, list):
        # Protocol 2 stored a bare UUID allowlist; read it as an explicit pin.
        policy = {"mode": "uuids", "uuids": policy}
    if not isinstance(policy, dict):
        return {}, [], []
    pinned = [u for u in policy.get("uuids") or [] if isinstance(u, str)]
    missing = sorted(uuid for uuid in pinned if uuid not in inventory)
    mode = policy.get("mode")
    if mode == "uuids":
        return policy, [uuid for uuid in pinned if uuid in inventory], missing
    if mode == "all":
        return (
            policy,
            sorted(u for u, facts in inventory.items() if facts["manageable"]),
            missing,
        )
    if mode != "class":
        return policy, [], missing
    matches = class_filter(policy)
    return policy, sorted(uuid for uuid, facts in inventory.items() if matches(facts)), missing


def binding_reason(policy, allowed, missing, inventory):
    if not policy:
        return "This user has no GPU binding policy; enable GPU control in Studio"
    if allowed:
        return ""
    if missing:
        return "The bound GPU UUIDs are absent from this host; re-bind GPU control in Studio"
    if inventory and not any(facts["manageable"] for facts in inventory.values()):
        return "No GPU on this host reports a controllable power limit"
    return "The GPU binding matches no GPU on this host; re-bind GPU control in Studio"


def device(uuid):
    fields = "uuid,power.min_limit,power.max_limit,power.default_limit,power.limit,clocks.mem"
    rows = list(
        csv.reader(
            smi("-i", uuid, "--query-gpu=" + fields, "--format=csv,noheader,nounits").splitlines()
        )
    )
    if not rows:
        raise ValueError("The driver reports no telemetry for GPU " + uuid)
    values = rows[0]
    return {
        "uuid": values[0].strip(),
        "minimum": number_field(values[1], "minimum power limit"),
        "maximum": number_field(values[2], "maximum power limit"),
        "default": number_field(values[3], "default power limit"),
        "power_limit_w": number_field(values[4], "power limit"),
        "memory_mhz": int(number_field(values[5], "memory clock")),
    }


def clocks(uuid, memory):
    rows = csv.reader(
        smi(
            "-i", uuid, "--query-supported-clocks=memory,graphics", "--format=csv,noheader,nounits"
        ).splitlines()
    )
    return {int(row[1]) for row in rows if int(row[0]) == memory}


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + " must be a finite number")
    return value


def set_device(uuid, setting):
    if setting.get("power_limit_w") is not None:
        smi("-i", uuid, "-pl", str(setting["power_limit_w"]))
    if setting.get("reset_clocks"):
        smi("-i", uuid, "-rgc")
    elif setting.get("graphics_clock_mhz") is not None:
        clock = int(setting["graphics_clock_mhz"])
        smi("-i", uuid, "-lgc", f"{clock},{clock}")


def main():
    if os.geteuid() != 0:
        raise ValueError("This helper must be installed and invoked through sudo")
    if len(sys.argv) != 2 or sys.argv[1] not in {"check", "snapshot", "apply"}:
        raise ValueError("Supported actions: check, snapshot, apply")
    if CONFIG.stat().st_uid != 0 or CONFIG.stat().st_mode & 0o022:
        raise ValueError("Helper configuration must be root-owned and not writable by other users")
    config = json.loads(CONFIG.read_text())
    user = os.environ.get("SUDO_USER")
    inventory = gpu_inventory()
    policy, allowed, missing = resolve_policy(config, user, inventory)
    reason = binding_reason(policy, allowed, missing, inventory)
    if sys.argv[1] == "check":
        # A status probe never fails: Studio has to be able to tell an
        # unbound machine from an unauthorized one and guide the operator.
        print(
            json.dumps(
                {
                    "available": bool(allowed),
                    "protocol": PROTOCOL,
                    "allowed_uuids": allowed,
                    "binding": policy,
                    "mode": policy.get("mode"),
                    "missing_uuids": missing,
                    "detected_gpu_count": len(inventory),
                    "reason": reason,
                }
            )
        )
        return
    if not allowed:
        raise ValueError(reason)
    data = sys.stdin.read(65537)
    if len(data) > 65536:
        raise ValueError("Request too large")
    request = json.loads(data)
    if not isinstance(request, dict) or request.keys() - {
        "uuids",
        "setting",
        "settings",
        "initialize_unknown",
        "recover",
        "details",
    }:
        raise ValueError("Unknown control request")
    for flag in ("initialize_unknown", "recover", "details"):
        if flag in request and not isinstance(request[flag], bool):
            raise ValueError(flag + " must be a boolean")
    uuids = request.get("uuids")
    if not isinstance(uuids, list) or not uuids or len(uuids) > 64 or len(set(uuids)) != len(uuids):
        raise ValueError("Provide a nonempty unique GPU UUID list")
    if any(
        not isinstance(u, str) or not re.fullmatch(r"GPU-[a-fA-F0-9-]{36}", u) or u not in allowed
        for u in uuids
    ):
        raise ValueError("A selected GPU is not allowed")
    STATE.mkdir(mode=0o755, exist_ok=True)
    if STATE.is_symlink() or STATE.stat().st_uid != 0 or STATE.stat().st_mode & 0o022:
        raise ValueError("GPU state directory must be root-owned and protected")
    with (STATE / "gpu-helper.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        ledger_path = STATE / "clock-policies.json"
        current_boot = boot_id()
        saved = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
        ledger = saved.get("devices", {}) if saved.get("boot_id") == current_boot else {}
        journal_path = STATE / "pending-control.json"
        journal = json.loads(journal_path.read_text()) if journal_path.exists() else {}
        pending = journal.get("previous", {}) if journal.get("boot_id") == current_boot else {}
        if request.get("recover"):
            if sys.argv[1] != "apply" or request.keys() - {"uuids", "recover"}:
                raise ValueError("Invalid recovery request")
            if set(pending) - set(uuids):
                raise ValueError("Recovery needs authorization for every affected GPU")
            results = (
                rollback(pending, ledger, ledger_path, journal_path, current_boot)
                if pending
                else {}
            )
            if any(r["state"] == "restore_failed" for r in results.values()):
                raise ControlError("GPU recovery is incomplete", {"ok": False, "devices": results})
            print(json.dumps({"ok": True, "devices": results}))
            return
        devices = {uuid: device(uuid) for uuid in uuids}
        previous = {
            uuid: {
                "power_limit_w": devices[uuid]["power_limit_w"],
                "graphics_clock_mhz": ledger.get(uuid, {}).get("graphics_clock_mhz"),
                "clock_policy_known": uuid in ledger,
                "boot_id": current_boot,
                "recovery_pending": uuid in pending,
                "reset_clocks": not bool(ledger.get(uuid, {}).get("graphics_clock_mhz")),
            }
            for uuid in uuids
        }
        if sys.argv[1] == "snapshot":
            if request.get("details"):
                print(json.dumps(previous))
            else:
                # Retain the original snapshot contract for older Studio bundles.
                print(
                    json.dumps(
                        {
                            u: {
                                "power_limit_w": p["power_limit_w"],
                                "graphics_clock_mhz": p["graphics_clock_mhz"],
                                "reset_clocks": p["reset_clocks"],
                                "clock_policy_known": p["clock_policy_known"]
                                and not p["recovery_pending"],
                            }
                            for u, p in previous.items()
                        }
                    )
                )
            return
        if pending:
            raise ValueError("Recover the interrupted GPU operation first")
        if "setting" in request and "settings" in request:
            raise ValueError("Choose a shared setting or per-GPU settings")
        settings = request.get("settings", {u: request.get("setting", {}) for u in uuids})
        if not isinstance(settings, dict) or set(settings) != set(uuids):
            raise ValueError("Settings must match the selected GPUs")
        for uuid, info in devices.items():
            setting = settings[uuid]
            if not isinstance(setting, dict) or setting.keys() - {
                "power_limit_w",
                "graphics_clock_mhz",
                "reset_clocks",
            }:
                raise ValueError("Unknown hardware setting")
            watts, clock = setting.get("power_limit_w"), setting.get("graphics_clock_mhz")
            if not any((watts is not None, clock is not None, setting.get("reset_clocks"))):
                raise ValueError("Choose a power or clock setting")
            if "reset_clocks" in setting and not isinstance(setting["reset_clocks"], bool):
                raise ValueError("reset_clocks must be a boolean")
            if setting.get("reset_clocks") and clock is not None:
                raise ValueError("Choose a clock lock or a reset, not both")
            if (
                watts is not None
                and not info["minimum"] <= number(watts, "Power") <= info["maximum"]
            ):
                raise ValueError("Power is outside the device's supported range")
            if clock is not None and (
                number(clock, "Clock") != int(clock)
                or int(clock) not in clocks(uuid, info["memory_mhz"])
            ):
                raise ValueError("Graphics clock is unsupported at the current memory clock")
            if (
                not previous[uuid]["clock_policy_known"]
                and not setting.get("reset_clocks")
                and not request.get("initialize_unknown")
            ):
                raise ValueError(
                    "Clock policy is unknown. Initialize dynamic clocks with Restore defaults first"
                )
        applied = {}
        results = {}
        try:
            for uuid in uuids:
                setting = settings[uuid]
                applied[uuid] = previous[uuid]
                atomic_json(journal_path, {"boot_id": current_boot, "previous": applied})
                if not previous[uuid]["clock_policy_known"] and not setting.get("reset_clocks"):
                    set_device(uuid, {"reset_clocks": True})
                set_device(uuid, setting)
                actual = device(uuid)
                if (
                    setting.get("power_limit_w") is not None
                    and abs(actual["power_limit_w"] - setting["power_limit_w"]) > 0.6
                ):
                    raise ValueError("Power limit readback did not match the requested setting")
                old = ledger.get(uuid, {})
                ledger[uuid] = {
                    "graphics_clock_mhz": None
                    if setting.get("reset_clocks")
                    else setting["graphics_clock_mhz"]
                    if setting.get("graphics_clock_mhz") is not None
                    else old.get("graphics_clock_mhz")
                }
                results[uuid] = {
                    "state": "applied",
                    "power_limit_w": actual["power_limit_w"],
                    **ledger[uuid],
                    "clock_policy_known": True,
                    "boot_id": current_boot,
                }
            atomic_json(ledger_path, {"boot_id": current_boot, "devices": ledger})
            journal_path.unlink(missing_ok=True)
        except BaseException as error:
            results = rollback(applied, ledger, ledger_path, journal_path, current_boot)
            results.update({u: {"state": "unchanged"} for u in uuids if u not in applied})
            raise ControlError(
                str(error), {"ok": False, "devices": results, "error": str(error)}
            ) from error
        print(
            json.dumps(
                {
                    "ok": True,
                    "applied": uuids,
                    "settings": settings,
                    "devices": results,
                    "previous": previous,
                }
            )
        )


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        if isinstance(error, ControlError):
            print(json.dumps(error.result))
        print(str(error), file=sys.stderr)
        sys.exit(1)
