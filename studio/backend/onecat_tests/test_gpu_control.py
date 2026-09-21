# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import copy
import time
from contextlib import contextmanager

import pytest

from onecat import db, engine, gpu, gpu_control as control
from onecat.jobs import Job

U1 = "GPU-11111111-1111-1111-1111-111111111111"
U2 = "GPU-22222222-2222-2222-2222-222222222222"


def test_old_helper_exposes_setup_action_without_reading_or_writing_hardware(hardware, monkeypatch):
    devices, _actual, calls = hardware
    monkeypatch.setattr(control, "inventory", lambda: ({"available": True, "protocol": 1}, devices))
    value = control.status()
    assert value["setup_required"] is True
    assert all(not mode["available"] for mode in value["items"])
    assert not calls


@pytest.fixture
def hardware(monkeypatch):
    # This fixture owns the fake inventory, helper and job queue. Host opt-out
    # still disables real lifecycle work; reconciliation here exercises fakes.
    monkeypatch.setattr(control, "automatic_gpu_actions", lambda: True)
    devices = [
        {
            "uuid": u,
            "index": i,
            "name": "V100",
            "authorized": True,
            "processes": [{"pid": 123, "name": "external"}],
            "power_min_w": 150,
            "power_max_w": 300,
            "supported_graphics_clocks_mhz": [975],
            "power_limit_w": 300,
        }
        for i, u in enumerate([U1, U2])
    ]
    actual = {
        u: {
            "power_limit_w": 300,
            "graphics_clock_mhz": None,
            "reset_clocks": True,
            "clock_policy_known": True,
            "boot_id": "boot",
        }
        for u in [U1, U2]
    }
    monkeypatch.setattr(
        control,
        "inventory",
        lambda: ({"available": True, "protocol": 2, "allowed_uuids": [U1, U2]}, devices),
    )
    monkeypatch.setattr(
        gpu, "selected_devices", lambda ids: [d for d in devices if d["uuid"] in ids]
    )
    monkeypatch.setattr(engine, "status", lambda: db.get("engine", "active", {"state": "stopped"}))
    calls = []

    def helper(action, **payload):
        calls.append((action, payload))
        if action == "snapshot":
            return {u: copy.deepcopy(actual[u]) for u in payload["uuids"]}
        previous = copy.deepcopy(actual)
        for value in previous.values():
            value["reset_clocks"] = value.get("graphics_clock_mhz") is None
        for u, setting in payload.get("settings", {}).items():
            actual[u].update({k: v for k, v in setting.items() if v is not None})
            if setting.get("reset_clocks"):
                actual[u]["graphics_clock_mhz"] = None
        return {
            "ok": True,
            "devices": {u: {**actual[u], "state": "applied"} for u in payload["uuids"]},
            "previous": {u: previous[u] for u in payload["uuids"]},
        }

    monkeypatch.setattr(control, "helper", helper)

    def create(kind, payload):
        job = {
            "id": db.uid(),
            "kind": kind,
            "payload": payload,
            "created_at": time.time(),
            "state": "queued",
            "stage": "queued",
        }
        return db.put("jobs", job["id"], job)

    monkeypatch.setattr(control, "create_job", create)
    return devices, actual, calls


def test_stopped_all_cards_and_legacy_scope(client, hardware):
    db.put("engine", "active", {"state": "stopped", "profile": {"gpu_uuids": [U1]}})
    result = client.get("/api/gpu/power-modes").json()
    assert result["gpu_uuids"] == [U1, U2] and all(i["available"] for i in result["items"])
    assert client.get("/api/inference/power-modes").json()["gpu_uuids"] == [U1]
    first = client.post("/api/gpu/power-mode", json={"id": "eco"}).json()
    assert db.get("jobs", first["id"])["payload"]["uuids"] == [U1, U2]
    assert client.post("/api/gpu/power-mode", json={"id": "eco"}).json()["id"] == first["id"]
    assert client.post("/api/gpu/power-mode", json={"id": "balanced"}).status_code == 409
    client.post("/api/jobs/" + first["id"] + "/cancel")
    assert db.get("jobs", first["id"])["cancel_requested"]


