# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import os
import signal
import subprocess
import sys
import time

import psutil
import pytest

from onecat import db, engine, gpu, model_switch
from onecat.creative import services
from onecat.jobs import Job, same_process


def record_job(kind, payload, **fields):
    identity = db.uid()
    return db.put(
        "jobs",
        identity,
        dict(
            id=identity,
            kind=kind,
            payload=payload,
            state="queued",
            stage="queued",
            created_at=time.time(),
            **fields,
        ),
    )


@pytest.fixture
def queue(monkeypatch):
    monkeypatch.setattr(model_switch, "create_job", record_job)
    db.put("profiles", "text", {"gpu_uuids": ["g1"]})
    db.put(
        "creative_services",
        "h3",
        {"id": "h3", "name": "H3", "kind": "h3-local", "partition": "fl2va", "gpu_uuids": ["g1"]},
    )
    db.put(
        "creative_services",
        "other",
        {
            "id": "other",
            "name": "Other",
            "kind": "h3-local",
            "partition": "fl2va",
            "gpu_uuids": ["g2"],
        },
    )


def test_switch_cancels_overlapping_load_but_keeps_other_gpu_work(queue):
    old = model_switch.schedule("creative_load", {"service_id": "h3"})
    other = model_switch.schedule("creative_load", {"service_id": "other"})
    new = model_switch.schedule("start_model", {"profile_id": "text"})
    assert db.get("jobs", old["id"])["cancel_requested"]
    assert not db.get("jobs", other["id"]).get("cancel_requested")
    assert model_switch.schedule("start_model", {"profile_id": "text"})["id"] == new["id"]


def test_cancelled_load_can_be_retried_with_new_job(queue):
    old = model_switch.schedule("creative_load", {"service_id": "h3"})
    stop = model_switch.schedule("creative_stop", {"service_id": "h3"})
    assert db.get("jobs", old["id"])["cancel_requested"]
    fresh = model_switch.schedule("creative_load", {"service_id": "h3"})
    assert fresh["id"] not in (stop["id"], old["id"])


def test_queued_creative_load_is_visible_before_pid(queue):
    job = model_switch.schedule("creative_load", {"service_id": "h3"})
    view = services.public(db.get("creative_services", "h3"))["instance"]
    assert view["state"] == "loading" and view["job_id"] == job["id"]
    assert view["phase"] == "queued" and view["can_stop"]


def test_queued_text_load_is_visible_while_hardware_lock_is_busy(queue):
    model_switch.schedule("start_model", {"profile_id": "text"})
    db.put("engine", "operation", {"job_id": "h3-operation", "kind": "creative_load"})
    view = engine.status()
    assert view["state"] == "loading" and view["phase"] == "queued"
    assert view["actions"]["cancel"] and view["profile_id"] == "text"
    assert engine.private_state()["state"] == "stopped"


def test_shared_gpu_release_ignores_disjoint_services(queue, monkeypatch):
    for identity in ("h3", "other"):
        db.put("creative_instances", identity, {"state": "ready"})
    stopped = []
    monkeypatch.setattr(services, "stop_owned", stopped.append)
    job = Job(record_job("start_model", {"profile_id": "text"})["id"])
    services.release_overlapping(job, ["g1"])
    assert stopped == ["h3"] and services.instance("other")["state"] == "ready"
    assert db.get("jobs", job.id)["stage"] == "releasing_model"


def test_generation_must_finish_before_automatic_unload(queue, monkeypatch):
    db.put("creative_instances", "h3", {"state": "ready"})
    db.put("creative_runs", "busy", {"service_id": "h3", "state": "running"})
    monkeypatch.setattr(services, "stop_owned", lambda _: pytest.fail("Busy service stopped"))
    with pytest.raises(services.ServiceBusy, match="finish"):
        services.release_overlapping(Job(record_job("start_model", {})["id"]), ["g1"])
    assert services.instance("h3")["state"] == "ready"


def test_h3_early_http_message_does_not_hide_weight_counts():
    text = (
        "Waiting for application startup.\nLoading checkpoint shards: 50%|### | 2/4 [00:01<00:01]\n"
    )
    result = services.load_progress(text, {})
    assert result["phase"] == "loading_weights"
    assert result["phase_progress"] == {"done": 2, "total": 4, "percent": 50.0}


def test_reused_pid_is_not_owned_or_killed(queue, monkeypatch):
    db.put(
        "creative_instances",
        "h3",
        {"state": "failed", "pid": 123, "process_created": 10, "owned_processes": {"123": 10}},
    )
    monkeypatch.setattr(services, "same_process", lambda *a: False)
    monkeypatch.setattr(services.os, "kill", lambda *a: pytest.fail("Unrelated PID killed"))
    assert services.owned_pids() == set()
    services.stop_owned("h3")
    assert services.instance("h3")["state"] == "stopped"


def test_unload_waits_for_real_owned_descendants(queue, tmp_path):
    childfile = tmp_path / "child"
    code = "import subprocess,sys,time,pathlib;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)']);pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(90)"
    argv = [sys.executable, "-c", code, str(childfile)]
    proc = subprocess.Popen(argv, start_new_session=True)
    try:
        deadline = time.monotonic() + 5
        while not childfile.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child = int(childfile.read_text())
        child_created = psutil.Process(child).create_time()
        db.put(
            "creative_instances",
            "h3",
            {
                "state": "loading",
                "pid": proc.pid,
                "process_created": psutil.Process(proc.pid).create_time(),
                "argv": argv,
            },
        )
        assert {proc.pid, child} <= services.owned_pids()
        services.stop_owned("h3")
        assert not same_process(child, child_created)
        assert services.instance("h3")["state"] == "stopped"
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def test_setting_validation_reuses_snapshot(monkeypatch):
    monkeypatch.setattr(gpu, "snapshot", lambda: pytest.fail("Repeated hardware enumeration"))
    devices = [
        {
            "uuid": "g1",
            "name": "V100",
            "power_min_w": 150,
            "power_max_w": 300,
            "supported_graphics_clocks_mhz": [975],
        }
    ]
    gpu.validate_setting(["g1"], {"power_limit_w": 185}, devices=devices)
    with pytest.raises(ValueError, match="unsupported"):
        gpu.validate_setting(["g1"], {"power_limit_w": 400}, devices=devices)
