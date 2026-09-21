import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("ONECAT_STUDIO_HOME", str(tmp_path / "state"))
    from onecat import auth, gpu_setup

    # Unit tests never authorize or contact the host GPU control helper.
    monkeypatch.setattr(gpu_setup, "helper_check", lambda: {})
    # Nor do they read the host's real binding policy.
    monkeypatch.setattr(gpu_setup, "POLICY", tmp_path / "gpu-helper.json")
    from onecat.config import initialize_paths
    auth._attempts.clear()
    return initialize_paths()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from onecat.app import create_app

    with TestClient(create_app()) as client:
        assert (
            client.post("/api/auth/setup", json={"password": "test-only-password"}).status_code
            == 200
        )
        yield client