@pytest.mark.parametrize("ids", [[], [U1, U1], ["GPU-missing"]])
def test_invalid_scope_is_rejected_before_job(client, hardware, ids):
    assert (
        client.post("/api/gpu/power-mode", json={"id": "eco", "gpu_uuids": ids}).status_code == 400
    )
    assert not db.all_records("jobs")


def test_unauthorized_and_invalid_setting(client, hardware):
    hardware[0][1]["authorized"] = False
    assert (
        client.post("/api/gpu/power-mode", json={"id": "eco", "gpu_uuids": [U2]}).status_code == 400
    )
    assert (
        client.post(
            "/api/gpu/settings", json={"gpu_uuids": [U1], "setting": {"power_limit_w": 120}}
        ).status_code
        == 400
    )
    assert not db.all_records("jobs")


def test_external_task_allowed_and_fixed_target(hardware, monkeypatch):
    job = control.queue([U1], mode="balanced")
    db.put("engine", "active", {"state": "ready", "profile": {"gpu_uuids": [U2]}})
    monkeypatch.setattr(
        engine, "drain", lambda job: pytest.fail("Disjoint GPU request must not drain inference")
    )
    monkeypatch.setattr(
        gpu,
        "check_ownership",
        lambda ids: pytest.fail("Explicit hardware control permits external tasks"),
    )
    monkeypatch.setattr(engine, "stop", lambda *a, **k: pytest.fail("Do not stop external tasks"))
    control.execute(Job(job["id"]))
    assert hardware[1][U1]["power_limit_w"] == 185
    assert hardware[1][U2]["power_limit_w"] == 300
    assert db.get("gpu_policies", U1)["setting"]["power_limit_w"] == 185
    assert not db.get("engine", "maintenance")


def test_overlap_drains_and_failure_releases_lock(hardware, monkeypatch):
    db.put("engine", "active", {"state": "ready", "profile": {"gpu_uuids": [U1]}})
    job = control.queue([U1], mode="eco")
    drained = []

    def drain(job):
        assert db.get("engine", "maintenance")["job_id"] == job.id
        drained.append(True)

    monkeypatch.setattr(engine, "drain", drain)

    def fail(values, **kwargs):
        raise control.ControlFailure(
            "driver failure", {"devices": {U1: {"state": "restore_failed"}}}
        )

    monkeypatch.setattr(control, "apply_settings", fail)
    with pytest.raises(control.ControlFailure):
        control.execute(Job(job["id"]))
    assert drained and not db.get("engine", "maintenance")
    assert db.get("jobs", job["id"])["result"]["devices"][U1]["state"] == "restore_failed"
    assert not db.get("gpu_policies", U1)


def test_cancelled_operation_does_not_apply(hardware):
    job = control.queue([U1], mode="eco")
    db.patch("jobs", job["id"], {"cancel_requested": True})
    from onecat.jobs import Cancelled

    with pytest.raises(Cancelled):
        control.execute(Job(job["id"]))
    assert not any(action == "apply" for action, data in hardware[2])


def test_mixed_unknown_and_saved_policy_restore(hardware):
    hardware[1][U1]["power_limit_w"] = 185
    assert control.status()["state"] == "mixed"
    hardware[1][U2]["clock_policy_known"] = False
    assert control.status([U2])["state"] == "unknown"
    saved = {"power_limit_w": 185, "graphics_clock_mhz": None, "reset_clocks": True}
    db.put("gpu_policies", U2, {"id": U2, "setting": saved})
    hardware[1][U2]["clock_policy_known"] = True
    control.ensure_saved([U2])
    assert hardware[1][U2]["power_limit_w"] == 185
    assert control.status()["active"] == "balanced"
    before = len(hardware[2])
    control.ensure_saved([U2])
    assert all(action == "snapshot" for action, data in hardware[2][before:])


