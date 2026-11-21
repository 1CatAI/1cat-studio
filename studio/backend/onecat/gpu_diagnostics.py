# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Read-only checks used by the GPU control wizard when binding is unavailable."""

import os
import re
import subprocess
import time
from functools import lru_cache
from pathlib import Path


PCI_ROOT = Path("/sys/bus/pci/devices")
# GV100 cards, including the V100 variants, TITAN V, and Quadro GV100.
GV100_IDS = frozenset({
    "1d81", "1db1", "1db3", "1db4", "1db5", "1db6", "1db7", "1db8", "1dba",
    "1df0", "1df2", "1df5", "1df6",
})
GPU_LINE = re.compile(r"^GPU \d+:[^\n]*\(UUID: (GPU-[a-f0-9-]+)\)\s*$", re.I | re.M)
PCI_LINE = re.compile(r"^[0-9a-f:.]+\s+([0-9a-f]{4}):\s+10de:([0-9a-f]{4})\b", re.I)
PCI_DRIVER = re.compile(r"^[ \t]*Kernel driver in use: (\S+)", re.M)


def _run(*argv):
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, errors="replace", timeout=2,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _pci_devices():
    result = _run("lspci", "-D", "-n", "-k", "-d", "10de:")
    devices = []
    if result is not None and result.returncode == 0 and not result.stderr.strip():
        for entry in re.split(r"(?m)^(?=\S)", result.stdout):
            if not entry.strip():
                continue
            match = PCI_LINE.match(entry)
            if not match:
                break
            device_class, device_id = match.groups()
            if device_class.startswith("03") or device_class == "1200":
                driver = PCI_DRIVER.search(entry)
                devices.append((device_id.lower(), driver[1] if driver else None))
        else:
            return devices
        devices.clear()

    # Minimal server images may lack pciutils. sysfs can provide the same evidence.
    try:
        paths = list(PCI_ROOT.iterdir())
        if not paths:
            return None
        for path in paths:
            if (path / "vendor").read_text().strip() != "0x10de":
                continue
            device_class = (path / "class").read_text().strip().removeprefix("0x")[:4]
            if not (device_class.startswith("03") or device_class == "1200"):
                continue
            device_id = (path / "device").read_text().strip().removeprefix("0x").lower()
            try:
                driver = (path / "driver").readlink().name
            except FileNotFoundError:
                driver = None
            devices.append((device_id, driver))
        return devices
    except OSError:
        return None


def _unavailable_reason(smi, devices, drivers):
    if devices == []:
        return "pci_not_visible"
    if devices and not drivers:
        return "driver_unbound"
    if drivers and "nvidia" not in drivers:
        return "other_driver"
    if smi is None:
        return "probe_unavailable"

    error = (smi.stdout + "\n" + smi.stderr).lower()
    if smi.returncode == 4 or "insufficient permissions" in error or "permission denied" in error:
        return "access_denied"
    if smi.returncode == 9:
        return "driver_not_loaded"
    if smi.returncode == 12 or "driver/library version mismatch" in error:
        return "nvml_error"
    if smi.returncode == 15 or "fallen off the bus" in error:
        return "gpu_inaccessible"
    return "driver_error" if devices else "probe_unavailable"


def diagnose():
    # The GPU controls page polls every two seconds. Reuse short-lived probe results.
    return _diagnose(int(time.monotonic()) // 6)


@lru_cache(maxsize=1)
def _diagnose(_time_window):
    smi = _run("nvidia-smi", "-L")
    count = len(set(GPU_LINE.findall(smi.stdout))) if smi else 0
    if count:
        return {"reason": "visible", "smi_gpu_count": count}

    devices = _pci_devices()
    drivers = sorted({driver for _, driver in devices or [] if driver})
    return {
        "reason": _unavailable_reason(smi, devices, drivers),
        "smi_gpu_count": 0,
        "pci_gpu_count": None if devices is None else len(devices),
        "gv100_count": sum(device in GV100_IDS for device, _ in devices or []),
        "pci_drivers": drivers,
    }
