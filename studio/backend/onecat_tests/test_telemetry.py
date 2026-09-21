# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import asyncio
import time
from collections import deque

import pytest
from fastapi.testclient import TestClient
from onecat import db, telemetry
from onecat.app import create_app


@pytest.fixture
def hardware(monkeypatch):
    devices = [
        {"uuid": f"GPU-{i}", "power_w": 50 + i, "memory_used_mib": 100 + i} for i in range(4)
    ]
    now = time.time()
    snapshots = deque(
        {"timestamp": now + offset, "gpus": devices, "available": True} for offset in (-130, -2, -1)
    )
    monkeypatch.setattr(telemetry, "samples", snapshots)
    return devices


def test_live_scope_and_history_are_independent_of_model_assignment(hardware):
    history = None
    for assigned in (["GPU-0"], ["GPU-1", "GPU-2"], [], ["absent"]):
        live = telemetry.live(assigned)
        assert live["gpus"] == hardware
        assert live["gpu_uuids"] == [d["uuid"] for d in hardware]
        assert live["model_gpu_uuids"] == assigned
        assert live["monitoring_scope"] == "all_gpus"
        assert not live["stale"]
        if history is not None:
            assert live["history"] == history
        history = live["history"]
        assert len(history) == 2
        assert history[-1]["power_w"] == 206
        assert history[-1]["measured_power_w"] == 206
        assert history[-1]["memory_used_mib"] == 406
    assert len(telemetry.live([], since=history[0]["timestamp"])["history"]) == 1


def test_missing_sensor_has_explicit_partial_power_without_hiding_display_gpu(hardware):
    hardware.append({"uuid": "display", "power_w": None, "memory_used_mib": 264})
    live = telemetry.live(["GPU-0"])
    assert len(live["gpus"]) == 5
    point = live["history"][-1]
    assert point["power_w"] is None
    assert point["measured_power_w"] == 206
    assert point["power_reporting_gpu_count"] == 4
    assert point["gpu_count"] == 5
    assert point["memory_used_mib"] == 670
    for device in hardware:
        device["power_w"] = None
    assert telemetry.live([])["history"][-1]["measured_power_w"] is None


@pytest.mark.parametrize(
    "model_state", ["ready", "loading", "stopping", "unavailable", "stopped", "failed"]
)
def test_live_endpoint_reports_hardware_even_without_a_running_model(hardware, model_state, state):
    db.put("engine", "active", {"state": model_state, "profile": {"gpu_uuids": ["GPU-0"]}})
    # No lifespan: use only the fixture samples, never the workstation's real GPUs.
    client = TestClient(create_app())
    try:
        assert client.post("/api/auth/setup", json={"password": "test-monitor-password"}).is_success
        response = client.get("/api/gpu/live")
        assert response.is_success
        live = response.json()
        assert len(live["gpus"]) == len(live["gpu_uuids"]) == 4
        assert live["history"][-1]["power_w"] == 206
        assert live["model_gpu_uuids"] == (
            ["GPU-0"] if model_state in {"ready", "loading", "stopping", "unavailable"} else []
        )
    finally:
        client.close()


def test_empty_and_stale_samples_do_not_invent_zero_power(hardware):
    for sample in telemetry.samples:
        sample["timestamp"] -= 10
    assert telemetry.live([])["stale"]
    telemetry.samples.append({"timestamp": time.time(), "gpus": [], "available": False})
    live = telemetry.live([])
    assert not live["available"]
    assert live["history"][-1]["power_w"] is None
    assert live["history"][-1]["measured_power_w"] is None
    telemetry.samples.clear()
    assert not telemetry.live([])["available"]
    assert telemetry.live([])["history"] == []


def test_sampler_recovers_after_transient_driver_error(monkeypatch):
    calls = 0

    def snapshot():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary driver failure")
        return {"timestamp": time.time(), "gpus": [], "available": True}

    monkeypatch.setattr(telemetry, "samples", deque())
    monkeypatch.setattr(telemetry.gpu, "snapshot", snapshot)
    monkeypatch.setattr(telemetry, "INTERVAL", 0.001)

    async def run():
        task = asyncio.create_task(telemetry.monitor())
        try:
            async with asyncio.timeout(2):
                while len(telemetry.samples) < 2:
                    await asyncio.sleep(0.001)
            assert telemetry.samples[0]["error"] == "temporary driver failure"
            assert telemetry.samples[-1]["available"]
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
