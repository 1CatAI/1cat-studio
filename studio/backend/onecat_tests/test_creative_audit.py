# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import asyncio
import time

import httpx
import pytest
from onecat import db, jobs
from onecat.creative import api, components, runs, services, store
from onecat.creative.schema import Document, Node, Service


@pytest.fixture(autouse=True)
def no_gpu(monkeypatch):
    monkeypatch.setenv("ONECAT_AUTO_GPU_ACTIONS", "0")
    monkeypatch.setattr(services.gpu, "snapshot", lambda: {"gpus": []})


def service(kind="image-api"):
    record = services.save(
        Service(name="Fixture", kind="image-api", model="fixture", base_url="http://localhost:9999")
    )
    value = db.get("creative_services", record["id"])
    value["kind"] = kind
    db.put("creative_services", value["id"], value)
    db.put("creative_instances", value["id"], {"state": "ready"})
    return value


def task(kind, identity):
    jid = db.uid()
    db.put(
        "jobs",
        jid,
        {
            "id": jid,
            "kind": kind,
            "state": "running",
            "payload": {"service_id": identity},
            "created_at": time.time(),
            "cancel_requested": False,
        },
    )
    return jobs.Job(jid)


def test_stop_refusal_keeps_service_ready(state):
    s = service("h3-local")
    db.put("creative_runs", "pending", {"id": "pending", "service_id": s["id"], "state": "running"})
    with pytest.raises(ValueError, match="finish"):
        services.lifecycle(task("creative_stop", s["id"]))
    assert services.instance(s["id"])["state"] == "ready"


def test_no_admission_after_stop_begins(state, monkeypatch):
    s = service("h3-local")
    project = store.create(
        Document(
            nodes=[
                Node(
                    id="gen",
                    kind="generate",
                    x=0,
                    y=0,
                    text="A forest",
                    workflow="h3-t2va",
                    service_id=s["id"],
                )
            ]
        )
    )
    created = []
    monkeypatch.setattr(runs, "create_job", lambda *args: created.append(args) or {"id": "fake"})

    def stopping(identity):
        with pytest.raises(ValueError, match="service"):
            runs.queue(project["id"], "gen", 1, "during-stop-12345678")
        db.patch("creative_instances", identity, {"state": "stopped"})

    monkeypatch.setattr(services, "stop_owned", stopping)
    services.lifecycle(task("creative_stop", s["id"]))
    assert not created


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_cannot_edit_or_delete_queued_lifecycle(state, operation):
    s = service()
    task("creative_load", s["id"])
    with pytest.raises(ValueError, match="(operation|loading|task)"):
        if operation == "delete":
            api.delete_service(s["id"])
        else:
            services.save(Service.model_validate({k: v for k, v in s.items() if k != "updated_at"}))


def test_health_monitor_recovers_after_transient_failure(state, monkeypatch):
    s = service()
    calls = []

    def check(record):
        calls.append(record["id"])
        if len(calls) == 1:
            raise httpx.ConnectError("temporary outage")
        db.patch("creative_instances", record["id"], {"state": "ready"})

    ticks = []

    async def sleep(_):
        ticks.append(1)
        if len(ticks) == 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(services, "check", check)
    monkeypatch.setattr(services.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(services.monitor())
    assert len(calls) == 2
    assert services.instance(s["id"])["state"] == "ready"


@pytest.mark.parametrize("transition", ["stopped", "edited", "deleted"])
def test_old_health_failure_does_not_overwrite_new_state(state, monkeypatch, transition):
    s = service()

    def check(record):
        if transition == "edited":
            db.patch("creative_services", s["id"], {"model": "new-model"})
        if transition == "deleted":
            db.delete("creative_services", s["id"])
            db.delete("creative_instances", s["id"])
        else:
            db.put(
                "creative_instances",
                s["id"],
                {"state": "unchecked" if transition == "edited" else "stopped"},
            )
        raise httpx.ConnectError("late response from old check")

    async def stop(_):
        raise asyncio.CancelledError()

    monkeypatch.setattr(services, "check", check)
    monkeypatch.setattr(services.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(services.monitor())
    result = db.get("creative_instances", s["id"])
    if transition == "deleted":
        assert result is None
    else:
        assert result["state"] == ("unchecked" if transition == "edited" else "stopped")


def test_missing_component_files_enable_repair_download(state, monkeypatch):
    root = state / "component"
    root.mkdir()
    row = {"id": "base", "role": "base", "bytes": 3, "files": [{"path": "weights.safetensors", "bytes": 3}]}
    monkeypatch.setattr(components, "catalog", lambda: [row])
    db.put("creative_components", "base", {"id": "base", "path": str(root)})
    assert components.listed()[0]["installed"] is None
    (root / "weights.safetensors").write_bytes(b"123")
    assert components.listed()[0]["installed"]["path"] == str(root)
    (root / "weights.safetensors").write_bytes(b"12")
    assert components.listed()[0]["installed"] is None


def test_dead_lifecycle_worker_does_not_lock_service_forever(state, monkeypatch):
    s = service()
    job = task("creative_load", s["id"])
    db.patch("jobs", job.id, {"pid": 999999, "process_created": 1})
    monkeypatch.setattr(jobs, "same_process", lambda *args: False)
    assert not services.pending_lifecycle(s["id"])
    assert db.get("jobs", job.id)["state"] == "failed"


def test_cancel_queued_operation_preserves_running_service(state):
    s = service("h3-local")
    job = task("creative_stop", s["id"])
    db.patch("jobs", job.id, {"cancel_requested": True})
    with pytest.raises(jobs.Cancelled):
        services.lifecycle(job)
    assert services.instance(s["id"])["state"] == "ready"


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_unavailable_local_service_must_stop_before_editing(state, operation):
    s = service("h3-local")
    db.patch("creative_instances", s["id"], {"state": "unavailable"})
    with pytest.raises(ValueError, match="Stop"):
        if operation == "delete":
            api.delete_service(s["id"])
        else:
            services.save(Service.model_validate({k: v for k, v in s.items() if k != "updated_at"}))
