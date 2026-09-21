# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import psutil

from .config import state_root


def snapshot() -> dict:
    """Board telemetry only. Unsupported counters remain null rather than invented zeroes."""
    try:
        import pynvml as nv

        nv.nvmlInit()
    except Exception as error:
        return {"available": False, "gpus": [], "error": str(error), "timestamp": time.time()}

    def read(fn, *args):
        try:
            value = fn(*args)
            return value.decode() if isinstance(value, bytes) else value
        except nv.NVMLError:
            return None

    gpus = []
    try:
        for index in range(nv.nvmlDeviceGetCount()):
            handle = nv.nvmlDeviceGetHandleByIndex(index)
            memory = read(nv.nvmlDeviceGetMemoryInfo, handle)
            util = read(nv.nvmlDeviceGetUtilizationRates, handle)
            constraints = read(nv.nvmlDeviceGetPowerManagementLimitConstraints, handle)
            energy = read(nv.nvmlDeviceGetTotalEnergyConsumption, handle)
            power = read(nv.nvmlDeviceGetPowerUsage, handle)
            limit = read(nv.nvmlDeviceGetEnforcedPowerLimit, handle)
            defaults = read(nv.nvmlDeviceGetPowerManagementDefaultLimit, handle)
            mem_clock = read(nv.nvmlDeviceGetClockInfo, handle, nv.NVML_CLOCK_MEM)
            clocks = (
                read(nv.nvmlDeviceGetSupportedGraphicsClocks, handle, mem_clock)
                if mem_clock
                else []
            )
            processes = []
            for proc in read(nv.nvmlDeviceGetComputeRunningProcesses, handle) or []:
                try:
                    name = psutil.Process(proc.pid).name()
                except psutil.Error:
                    name = "unknown"
                processes.append({"pid": proc.pid, "name": name})
            capability = read(nv.nvmlDeviceGetCudaComputeCapability, handle)
            gpus.append(
                {
                    "index": index,
                    "uuid": read(nv.nvmlDeviceGetUUID, handle),
                    "name": read(nv.nvmlDeviceGetName, handle),
                    "compute_capability": list(capability) if capability else None,
                    "memory_total_mib": round(memory.total / 1048576) if memory else None,
                    "memory_used_mib": round(memory.used / 1048576) if memory else None,
                    "utilization": util.gpu if util else None,
                    "temperature_c": read(
                        nv.nvmlDeviceGetTemperature, handle, nv.NVML_TEMPERATURE_GPU
                    ),
                    "power_w": power / 1000 if power is not None else None,
                    "power_limit_w": limit / 1000 if limit is not None else None,
                    "default_power_limit_w": defaults / 1000 if defaults is not None else None,
                    "power_min_w": constraints[0] / 1000 if constraints else None,
                    "power_max_w": constraints[1] / 1000 if constraints else None,
                    "graphics_clock_mhz": read(
                        nv.nvmlDeviceGetClockInfo, handle, nv.NVML_CLOCK_GRAPHICS
                    ),
                    "memory_clock_mhz": mem_clock,
                    "supported_graphics_clocks_mhz": sorted(clocks or []),
                    "energy_j": energy / 1000 if energy is not None else None,
                    "processes": processes,
                }
            )
        return {
            "available": True,
            "timestamp": time.time(),
            "gpus": gpus,
            "driver": read(nv.nvmlSystemGetDriverVersion),
            "scope": "gpu_boards",
        }
    finally:
        nv.nvmlShutdown()


def helper_path() -> Path:
    return Path(os.environ.get("ONECAT_GPU_HELPER", "/usr/local/libexec/onecat-gpu-helper"))


def control_available() -> bool:
    helper = helper_path()
    if not helper.is_file():
        return False
    try:
        result = subprocess.run(
            ["sudo", "-n", str(helper), "check"], capture_output=True, timeout=10
        )
        if result.returncode:
            return False
        return bool(json.loads(result.stdout).get("available"))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def selected_devices(uuids: list[str]) -> list[dict]:
    devices = {d["uuid"]: d for d in snapshot()["gpus"]}
    if not uuids or any(id not in devices for id in uuids):
        raise ValueError(
            "选中的显卡已不在本机，请在设置中重新绑定 / "
            "Selected GPUs are not present on this machine; re-bind GPU control in Settings"
        )
    return [devices[id] for id in uuids]


