# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import subprocess
import sys
from types import ModuleType

import pytest

from onecat import gpu, gpu_diagnostics as diagnostics


GPU = "GPU-11111111-1111-1111-1111-111111111111"
V100 = "0000:18:00.0 0302: 10de:1db5 (rev a1)\n"


def result(code, stdout="", stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


@pytest.fixture(autouse=True)
def clear_cache():
    diagnostics._diagnose.cache_clear()
    yield
    diagnostics._diagnose.cache_clear()


def test_smi_visible_skips_pci_and_reuses_probe(monkeypatch):
    calls = []

    def run(*argv):
        calls.append(argv)
        return result(0, f"GPU 0: Tesla V100-SXM2-32GB (UUID: {GPU})\n")

    monkeypatch.setattr(diagnostics, "_run", run)
    monkeypatch.setattr(diagnostics.time, "monotonic", lambda: 18)
    assert diagnostics.diagnose() == {"reason": "visible", "smi_gpu_count": 1}
    assert diagnostics.diagnose()["smi_gpu_count"] == 1
    assert calls == [("nvidia-smi", "-L")]


@pytest.mark.parametrize(
    "smi,pci,expected,gv100,drivers",
    [
        (result(9), V100 + "\tKernel modules: nvidia\n", "driver_unbound", 1, []),
        (result(6), V100 + "\tKernel driver in use: vfio-pci\n", "other_driver", 1, ["vfio-pci"]),
        (result(6), V100 + "\tKernel driver in use: nouveau\n", "other_driver", 1, ["nouveau"]),
        (result(9), V100 + "\tKernel driver in use: nvidia\n", "driver_not_loaded", 1, ["nvidia"]),
        (result(12), V100 + "\tKernel driver in use: nvidia\n", "nvml_error", 1, ["nvidia"]),
        (result(255, stderr="Failed to initialize NVML: Driver/library version mismatch"),
         V100 + "\tKernel driver in use: nvidia\n", "nvml_error", 1, ["nvidia"]),
        (result(4), V100 + "\tKernel driver in use: nvidia\n", "access_denied", 1, ["nvidia"]),
        (result(15), V100 + "\tKernel driver in use: nvidia\n", "gpu_inaccessible", 1, ["nvidia"]),
        (result(6), V100 + "\tKernel driver in use: nvidia\n", "driver_error", 1, ["nvidia"]),
        (result(6), "0000:01:00.1 0403: 10de:10f0 (rev a1)\n", "pci_not_visible", 0, []),
        (result(6), "0000:01:00.0 0300: 10de:2204 (rev a1)\n", "driver_unbound", 0, []),
    ],
)
def test_pci_fallback_distinguishes_device_presence_from_driver_binding(
    monkeypatch, smi, pci, expected, gv100, drivers
):
    monkeypatch.setattr(diagnostics, "_run", lambda command, *args:
                        smi if command == "nvidia-smi" else result(0, pci))
    value = diagnostics.diagnose()
    assert value["reason"] == expected
    assert value["gv100_count"] == gv100
    assert value["pci_drivers"] == drivers
    assert value["pci_gpu_count"] == (0 if expected == "pci_not_visible" else 1)


def test_unavailable_lspci_uses_sysfs_without_assuming_absent_hardware(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "_run", lambda *args: None)
    pci_root = tmp_path / "pci"
    pci_root.mkdir()
    monkeypatch.setattr(diagnostics, "PCI_ROOT", pci_root)
    assert diagnostics.diagnose()["reason"] == "probe_unavailable"
    assert diagnostics.diagnose()["pci_gpu_count"] is None

    board = pci_root / "0000:18:00.0"
    board.mkdir()
    for key, value in {"vendor": "0x10de", "class": "0x030200", "device": "0x1db5"}.items():
        (board / key).write_text(value)
    diagnostics._diagnose.cache_clear()
    assert diagnostics.diagnose()["reason"] == "driver_unbound"
    assert diagnostics.diagnose()["gv100_count"] == 1


def test_nvml_lost_handle_does_not_hide_other_gpus(monkeypatch):
    class GpuLost(Exception):
        pass

    def get_handle(index):
        if index == 0:
            raise GpuLost()
        return index

    nvml = ModuleType("pynvml")
    nvml.NVMLError = GpuLost
    nvml.nvmlDeviceGetCount = lambda: 2
    nvml.nvmlDeviceGetHandleByIndex = get_handle
    nvml.nvmlDeviceGetUUID = lambda handle: GPU
    nvml.__getattr__ = lambda name: lambda *args: None
    monkeypatch.setitem(sys.modules, "pynvml", nvml)
    assert [board["uuid"] for board in gpu.snapshot()["gpus"]] == [GPU]
