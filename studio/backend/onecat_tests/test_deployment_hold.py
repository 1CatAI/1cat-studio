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


@pytest.mark.parametrize('state', ['stopped', 'ready'])
def test_background_startup_and_idle_do_not_touch_gpus(monkeypatch, state):
    monkeypatch.setenv('ONECAT_AUTO_GPU_ACTIONS', '0')
    db.put('settings', 'main', {'autostart_profile': 'p', 'idle_unload_minutes': 1})
    monkeypatch.setattr(gpu, 'recover_interrupted', forbidden)
    monkeypatch.setattr(adopt, 'reconcile', forbidden)
    monkeypatch.setattr(gpu_control, 'reconcile_saved', forbidden)
    monkeypatch.setattr(lifecycle, 'reconcile_owned', forbidden)
    monkeypatch.setattr(app, 'scheduled_job', forbidden)
    monkeypatch.setattr(runtimes, 'refresh_incomplete_metadata', lambda: None)
    monkeypatch.setattr(catalog, 'backfill_default_profiles', lambda: None)
    monkeypatch.setattr(app.engine, 'status', lambda: {'state': state, 'last_request_at': 0, 'adopted_service': True})
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
