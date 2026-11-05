# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Rebind installed V100 profiles after a system-disk clone changes GPU UUIDs."""

from __future__ import annotations

import argparse
import time

from . import db, gpu


def _uuids(devices: list[dict]) -> list[str]:
    candidates = [
        item
        for item in devices
        if item.get("compute_capability") == [7, 0] and (item.get("memory_total_mib") or 0) >= 15360
    ]
    if len(candidates) != 4:
        # A different GPU topology needs an operator-selected profile; leave it
        # visible in Studio rather than binding a display GPU or a smaller card.
        return []
    uuids = [item.get("uuid") for item in candidates]
    if any(not isinstance(uuid, str) or not uuid.startswith("GPU-") for uuid in uuids):
        return []
    return uuids if len(set(uuids)) == 4 else []


def _bind(profile: dict, uuids: list[str]) -> bool:
    if not profile.get("portable_gpu_binding") or profile.get("tensor_parallel_size") != 4:
        return False
    pinned = list(profile.get("gpu_uuids") or [])
    # Keep an operator's ordering when all selected boards are still present.
    if pinned and set(pinned).issubset(uuids):
        return False
    profile["gpu_uuids"] = uuids
    stored = db.get("profiles", profile.get("id")) if profile.get("id") else None
    if (
        stored
        and stored.get("portable_gpu_binding")
        and stored.get("tensor_parallel_size") == 4
        and stored.get("gpu_uuids", []) == pinned
    ):
        db.patch("profiles", profile["id"], {"gpu_uuids": uuids})
    return True


def rebind_profile(profile: dict) -> bool:
    """Retry a portable binding at validation if boot preceded driver readiness."""
    if not profile.get("portable_gpu_binding") or profile.get("tensor_parallel_size") != 4:
        return False
    uuids = _uuids(gpu.snapshot()["gpus"])
    return _bind(profile, uuids) if uuids else False


def rebind(*, wait_seconds: float = 0) -> list[str]:
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        uuids = _uuids(gpu.snapshot()["gpus"])
        if uuids:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return []
        time.sleep(min(1, remaining))
    for profile in db.all_records("profiles"):
        _bind(profile, uuids)
    return uuids


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-seconds", type=float, default=0)
    args = parser.parse_args()
    uuids = rebind(wait_seconds=args.wait_seconds)
    print("Portable GPU binding:", len(uuids), "V100 devices")
    if not uuids:
        print(
            "No compatible four-GPU topology ready; portable profiles unchanged. "
            "Binding will be retried when a model is validated."
        )
