# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Resolve GPU UUIDs in cloned Studio profiles against compatible local boards."""

from __future__ import annotations

import argparse
import json
import time

from . import db, gpu, gpu_setup


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


def remember_devices(devices):
    """Keep the card facts on the source disk, including across delayed driver startup."""
    for d in devices:
        uuid = d.get("uuid")
        if not uuid or not d.get("compute_capability") or not d.get("memory_total_mib"):
            continue
        facts = {k: d.get(k) for k in ("compute_capability", "index", "memory_total_mib")}
        if db.get("gpu_binding_history", uuid) != facts:
            db.put("gpu_binding_history", uuid, facts)


def replacement(profile, devices, pool):
    """Return a complete ordered binding, or leave the original available for retry."""
    pinned = profile.get("gpu_uuids") or []
    width = profile.get("tensor_parallel_size")
    present = {d["uuid"] for d in devices if d.get("uuid")}
    if pinned and all(u in present for u in pinned):
        return None
    facts = [db.get("gpu_binding_history", u) for u in pinned]
    if not any(facts):
        # Older V100 images did not record card facts. Keep their established fallback.
        if width == 4 and (profile.get("portable_gpu_binding") or
                           (profile.get("source") == "catalog-default" and
                            profile.get("attention_backend") == "FLASH_ATTN_V100")):
            return _uuids(pool) or None
        return None
    if len(pinned) != width or len(set(pinned)) != width:
        return None
    available = {d["uuid"]: d for d in pool if d.get("uuid")}
    if any(u in present and u not in available for u in pinned):
        return None
    missing = [i for i, u in enumerate(pinned) if u not in present]
    if any(not facts[i] for i in missing):
        return None
    result = pinned.copy()
    for u in pinned:
        available.pop(u, None)
    # Reserve larger cards for the ranks that need them first.
    for i in sorted(missing, key=lambda i: facts[i]["memory_total_mib"], reverse=True):
        old = facts[i]
        candidates = [d for d in available.values()
                      if d.get("compute_capability") == old["compute_capability"]
                      and round((d.get("memory_total_mib") or 0) / 1024)
                      >= round(old["memory_total_mib"] / 1024)]
        if not candidates:
            return None
        best = min(candidates, key=lambda d: (d.get("index") != old.get("index"),
                                              d["memory_total_mib"], d.get("index", 0)))
        result[i] = best["uuid"]
        del available[result[i]]
    return result


def apply_binding(profile, uuids):
    old = profile.get("gpu_uuids", [])
    if profile.get("id"):
        with db.connect() as conn:
            conn.execute(
                "UPDATE records SET data=json_set(data,'$.gpu_uuids',json(?)), updated=? "
                "WHERE bucket='profiles' AND id=? AND coalesce(json_extract(data,'$.gpu_uuids'),'[]')=json(?) "
                "AND coalesce(json_extract(data,'$.tensor_parallel_size'),1)=?",
                (json.dumps(uuids), time.time(), profile["id"], json.dumps(old),
                 profile.get("tensor_parallel_size", 1)),
            )
    profile["gpu_uuids"] = uuids


def _pool(devices):
    from .activation import authorized_uuids

    allowed, _ = authorized_uuids()
    if allowed is None:
        return [] if gpu_setup.stored_binding() is not None else devices
    return [d for d in devices if d.get("uuid") in allowed]


def rebind_profile(profile):
    pinned = profile.get("gpu_uuids") or []
    devices = gpu.snapshot()["gpus"]
    if not devices:
        return False
    if pinned and set(pinned).issubset(d.get("uuid") for d in devices):
        remember_devices([d for d in devices if d.get("uuid") in pinned])
        return False
    uuids = replacement(profile, devices, _pool(devices))
    if not uuids:
        return False
    apply_binding(profile, uuids)
    return True


def rebind(*, wait_seconds: float = 0) -> list[str]:
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        devices = gpu.snapshot()["gpus"]
        uuids = _uuids(devices)
        remaining = deadline - time.monotonic()
        if uuids or remaining <= 0:
            break
        time.sleep(min(1, remaining))
    if devices:
        remember_devices(devices)
        present = {d.get("uuid") for d in devices}
        pool = None
        for profile in db.all_records("profiles"):
            if profile.get("gpu_uuids") and set(profile["gpu_uuids"]) <= present:
                continue
            if pool is None:
                pool = _pool(devices)
            chosen = replacement(profile, devices, pool)
            if chosen:
                apply_binding(profile, chosen)
    return uuids


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-seconds", type=float, default=0)
    args = parser.parse_args()
    rebind(wait_seconds=args.wait_seconds)
    print("GPU profile binding check completed.")
