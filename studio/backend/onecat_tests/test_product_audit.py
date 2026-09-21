"""Regression cases found in the September 8 product-flow audit."""
import io
import time

import pytest
from onecat import db, jobs
from PIL import Image


@pytest.mark.parametrize('payload', [
    {'port': 8888.5}, {'port': None}, {'idle_unload_minutes': 1.5},
    {'idle_unload_minutes': True}, {'modelscope_endpoint': 'https://'},
    {'modelscope_endpoint': 'https://user:password@modelscope.cn'},
    {'model_directory': ''}, {'model_directory': None},
    {'autostart_profile': 'missing-profile'},
])
def test_settings_reject_invalid_values_without_changing_data(client, payload):
    before = db.settings()
    client._transport.raise_server_exceptions = False
    response = client.put('/api/settings', json=payload)
    assert response.status_code in (400, 422), response.text
    assert db.settings() == before


def test_settings_restart_required_only_when_listener_changes(client):
    before = db.settings()
    result = client.put('/api/settings', json={'port': before['port']}).json()
    assert not result['restart_required']
    result = client.put('/api/settings', json={'port': before['port'] + 1}).json()
    assert result['restart_required']


def test_worker_spawn_failure_releases_job(client, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('Cannot create worker process')
    monkeypatch.setattr(jobs.subprocess, 'Popen', fail)
    with pytest.raises((ValueError, OSError)):
        jobs.create_job('download_model', {'repo_id': 'test/model'})
    record = db.all_records('jobs')[0]
    assert record['state'] == 'failed'
    assert 'Cannot create worker' in record['error']


def test_old_job_without_pid_is_reconciled():
    db.put('jobs', 'old', {'id': 'old', 'kind': 'start_model', 'state': 'queued', 'created_at': time.time() - 300})
    db.put('jobs', 'new', {'id': 'new', 'kind': 'start_model', 'state': 'queued', 'created_at': time.time()})
    jobs.list_jobs()
    assert db.get('jobs', 'old')['state'] == 'failed'
    assert db.get('jobs', 'new')['state'] == 'queued'


@pytest.mark.parametrize('messages', [
    [None], [{'role': 'assistant', 'content': [{'type': 'text', 'text': {'bad': 1}}]}],
    [{'role': 'assistant', 'content': [{'type': 'reasoning', 'text': 12}]}],
    [{'role': 'unsupported', 'content': 'lost'}],
    [{'role': 'user', 'content': 'valid'}, {'role': 'assistant', 'content': 12}],
])
def test_chat_import_rejects_corrupt_content_atomically(client, messages):
    before = client.get('/api/chat/threads').json()['items']
    client._transport.raise_server_exceptions = False
    response = client.post('/api/chat/import', json={'messages': messages})
    assert response.status_code == 400, response.text
    assert client.get('/api/chat/threads').json()['items'] == before


def test_failed_image_import_does_not_leave_files(client, state):
    import base64
    stream = io.BytesIO()
    Image.new('RGB', (2, 2)).save(stream, format='PNG')
    data = 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()
    response = client.post('/api/chat/import', json={
        'messages': [{'role': 'user', 'content': [{'type': 'image', 'attachment_id': 'a'}, {'type': 'image', 'attachment_id': 'b'}]}],
        'attachments': {'a': {'name': 'valid.png', 'data_url': data}},
    })
    assert response.status_code == 400
    assert not db.all_records('attachments')
    assert not list((state / 'attachments').glob('*'))


def test_duplicate_message_ids_are_rejected(client):
    thread = client.post('/api/chat/threads', json={}).json()['id']
    message = {'id': 'duplicate', 'role': 'user', 'content': [{'type': 'text', 'text': 'one'}]}
    response = client.put(f'/api/chat/threads/{thread}/messages', json={'messages': [message, {**message, 'role': 'assistant'}]})
    assert response.status_code == 400
    assert client.get(f'/api/chat/threads/{thread}').json()['messages'] == []


def test_same_second_backups_are_distinct(client, monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, 'time', lambda: now)
    first = client.post('/api/backup').json()['filename']
    second = client.post('/api/backup').json()['filename']
    assert first != second
    assert client.get('/api/backup/' + first).status_code == 200
    assert client.get('/api/backup/' + second).status_code == 200


def test_profile_deletion_clears_autostart_and_rejects_queued_use(client, monkeypatch):
    from onecat import engine
    monkeypatch.setattr(engine, 'status', lambda: {'state': 'stopped'})
    db.put('profiles', 'p', {'id': 'p'})
    db.put('settings', 'main', {**db.settings(), 'autostart_profile': 'p'})
    db.put('jobs', 'queued-start', {'id': 'queued-start', 'state': 'queued', 'kind': 'start_model', 'payload': {'profile_id': 'p'}, 'created_at': time.time()})
    assert client.delete('/api/profiles/p').status_code == 409
    assert db.get('profiles', 'p')
    db.patch('jobs', 'queued-start', {'state': 'cancelled'})
    assert client.delete('/api/profiles/p').status_code == 200
    assert db.settings()['autostart_profile'] is None


def test_updating_missing_thread_returns_not_found(client):
    assert client.patch('/api/chat/threads/missing', json={'title': 'Rename'}).status_code == 404
    assert client.patch('/api/chat/threads/missing', json={'pinned': True}).status_code == 404
    assert not db.get('chat_pins', 'missing')


def test_legacy_power_modes_require_authorization_and_recorded_clock_policy(client, monkeypatch):
    from onecat import engine, gpu, power_modes
    device = {'uuid': 'gpu', 'name': 'V100', 'power_limit_w': 300}
    monkeypatch.setattr(gpu, 'selected_devices', lambda ids: [device] if ids else [])
    monkeypatch.setattr(gpu, 'validate_setting', lambda *args: None)
    state = {'state': 'stopped', 'profile': {'gpu_uuids': ['gpu']}}
    monkeypatch.setattr(engine, 'status', lambda: state)
    assert client.get('/api/inference/power-modes').json()['active'] is None
    assert not any(i['available'] for i in client.get('/api/inference/power-modes').json()['items'])
    state['state'] = 'ready'
    assert power_modes.options(['gpu'])['active'] is None
    db.put('hardware', 'gpu', {'graphics_clock_mhz': None, 'reset_clocks': True})
    assert power_modes.options(['gpu'])['active'] == 'performance'
