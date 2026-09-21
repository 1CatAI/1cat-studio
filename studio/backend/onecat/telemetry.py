# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""One sampler for live charts and request-window board power, independent of browsers."""

from __future__ import annotations

import asyncio
import time
from collections import deque

from . import gpu

INTERVAL = 0.5
samples: deque[dict] = deque(maxlen=1200)


async def monitor():
    samples.clear()
    while True:
        started = time.monotonic()
        try:
            sample = await asyncio.to_thread(gpu.snapshot)
        except Exception as error:
            # A transient driver error must not permanently stop live sampling.
            sample = {
                "available": False,
                "gpus": [],
                "timestamp": time.time(),
                "error": str(error),
            }
        sample["monotonic"] = time.monotonic()
        samples.append(sample)
        await asyncio.sleep(max(0, INTERVAL - (time.monotonic() - started)))


def live(model_uuids: list[str], since: float = 0) -> dict:
    """Monitor every physical GPU; model assignment is annotation, not a filter."""
    now = time.time()
    # The sampler can append from another thread while the API builds a response.
    snapshots = list(samples)
    current = snapshots[-1] if snapshots else {"gpus": [], "available": False}
    history = []
    for sample in snapshots:
        if sample["timestamp"] <= max(since, now - 120):
            continue
        devices = sample["gpus"]
        measured = [d for d in devices if d.get("power_w") is not None]
        history.append(
            {
                "timestamp": sample["timestamp"],
                "power_w": sum(d["power_w"] for d in measured)
                if devices and len(measured) == len(devices)
                else None,
                # Unsupported sensors (e.g. P400) must not imply a zero-watt GPU.
                "measured_power_w": sum(d["power_w"] for d in measured) if measured else None,
                "power_reporting_gpu_count": len(measured),
                "gpu_count": len(devices),
                "memory_used_mib": sum(d["memory_used_mib"] for d in devices)
                if devices and all(d.get("memory_used_mib") is not None for d in devices)
                else None,
            }
        )
    return {
        **{k: v for k, v in current.items() if k != "monotonic"},
        "stale": now - current.get("timestamp", 0) > 3,
        "interval_ms": int(INTERVAL * 1000),
        "gpu_uuids": [d["uuid"] for d in current["gpus"]],
        "model_gpu_uuids": model_uuids,
        "monitoring_scope": "all_gpus",
        "history": history,
    }


def request_energy(uuids: list[str], start: float, end: float) -> dict:
    """Integrate only a fully sampled request window; never allocate energy by token share."""
    if not uuids or end <= start:
        return {}
    points = []
    for sample in list(samples):
        devices = {d["uuid"]: d for d in sample["gpus"]}
        selected = [devices.get(uuid) for uuid in uuids]
        watts = (
            sum(d["power_w"] for d in selected)
            if all(d and d["power_w"] is not None for d in selected)
            else None
        )
        points.append((sample["monotonic"], watts))
    total, coverage = 0.0, 0.0
    for (a, wa), (b, wb) in zip(points, points[1:]):
        low, high = max(a, start), min(b, end)
        if high <= low:
            continue
        if wa is None or wb is None or b - a > INTERVAL * 3:
            return {}
        left = wa + (wb - wa) * (low - a) / (b - a)
        right = wa + (wb - wa) * (high - a) / (b - a)
        total += (left + right) * (high - low) / 2
        coverage += high - low
    if abs(coverage - (end - start)) > 0.001:
        return {}
    return {
        "average_gpu_w": total / (end - start),
        "energy_wh": total / 3600,
        "energy_method": "sampled_gpu_boards",
        "energy_scope": "request_window",
    }