def validate_setting(uuids: list[str], setting: dict, *, devices=None):
    selected = (
        selected_devices(uuids) if devices is None else [d for d in devices if d["uuid"] in uuids]
    )
    if {d["uuid"] for d in selected} != set(uuids):
        raise ValueError(
            "选中的显卡已不在本机，请在设置中重新绑定 / "
            "Selected GPUs are not present on this machine; re-bind GPU control in Settings"
        )
    for device in selected:
        watts = setting.get("power_limit_w")
        if watts is not None:
            low, high = device["power_min_w"], device["power_max_w"]
            if low is None or high is None or not low <= watts <= high:
                raise ValueError(f"{device['name']}: unsupported power limit {watts} W")
        clock = setting.get("graphics_clock_mhz")
        if clock is not None and clock not in device["supported_graphics_clocks_mhz"]:
            raise ValueError(f"{device['name']}: unsupported graphics clock {clock} MHz")


def apply(uuids: list[str], setting: dict):
    validate_setting(uuids, setting)
    result = subprocess.run(
        ["sudo", "-n", str(helper_path()), "apply"],
        input=json.dumps({"uuids": uuids, "setting": setting}),
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "GPU helper rejected the operation")
    # Record only successfully applied settings, for exact restoration of our own locks.
    from . import db

    for uuid in uuids:
        previous = db.get("hardware", uuid, {})
        if setting.get("reset_clocks"):
            previous["graphics_clock_mhz"] = None
        previous.update({k: v for k, v in setting.items() if v is not None})
        db.put("hardware", uuid, previous)


def check_ownership(uuids: list[str]):
    from .engine import owned_pids

    owned = owned_pids()
    for device in selected_devices(uuids):
        outsiders = [p["pid"] for p in device["processes"] if p["pid"] not in owned]
        if outsiders:
            raise RuntimeError(
                f"GPU {device['index']} is also used by other processes: {outsiders}"
            )


def capture_settings(uuids: list[str]) -> dict:
    check_ownership(uuids)
    result = subprocess.run(
        ["sudo", "-n", str(helper_path()), "snapshot"],
        input=json.dumps({"uuids": uuids, "details": True}),
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "GPU control helper is unavailable")
    policies = json.loads(result.stdout)
    if set(policies) != set(uuids) or any(
        not p.get("clock_policy_known") or p.get("recovery_pending") for p in policies.values()
    ):
        raise ValueError(
            "Clock policy is unknown. Select a GPU power mode first before running calibration"
        )
    return {
        uuid: {k: setting[k] for k in ("power_limit_w", "graphics_clock_mhz", "reset_clocks")}
        for uuid, setting in policies.items()
    }


def restore(settings: dict):
    errors = []
    for uuid, values in settings.items():
        try:
            apply([uuid], values)
        except Exception as error:
            errors.append(str(error))
    if errors:
        raise RuntimeError("Hardware restore failed: " + "; ".join(errors))


def recover_interrupted():
    """A persisted journal also covers worker SIGKILL, which cannot execute finally."""
    from . import db
    from .jobs import same_process

    for journal in (state_root() / "benchmarks").glob("*/restore-hardware.json"):
        job = db.get("jobs", journal.parent.name, {})
        if same_process(job.get("pid"), job.get("process_created")):
            continue
        settings = json.loads(journal.read_text())
        try:
            check_ownership(list(settings))
            restore(settings)
            journal.unlink()
            db.patch(
                "benchmarks",
                journal.parent.name,
                {
                    "state": "failed",
                    "hardware_restored": True,
                    "error": "Calibration worker exited",
                },
            )
            db.delete("engine", "hardware_recovery")
        except Exception as error:
            db.put(
                "engine", "hardware_recovery", {"run_id": journal.parent.name, "error": str(error)}
            )
