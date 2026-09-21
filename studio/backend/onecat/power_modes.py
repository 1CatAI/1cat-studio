# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""V100 presets: target operating draw is distinct from the driver's power limit."""

from . import db, gpu

MODES = {
    "eco": {"power_limit_w": 150, "graphics_clock_mhz": 975, "reset_clocks": False},
    "balanced": {"power_limit_w": 185, "graphics_clock_mhz": None, "reset_clocks": True},
    "performance": {"power_limit_w": 300, "graphics_clock_mhz": None, "reset_clocks": True},
}


def options(uuids: list[str]) -> dict:
    missing = None
    try:
        devices = gpu.selected_devices(uuids) if uuids else []
    except ValueError:
        devices = []
        missing = "模型所选 GPU 当前不可用 / Selected model GPUs are currently unavailable"
    available = bool(devices) and all("V100" in d["name"] for d in devices)
    items = []
    active = None
    for id, setting in MODES.items():
        reason = None if available else missing or "These presets require an active V100 model"
        if not reason:
            try:
                gpu.validate_setting(uuids, setting)
            except ValueError as error:
                reason = str(error)
        if not reason and all(
            d["power_limit_w"] == setting["power_limit_w"]
            and "graphics_clock_mhz" in db.get("hardware", d["uuid"], {})
            and db.get("hardware", d["uuid"], {}).get("graphics_clock_mhz")
            == setting["graphics_clock_mhz"]
            for d in devices
        ):
            active = id
        items.append({"id": id, "setting": setting, "available": reason is None, "reason": reason})
    pending = None
    profile_id = db.get("engine", "active", {}).get("profile_id")
    for job in db.all_records("jobs"):
        payload = job.get("payload", {})
        if (
            job.get("kind") == "apply_hardware"
            and job.get("state") not in {"completed", "failed", "cancelled"}
            and payload.get("profile_id") == profile_id
        ):
            mode = next(
                (id for id, setting in MODES.items() if payload.get("setting") == setting), None
            )
            if mode:
                pending = {"mode": mode, "job": job["id"], "stage": job.get("stage")}
                break
    return {"items": items, "active": active, "pending": pending}


def setting_for(id: str, uuids: list[str]) -> dict:
    mode = next((m for m in options(uuids)["items"] if m["id"] == id), None)
    if not mode:
        raise ValueError("Unknown power mode")
    if not mode["available"]:
        raise ValueError(mode["reason"])
    return mode["setting"]
