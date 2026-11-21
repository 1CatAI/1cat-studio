"""A manager-only deployment must not act on another agent's GPU workload."""
import asyncio

import pytest

from onecat import adopt, app, catalog, db, gpu, gpu_control, lifecycle, runtimes
from onecat.config import automatic_gpu_actions


def forbidden(*args, **kwargs):
    pytest.fail('Automatic GPU/process action ran during deployment hold')


def test_default_and_operator_override(monkeypatch):
    monkeypatch.delenv('ONECAT_AUTO_GPU_ACTIONS', raising=False)
    assert automatic_gpu_actions()
    monkeypatch.setenv('ONECAT_AUTO_GPU_ACTIONS', '0')
    assert not automatic_gpu_actions()
    monkeypatch.setenv('ONECAT_AUTO_GPU_ACTIONS', '1')
    assert automatic_gpu_actions()


def test_direct_restart_reconciliation_respects_hold(monkeypatch):
    monkeypatch.setenv('ONECAT_AUTO_GPU_ACTIONS', '0')
    db.put('gpu_policies', 'gpu', {'id': 'gpu', 'setting': {'power_limit_w': 150}})
    monkeypatch.setattr(gpu_control, 'helper', forbidden)
    monkeypatch.setattr(gpu_control, 'pending_uuids', forbidden)
    monkeypatch.setattr(gpu_control, 'queue', forbidden)
    gpu_control.reconcile_saved()
    assert db.get('gpu_policies', 'gpu')['setting']['power_limit_w'] == 150


# Not named "state": that would replace the fixture isolating the database.
@pytest.mark.parametrize('engine_state', ['stopped', 'ready'])
def test_background_startup_and_idle_do_not_touch_gpus(monkeypatch, engine_state):
    monkeypatch.setenv('ONECAT_AUTO_GPU_ACTIONS', '0')
    db.put('settings', 'main', {'autostart_profile': 'p', 'idle_unload_minutes': 1})
    monkeypatch.setattr(gpu, 'recover_interrupted', forbidden)
    monkeypatch.setattr(adopt, 'reconcile', forbidden)
    monkeypatch.setattr(gpu_control, 'reconcile_saved', forbidden)
    monkeypatch.setattr(lifecycle, 'reconcile_owned', forbidden)
    monkeypatch.setattr(app, 'scheduled_job', forbidden)
    monkeypatch.setattr(runtimes, 'refresh_incomplete_metadata', lambda: None)
    monkeypatch.setattr(catalog, 'backfill_default_profiles', lambda: None)
    monkeypatch.setattr(app.engine, 'status', lambda: {'state': engine_state, 'last_request_at': 0, 'adopted_service': True})
    calls = 0
    async def sleep(_seconds):
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise asyncio.CancelledError
    monkeypatch.setattr(app.asyncio, 'sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(app.lifecycle_monitor())
    assert calls == 3


@pytest.mark.parametrize("prior,enabled,expected", [
    ({"state": "ready"}, True, ["last"]),
    ({"state": "failed", "ready_at": 100}, True, ["last"]),
    ({"state": "stopped", "last_profile_id": "last"}, True, []),
    ({"state": "failed"}, True, []),
    ({"state": "loading"}, True, []),
    ({"state": "ready"}, False, []),
])
def test_restore_once_per_boot(monkeypatch, prior, enabled, expected):
    monkeypatch.setenv("ONECAT_AUTO_GPU_ACTIONS", "0")
    monkeypatch.setenv("ONECAT_BOOT_MODEL_RESTORE", "1")
    from pathlib import Path
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda p, *a, **kw:
                        "test-boot" if p.name == "boot_id" else original(p, *a, **kw))
    db.put("settings", "main", {"restore_model_on_boot": enabled})
    db.put("profiles", "last", {"id": "last"})
    db.put("engine", "active", {"profile_id": "last", **prior})
    monkeypatch.setattr(app.engine, "status", lambda: {"state": "failed"})
    queued = []
    monkeypatch.setattr(app, "scheduled_job", lambda kind, payload: queued.append(payload["profile_id"]))
    app.restore_model_on_boot()
    app.restore_model_on_boot()
    assert queued == expected
