# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Hardware-scoped control, independent of model lifetime and external workloads."""

import fcntl
import json
import subprocess
import time

from fastapi import HTTPException

from . import db, engine, gpu, gpu_setup, power_modes
from .config import automatic_gpu_actions, state_root
from .jobs import TERMINAL, Job, create_job, list_jobs
from .schemas import HardwareSetting

KIND = "apply_gpu_settings"


class ControlFailure(ValueError):
    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result or {}


def helper(action, **payload):
    if action == "snapshot":
        payload["details"] = True
    result = subprocess.run(
        ["sudo", "-n", str(gpu.helper_path()), action],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    try:
        data = json.loads(result.stdout)
    except ValueError:
        data = {}
    if result.returncode:
        raise ControlFailure(result.stderr.strip() or "GPU control failed", data)
    return data


def inventory():
    control = gpu_setup.helper_check()
    devices = gpu.snapshot().get("gpus", [])
    allowed = set(control.get("allowed_uuids", []))
    from .creative.services import owned_pids

    owned = engine.owned_pids() | owned_pids()
    return control, [
        {
            **d,
            "authorized": d["uuid"] in allowed,
            "external_processes": [p for p in d.get("processes", []) if p["pid"] not in owned],
        }
        for d in devices
    ]


def targets(requested, control, devices):
    if not control.get("available"):
        raise ValueError("请先完成 GPU 控制授权 / Authorize GPU controls first")
    if control.get("protocol", 0) < 2:
        raise ValueError("请更新内置 GPU 控制组件 / Update the bundled GPU control component")
    values = [d["uuid"] for d in devices if d["authorized"]] if requested is None else requested
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(u, str) for u in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError("请选择不重复的 GPU / Select a non-empty, unique GPU list")
    known = {d["uuid"]: d for d in devices}
    if any(u not in known or not known[u]["authorized"] for u in values):
        raise ValueError("所选 GPU 不存在或未授权 / A selected GPU is missing or unauthorized")
    return sorted(values)


def normalized(settings, uuids, *, devices=None):
    if not isinstance(settings, dict) or set(settings) != set(uuids):
        raise ValueError("Settings must match the selected GPUs")
    result = {}
    for uuid in uuids:
        value = HardwareSetting.model_validate(settings[uuid]).model_dump()
        if (
            value["power_limit_w"] is None
            and value["graphics_clock_mhz"] is None
            and not value["reset_clocks"]
        ):
            raise ValueError("请选择功率或频率设置 / Choose a power or clock setting")
        gpu.validate_setting([uuid], value, devices=devices)
        result[uuid] = value
    return result


def matches(actual, setting):
    return bool(
        actual
        and actual.get("clock_policy_known")
        and not actual.get("recovery_pending")
        and (
            setting.get("power_limit_w") is None
            or abs(actual["power_limit_w"] - setting["power_limit_w"]) <= 0.6
        )
        and (
            actual.get("graphics_clock_mhz") is None
            if setting.get("reset_clocks")
            else setting.get("graphics_clock_mhz") is None
            or actual.get("graphics_clock_mhz") == setting["graphics_clock_mhz"]
        )
    )


def status(requested=None):
    control, devices = inventory()
    error = None
    actual = {}
    uuids = requested or []
    try:
        uuids = targets(requested, control, devices)
        actual = helper("snapshot", uuids=[d["uuid"] for d in devices if d["authorized"]])
    except (ValueError, OSError, subprocess.SubprocessError) as problem:
        error = str(problem)
    selected = set(uuids)
    items = []
    for mode, setting in power_modes.MODES.items():
        reason = error
        if not reason:
            try:
                if any("V100" not in d["name"] for d in devices if d["uuid"] in selected):
                    raise ValueError("这些档位适用于 V100 / These presets require V100 GPUs")
                normalized({u: setting for u in uuids}, uuids, devices=devices)
            except ValueError as problem:
                reason = str(problem)
        items.append(
            {"id": mode, "setting": setting, "available": reason is None, "reason": reason}
        )
    active = next(
        (
            i["id"]
            for i in items
            if i["available"] and all(matches(actual.get(u), i["setting"]) for u in uuids)
        ),
        None,
    )
    list_jobs()
    jobs = [j for j in db.all_records("jobs") if j["kind"] == KIND]
    pending = next((j for j in jobs if j["state"] not in TERMINAL), None)
    latest = next(
        (j for j in jobs if selected.intersection(j.get("payload", {}).get("uuids", []))), None
    )
    policies = {p["id"]: p for p in db.all_records("gpu_policies")}
    chosen = {u: actual[u] for u in uuids if u in actual}
    signatures = {(a.get("power_limit_w"), a.get("graphics_clock_mhz")) for a in chosen.values()}
    state = (
        "applied"
        if active
        else "mixed"
        if len(signatures) > 1
        else "applied"
        if chosen and all(a.get("clock_policy_known") for a in chosen.values())
        else "unknown"
    )
    if any(not matches(actual.get(u), policies[u]["setting"]) for u in uuids if u in policies):
        state = "selected"
    if latest and latest["state"] == "failed":
        state = "failed"
    if any(a.get("recovery_pending") for a in actual.values()):
        state = "failed"
    if pending:
        state = (
            "applying"
            if pending.get("stage") in {"applying_gpu_settings", "recovering_gpu_settings"}
            else "waiting"
        )
    for device in devices:
        u = device["uuid"]
        device.update(actual=actual.get(u), policy=policies.get(u))
    return {
        "scope": "all" if requested is None else "custom",
        "active": active,
        "state": state,
        "setup_required": not control.get("available") or control.get("protocol", 0) < 2,
        "items": items,
        "devices": devices,
        "gpu_uuids": uuids,
        "restore_error": db.get("gpu_control", "restore_error", {}).get("error"),
        "error": error,
        "pending": {
            "job": pending["id"],
            "mode": pending["payload"].get("mode"),
            "stage": pending["stage"],
            "gpu_uuids": pending["payload"]["uuids"],
            "cancel_requested": pending.get("cancel_requested"),
            "detail": pending.get("detail"),
            "created_at": pending.get("created_at"),
        }
        if pending
        else None,
        "last_result": {k: latest.get(k) for k in ("id", "state", "error", "result")}
        if latest
        else None,
    }


def queue(requested, *, mode=None, setting=None, settings=None, recover=False, recover_only=False):
    control, devices = inventory()
    uuids = targets(requested, control, devices)
    if mode is not None:
        if mode not in power_modes.MODES:
            raise ValueError("Unknown power mode")
        if any("V100" not in d["name"] for d in devices if d["uuid"] in uuids):
            raise ValueError("These presets require V100 GPUs")
        setting = power_modes.MODES[mode]
    values = (
        {}
        if recover_only
        else normalized(settings if settings is not None else {u: setting for u in uuids}, uuids, devices=devices)
    )
    payload = {"uuids": uuids, "settings": values, "mode": mode, "recover": recover}
    with (state_root() / "gpu-queue.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _enqueue(payload)


def _enqueue(payload):
    list_jobs()
    for job in db.all_records("jobs"):
        if job["kind"] == KIND and job["state"] not in TERMINAL:
            if job["payload"] == payload:
                return {k: v for k, v in job.items() if k != "payload"}
            raise HTTPException(
                409, "请等待或取消当前 GPU 调节任务 / Wait for or cancel the current GPU task"
            )
    job = create_job(KIND, payload)
    return {k: v for k, v in job.items() if k != "payload"}


def apply_settings(values, *, persist):
    uuids = sorted(values)
    control, devices = inventory()
    targets(uuids, control, devices)
    normalized(values, uuids, devices=devices)
    result = helper("apply", uuids=uuids, settings=values, initialize_unknown=True)
    try:
        readback = result["devices"]
        for uuid in uuids:
            if not matches(readback.get(uuid), values[uuid]):
                raise ControlFailure(
                    "GPU 设置读回不一致 / GPU settings did not match readback", result
                )
        # Persist actual full settings, including a retained power limit for clock-only changes.
        with db.connect() as connection:
            for uuid, actual in readback.items():
                setting = {
                    "power_limit_w": actual["power_limit_w"],
                    "graphics_clock_mhz": actual.get("graphics_clock_mhz"),
                    "reset_clocks": actual.get("graphics_clock_mhz") is None,
                }
                records = [("hardware", {**setting, "boot_id": actual["boot_id"]})]
                if persist:
                    records.append(
                        (
                            "gpu_policies",
                            {"id": uuid, "setting": setting, "updated_at": time.time()},
                        )
                    )
                for bucket, value in records:
                    connection.execute(
                        "INSERT OR REPLACE INTO records VALUES(?,?,?,?)",
                        (bucket, uuid, json.dumps(value), time.time()),
                    )
    except Exception as error:
        # Hardware confirmation and durable policy storage form one operation.
        # If saving fails, restore the helper's pre-operation snapshot as well.
        previous = result.get("previous", {})
        restored = {}
        try:
            if set(previous) != set(uuids):
                raise ValueError("Missing pre-operation snapshot")
            old = {
                u: {k: p[k] for k in ("power_limit_w", "graphics_clock_mhz", "reset_clocks")}
                for u, p in previous.items()
            }
            helper("apply", uuids=uuids, settings=old, initialize_unknown=True)
            restored = {
                u: {"state": "restored", "restored_exact": p["clock_policy_known"]}
                for u, p in previous.items()
            }
        except Exception as restore_error:  # noqa: BLE001 -- report every failed recovery
            restored = {u: {"state": "restore_failed", "error": str(restore_error)} for u in uuids}
        raise ControlFailure(str(error), {"ok": False, "devices": restored}) from error
    return result


def pending_uuids():
    control, devices = inventory()
    uuids = targets(None, control, devices)
    actual = helper("snapshot", uuids=uuids)
    return [u for u, a in actual.items() if a.get("recovery_pending")]


def recover_pending():
    control, devices = inventory()
    uuids = targets(None, control, devices)
    actual = helper("snapshot", uuids=uuids)
    if any(a.get("recovery_pending") for a in actual.values()):
        return helper("apply", uuids=uuids, recover=True)
    return {}


def execute(job: Job):
    uuids = job.payload["uuids"]
    with engine.operation_lock(job):
        affected = set(uuids).union(pending_uuids())
        state = engine.private_state()
        overlaps = state.get("state") in {"ready", "loading", "unavailable", "stopping"} and bool(
            affected.intersection(state.get("profile", {}).get("gpu_uuids", []))
        )
        if overlaps:
            db.put("engine", "maintenance", {"job_id": job.id, "kind": KIND})
        try:
            if overlaps:
                engine.drain(job)
            job.check_cancelled()
            job.update("recovering_gpu_settings")
            recovery = recover_pending()
            job.check_cancelled()
            job.update("applying_gpu_settings")
            result = (
                apply_settings(job.payload["settings"], persist=not job.payload.get("recover"))
                if job.payload["settings"]
                else {}
            )
            db.delete("gpu_control", "restore_error")
            return {**result, "recovery": recovery}
        except ControlFailure as error:
            job.update("gpu_control_failed", ignore_cancel=True, result=error.result)
            raise
        finally:
            if db.get("engine", "maintenance", {}).get("job_id") == job.id:
                db.delete("engine", "maintenance")


def ensure_saved(uuids):
    policies = {u: db.get("gpu_policies", u) for u in uuids}
    values = {u: p["setting"] for u, p in policies.items() if p}
    if not values and gpu_setup.helper_check().get("protocol", 0) < 2:
        return
    recover_pending()
    if not values:
        return
    actual = helper("snapshot", uuids=sorted(values))
    changed = {u: v for u, v in values.items() if not matches(actual.get(u), v)}
    if changed:
        apply_settings(changed, persist=False)


def reconcile_saved():
    """One restart reconciliation; failures stay visible instead of being retried forever."""
    if not automatic_gpu_actions():
        return
    policies = db.all_records("gpu_policies")
    values = {p["id"]: p["setting"] for p in policies}
    try:
        pending = pending_uuids()
        actual = helper("snapshot", uuids=sorted(values)) if values else {}
        changed = {u: v for u, v in values.items() if not matches(actual.get(u), v)}
        if changed:
            queue(sorted(changed), settings=changed, recover=True)
        elif pending:
            queue(pending, recover=True, recover_only=True)
        db.delete("gpu_control", "restore_error")
    except (ValueError, OSError, subprocess.SubprocessError, HTTPException) as error:
        if not values and not gpu_setup.helper_check().get("available"):
            return
        db.put("gpu_control", "restore_error", {"error": str(error), "updated_at": time.time()})
