# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import copy

import pytest
from onecat import catalog, db, gpu, portable_gpu
from onecat.schemas import Profile

OLD = [f"GPU-{i:08x}-0000-0000-0000-000000000000" for i in range(4)]
NEW = [f"GPU-{i:08x}-1111-1111-1111-111111111111" for i in range(4)]


@pytest.fixture
def devices(monkeypatch):
    value = [
        {
            "uuid": uuid,
            "index": i,
            "compute_capability": [7, 0],
            "memory_total_mib": 16384,
            "processes": [],
        }
        for i, uuid in enumerate(NEW)
    ]
    monkeypatch.setattr(gpu, "snapshot", lambda: {"gpus": copy.deepcopy(value)})
    return value


@pytest.fixture
def profile(tmp_path, monkeypatch):
    folder = tmp_path / "model"
    folder.mkdir()
    (folder / "config.json").write_text("{}")
    (folder / "model.safetensors").write_bytes(b"test weights")
    db.put("runtimes", "runtime", {"id": "runtime", "validated": True})
    value = Profile(
        id="portable",
        name="Portable",
        model_path=str(folder),
        runtime_id="runtime",
        gpu_uuids=OLD,
        tensor_parallel_size=4,
        portable_gpu_binding=True,
    ).model_dump()
    db.put("profiles", value["id"], value)
    monkeypatch.setattr(catalog, "capabilities", lambda *_: {})
    return value


def test_driver_appears_after_prestart(devices, profile, monkeypatch):
    states = iter([[], devices[:2], devices])
    monkeypatch.setattr(gpu, "snapshot", lambda: {"gpus": next(states)})
    now = [0.0]
    monkeypatch.setattr(portable_gpu.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        portable_gpu.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    assert portable_gpu.rebind(wait_seconds=60) == NEW
    assert now[0] == 2
    assert db.get("profiles", "portable") == {**profile, "gpu_uuids": NEW}


def test_timeout_is_bounded_then_model_validation_recovers(devices, profile, monkeypatch):
    monkeypatch.setattr(gpu, "snapshot", lambda: {"gpus": []})
    now = [0.0]
    monkeypatch.setattr(portable_gpu.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        portable_gpu.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    assert portable_gpu.rebind(wait_seconds=3) == []
    assert now[0] == 3
    assert db.get("profiles", "portable") == profile
    monkeypatch.setattr(gpu, "snapshot", lambda: {"gpus": devices})
    candidate = copy.deepcopy(profile)
    catalog.validate_features(candidate)
    assert candidate["gpu_uuids"] == NEW
    assert db.get("profiles", "portable") == {**profile, "gpu_uuids": NEW}


def test_manual_profile_stays_pinned(devices, profile):
    profile["portable_gpu_binding"] = False
    db.put("profiles", "portable", profile)
    assert not portable_gpu.rebind_profile(profile)
    assert db.get("profiles", "portable") == profile
    with pytest.raises(ValueError, match="Selected GPUs are not present"):
        catalog.validate_features(profile)


def test_preserves_valid_order_and_does_not_rewrite(devices, profile):
    profile["gpu_uuids"] = list(reversed(NEW))
    db.put("profiles", "portable", profile)
    with db.connect() as conn:
        before = conn.execute("SELECT updated FROM records WHERE bucket='profiles'").fetchone()[0]
    assert portable_gpu.rebind() == NEW
    assert db.get("profiles", "portable") == profile
    with db.connect() as conn:
        after = conn.execute("SELECT updated FROM records WHERE bucket='profiles'").fetchone()[0]
    assert before == after


@pytest.mark.parametrize(
    "kind", ["two", "eight", "small", "architecture", "missing_uuid", "duplicate_uuid"]
)
def test_unsupported_topology_is_not_applied(devices, profile, monkeypatch, kind):
    if kind == "two":
        devices = devices[:2]
    elif kind == "eight":
        devices = devices + [{**d, "uuid": OLD[i]} for i, d in enumerate(devices)]
    elif kind == "small":
        devices[0]["memory_total_mib"] = 8192
    elif kind == "architecture":
        devices[0]["compute_capability"] = [8, 0]
    elif kind == "missing_uuid":
        devices[0]["uuid"] = None
    else:
        devices[0]["uuid"] = devices[1]["uuid"]
    monkeypatch.setattr(gpu, "snapshot", lambda: {"gpus": devices})
    assert portable_gpu.rebind() == []
    assert not portable_gpu.rebind_profile(profile)
    assert db.get("profiles", "portable") == profile


def test_display_gpu_excluded(devices, profile):
    devices.append({"uuid": "GPU-display", "compute_capability": [8, 6], "memory_total_mib": 24576})
    assert portable_gpu.rebind() == NEW
    assert db.get("profiles", "portable")["gpu_uuids"] == NEW


def test_does_not_convert_two_card_profile_to_four(devices, profile):
    profile.update(tensor_parallel_size=2, gpu_uuids=OLD[:2])
    db.put("profiles", "portable", profile)
    assert portable_gpu.rebind() == NEW
    assert db.get("profiles", "portable") == profile


@pytest.mark.parametrize("already_rebound", [False, True])
def test_stale_browser_payload_recovers_on_save(devices, profile, client, already_rebound):
    if already_rebound:
        assert portable_gpu.rebind() == NEW
    response = client.post("/api/profiles", json=profile)
    assert response.status_code == 200, response.text
    assert response.json() == {**profile, "gpu_uuids": NEW}


def test_default_model_load_recovers_even_when_prestart_missed(
    devices, profile, client, monkeypatch
):
    from onecat import app
    from onecat.creative import services

    monkeypatch.setattr(services, "owned_pids", lambda: set())
    db.put(
        "models",
        "model",
        {
            "id": "model",
            "name": "Model",
            "state": "downloaded",
            "path": profile["model_path"],
            "default_profile_id": profile["id"],
        },
    )
    monkeypatch.setattr(app, "scheduled_job", lambda kind, payload: {"kind": kind, **payload})
    response = client.post("/api/inference/load-model", json={"model_id": "model"})
    assert response.status_code == 200, response.text
    assert response.json()["profile_id"] == profile["id"]
    assert db.get("profiles", "portable")["gpu_uuids"] == NEW
