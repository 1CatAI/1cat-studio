# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Machine activation: bind delivered presets to the GPUs this machine actually has.

A bundle can be produced on one machine and installed on another, or a card can be
replaced. Presets carry the GPU UUIDs they were created with, so a delivered state
would otherwise reference hardware that is not present. Activation resolves the
authorized GPUs of the running host and re-binds every preset that no longer
matches, keeping the preset's parallel width when the host can satisfy it.
"""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

HELPER = Path("/usr/local/libexec/onecat-gpu-helper")
SMI = "/usr/bin/nvidia-smi"
V100 = [7, 0]


def detected_gpus() -> list[dict]:
    """Every GPU the driver reports, in PCI order, with the facts a binding needs."""
    fields = "uuid,compute_cap,name"
    result = subprocess.run(
        [SMI, "--query-gpu=" + fields, "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise ValueError("NVIDIA driver is unavailable: " + result.stderr.strip()[:200])
    devices = []
    for values in csv.reader(result.stdout.splitlines()):
        values = [value.strip() for value in values]
        if len(values) < 3 or not values[0].startswith("GPU-"):
            continue
        try:
            capability = [int(part) for part in values[1].split(".")]
        except ValueError:
            capability = None
        devices.append({"uuid": values[0], "compute_capability": capability, "name": values[2]})
    return devices


def authorized_uuids() -> tuple[list[str] | None, dict | None]:
    """The GPUs this user may control, or None when system authorization is pending."""
    try:
        result = subprocess.run(
            ["sudo", "-n", str(HELPER), "check"], capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    if result.returncode:
        return None, None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return None, None
    if not data.get("available"):
        return None, None
    return list(data.get("allowed_uuids") or []), data.get("binding")


def choose(pool: list[dict], profile: dict, wanted: int) -> tuple[list[dict], list[str]]:
    """Pick `wanted` GPUs, preferring the class the preset was validated for."""
    notes = []
    preference = V100 if str(profile.get("dtype", "")).lower() in {"half", "float16"} else None
    preferred = [d for d in pool if preference and d.get("compute_capability") == preference]
    chosen = (preferred or pool)[:wanted]
    if len(chosen) < wanted:
        notes.append(
            "host has %d usable GPU(s) but the preset asks for %d" % (len(chosen), wanted)
        )
    return chosen, notes


def rebind_profiles(*, apply: bool = True) -> dict:
    """Re-bind presets whose GPUs are gone. Idempotent; returns a report."""
    from . import db

    devices = detected_gpus()
    present = {d["uuid"]: d for d in devices}
    allowed, binding = authorized_uuids()
    if allowed is None:
        pool = list(devices)
        authorized = False
    else:
        pool = [present[uuid] for uuid in allowed if uuid in present]
        authorized = True

    report = {
        "detected_gpu_count": len(devices),
        "authorized": authorized,
        "binding": binding,
        "usable_uuids": [d["uuid"] for d in pool],
        "profiles": [],
        "warnings": [],
    }
    if not pool:
        report["warnings"].append(
            "No usable GPU is authorized yet; finish GPU authorization in Studio Settings"
        )

    kept = 0
    rebound = 0
    for profile in db.all_records("profiles"):
        pinned = list(profile.get("gpu_uuids") or [])
        missing = [uuid for uuid in pinned if uuid not in present]
        entry = {"id": profile["id"], "name": profile.get("name")}
        if pinned and not missing:
            kept += 1
            entry["action"] = "kept"
            report["profiles"].append(entry)
            continue
        if not pool:
            entry["action"] = "blocked"
            report["profiles"].append(entry)
            continue
        wanted = int(profile.get("tensor_parallel_size") or 0) or len(pool)
        wanted = max(1, wanted)
        chosen, notes = choose(pool, profile, wanted)
        entry.update(
            {
                "action": "rebound",
                "missing": len(missing),
                "from": pinned,
                "to": [d["uuid"] for d in chosen],
                "tensor_parallel_size": {"from": profile.get("tensor_parallel_size"), "to": len(chosen)},
            }
        )
        if notes:
            entry["notes"] = notes
            report["warnings"].extend("%s: %s" % (profile.get("name"), note) for note in notes)
        rebound += 1
        if apply:
            profile["gpu_uuids"] = [d["uuid"] for d in chosen]
            profile["tensor_parallel_size"] = len(chosen)
            db.put("profiles", profile["id"], profile)
        report["profiles"].append(entry)

    stale = [
        record["id"]
        for record in db.all_records("gpu_policies")
        if record["id"] not in present
    ]
    if apply:
        for uuid in stale:
            db.delete("gpu_policies", uuid)
    report["stale_policies_removed"] = len(stale)
    report["kept"] = kept
    report["rebound"] = rebound
    report["applied"] = apply
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Bind Studio presets to this machine's GPUs")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()
    report = rebind_profiles(apply=not args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if report["warnings"]:
        for warning in report["warnings"]:
            print("warning: " + warning, file=__import__("sys").stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def validated_runtimes() -> list[dict]:
    from . import db

    return [record for record in db.all_records("runtimes") if record.get("validated")]


def _runtime_version(record: dict):
    from packaging.version import InvalidVersion, Version

    capabilities = record.get("capabilities") or {}
    text = str(
        (record.get("release") or {}).get("version")
        or capabilities.get("distribution_version")
        or capabilities.get("vllm_version")
        or "0"
    )
    try:
        return Version(text.split("+")[0])
    except InvalidVersion:
        return Version("0")


def best_runtime(family: str) -> dict | None:
    """The runtime a preset should use now: same family first, then newest install."""
    from .catalog import runtime_family

    available = validated_runtimes()
    if not available:
        return None
    same = [record for record in available if family and runtime_family(record) == family]
    pool = same or available
    pool.sort(
        key=lambda record: (
            bool(record.get("managed")),
            _runtime_version(record),
            record.get("validated_at") or 0,
        ),
        reverse=True,
    )
    return pool[0]


def ensure_runtime(profile: dict, *, apply: bool = True) -> dict | None:
    """Move a preset onto a validated runtime when its own one is gone or unvalidated."""
    from . import db
    from .catalog import runtime_family

    current = db.get("runtimes", profile.get("runtime_id") or "")
    if current and current.get("validated"):
        return None
    chosen = best_runtime(runtime_family(current or profile.get("runtime_id")))
    if not chosen:
        return None
    if apply:
        profile["runtime_id"] = chosen["id"]
        db.put("profiles", profile["id"], profile)
    return chosen


def rebind_runtimes(*, apply: bool = True) -> dict:
    """Every preset follows an installed runtime; the runtime stays a replaceable part."""
    from . import db
    from .catalog import runtime_family

    report = {
        "installed": [
            {"id": record["id"], "name": record.get("name"), "family": runtime_family(record)}
            for record in validated_runtimes()
        ],
        "profiles": [],
        "rebound": 0,
        "kept": 0,
        "warnings": [],
        "applied": apply,
    }
    for profile in db.all_records("profiles"):
        current = db.get("runtimes", profile.get("runtime_id") or "")
        if current and current.get("validated"):
            report["kept"] += 1
            continue
        chosen = best_runtime(runtime_family(current or profile.get("runtime_id")))
        entry = {
            "id": profile["id"],
            "name": profile.get("name"),
            "from": profile.get("runtime_id"),
        }
        if not chosen:
            entry["action"] = "blocked"
            report["warnings"].append(
                "%s: no validated runtime is installed" % profile.get("name")
            )
            report["profiles"].append(entry)
            continue
        entry.update({"action": "rebound", "to": chosen["id"]})
        report["rebound"] += 1
        if apply:
            profile["runtime_id"] = chosen["id"]
            db.put("profiles", profile["id"], profile)
        report["profiles"].append(entry)
    return report
