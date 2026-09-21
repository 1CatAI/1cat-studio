"""Public deployments serve corresponding source only to Studio administrators."""

import pytest
from fastapi.testclient import TestClient
from onecat import app, auth


@pytest.fixture
def browser(state, monkeypatch, tmp_path):
    dist = tmp_path / "frontend"
    dist.mkdir()
    (dist / "index.html").write_text("<title>1Cat Studio</title>")
    (dist / "app.js").write_text("export const application = '1cat';")
    (dist / "source.tar.gz").write_bytes(b"private corresponding source")
    monkeypatch.setattr(app, "frontend_dist", lambda: dist)
    # Do not run application lifespan, hardware polling or recovery.
    return TestClient(app.create_app()), dist


@pytest.mark.parametrize(
    "path",
    ["/source.tar.gz", "/%73ource.tar.gz", "/%2e/source.tar.gz", "/assets/%2e%2e/source.tar.gz"],
)
def test_anonymous_source_and_normalized_aliases_require_login(browser, path):
    client, _ = browser
    response = client.get(path, headers={"Range": "bytes=0-0"})
    assert response.status_code == 401
    assert b"private corresponding source" not in response.content


def test_admin_download_supports_ranges_and_logout_revokes_access(browser):
    client, _ = browser
    session = auth.issue("source-test", "admin")
    client.cookies.set(auth.COOKIE, session)
    response = client.get("/source.tar.gz", headers={"Range": "bytes=0-6"})
    assert response.status_code == 206
    assert response.content == b"private"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Vary"] == "Cookie, Authorization"
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/source.tar.gz").status_code == 401


def test_inference_key_cannot_download_source(browser):
    client, _ = browser
    key = auth.issue("inference-test", "inference")
    assert (
        client.get("/source.tar.gz", headers={"Authorization": "Bearer " + key}).status_code == 403
    )


def test_missing_archive_is_not_replaced_with_the_application(browser):
    client, dist = browser
    (dist / "source.tar.gz").unlink()
    session = auth.issue("source-test", "admin")
    assert (
        client.get("/source.tar.gz", headers={"Authorization": "Bearer " + session}).status_code
        == 404
    )


def test_application_and_public_assets_remain_available(browser):
    client, _ = browser
    assert "1Cat Studio" in client.get("/chat").text
    assert client.get("/app.js").status_code == 200
