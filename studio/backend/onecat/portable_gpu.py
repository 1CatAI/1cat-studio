# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Rebind installed V100 profiles after a system-disk clone changes GPU UUIDs."""

from __future__ import annotations

from . import db, gpu


def rebind() -> list[str]:
    devices = gpu.snapshot()["gpus"]
    candidates = [
        item for item in devices
        if item["compute_capability"] == [7, 0]
        and (item["memory_total_mib"] or 0) >= 15360
    ]
    if len(candidates) != 4:
        # A different GPU topology needs an operator-selected profile; leave it
        # visible in Studio rather than binding a display GPU or a smaller card.
        return []
    uuids = [item["uuid"] for item in candidates]
    for profile in db.all_records("profiles"):
        if profile.get("portable_gpu_binding") and profile.get("gpu_uuids") != uuids:
            db.patch("profiles", profile["id"], {"gpu_uuids": uuids})
    return uuids


if __name__ == "__main__":
    print("Portable GPU binding:", len(rebind()), "V100 devices")