def test_concurrent_identical_submissions_share_one_job(hardware):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: control.queue([U2, U1], mode="eco")["id"], range(4)))
    assert len(set(ids)) == 1
    assert len(db.all_records("jobs")) == 1


def test_restart_only_restores_mismatched_gpu_and_preserves_policy(hardware):
    saved = {"power_limit_w": 185, "graphics_clock_mhz": None, "reset_clocks": True}
    for u in [U1, U2]:
        db.put("gpu_policies", u, {"id": u, "setting": saved, "updated_at": 100})
    hardware[1][U1]["power_limit_w"] = 185
    control.reconcile_saved()
    record = db.all_records("jobs")[0]
    assert record["payload"]["uuids"] == [U2]
    control.execute(Job(record["id"]))
    assert db.get("gpu_policies", U2)["updated_at"] == 100
    assert hardware[1][U2]["power_limit_w"] == 185


def test_dead_worker_does_not_keep_control_disabled(hardware):
    job = control.queue([U1], mode="eco")
    db.patch("jobs", job["id"], {"pid": 99999999, "process_created": 1})
    status = control.status()
    assert status["pending"] is None
    assert status["state"] == "failed"
    assert all(item["available"] for item in status["items"])


def test_selected_scope_still_reports_other_device_policies(hardware):
    result = control.status([U1])
    assert result["scope"] == "custom" and result["gpu_uuids"] == [U1]
    assert all(device["actual"]["clock_policy_known"] for device in result["devices"])


def test_recovery_without_saved_policy_runs_and_can_retry(client, hardware, monkeypatch):
    hardware[1][U1]["recovery_pending"] = True
    control.reconcile_saved()
    record = db.all_records("jobs")[0]
    assert record["payload"]["settings"] == {} and record["payload"]["uuids"] == [U1]
    db.patch("jobs", record["id"], {"state": "failed"})
    retry = client.post("/api/jobs/" + record["id"] + "/retry")
    assert retry.status_code == 200, retry.text
    monkeypatch.setattr(
        control,
        "apply_settings",
        lambda *args, **kwargs: pytest.fail("Recovery must not apply a new policy"),
    )
    control.execute(Job(retry.json()["id"]))
    assert not db.all_records("gpu_policies")


def test_waiting_hardware_operation_can_be_cancelled(hardware):
    import fcntl
    from concurrent.futures import ThreadPoolExecutor
    from onecat.config import state_root
    from onecat.jobs import Cancelled

    job = control.queue([U1], mode="eco")
    with (
        (state_root() / "engine.lock").open("a") as lock,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        fcntl.flock(lock, fcntl.LOCK_EX)
        running = pool.submit(control.execute, Job(job["id"]))
        for _ in range(100):
            if db.get("jobs", job["id"])["stage"] == "waiting_for_engine":
                break
            time.sleep(0.01)
        assert db.get("jobs", job["id"])["stage"] == "waiting_for_engine"
        db.patch("jobs", job["id"], {"cancel_requested": True})
        with pytest.raises(Cancelled):
            running.result(timeout=3)
    assert not any(action == "apply" for action, _ in hardware[2])


def test_persistence_failure_rolls_back_hardware(hardware, monkeypatch):
    @contextmanager
    def broken_database():
        raise OSError("Database write failed")
        yield

    with monkeypatch.context() as patch:
        patch.setattr(db, "connect", broken_database)
        with pytest.raises(control.ControlFailure) as error:
            control.apply_settings({U1: control.power_modes.MODES["eco"]}, persist=True)
    assert hardware[1][U1]["power_limit_w"] == 300
    assert hardware[1][U1]["graphics_clock_mhz"] is None
    assert error.value.result["devices"][U1] == {"state": "restored", "restored_exact": True}
    assert not db.get("gpu_policies", U1)
