# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import json

import pytest
from fastapi.testclient import TestClient
from onecat import app


@pytest.fixture
def releases(tmp_path, monkeypatch):
    releases = tmp_path / "app/releases"
    old, current = [releases / n / "studio/frontend/dist" for n in ("old", "current")]
    for dist in (old, current):
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<title>Studio</title>")
        (dist.parents[2] / "manifest.json").write_text(
            json.dumps({"format": "onecat-studio-linux-v1"})
        )
    monkeypatch.setattr(app, "frontend_dist", lambda: current)
    return TestClient(app.create_app()), old, current


def test_open_tab_can_import_previous_release_chunk(releases):
    client, old, current = releases
    name = "assets/preview-panel-D68Phd6k.js"
    (old / name).write_text("export const PreviewPanel = true;")
    response = client.get("/" + name)
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.text == "export const PreviewPanel = true;"
    assert "immutable" in response.headers["cache-control"]
    (current / name).write_text('export const PreviewPanel = "current";')
    assert "current" in client.get("/" + name).text


@pytest.mark.parametrize(
    "path",
    [
        "assets/missing-AbCdEf12.js",
        "preview/runtime.js",
        "assets/manifest.json",
        "assets/unhashed.js",
        "missing-root.js",
    ],
)
def test_missing_assets_never_receive_spa_html(releases, path):
    client, old, _ = releases
    (old / "assets/manifest.json").write_text("private")
    assert client.get("/" + path).status_code == 404
    assert client.get("/chat").status_code == 200


def test_retired_source_and_symlinks_are_not_public(releases, tmp_path):
    client, old, _ = releases
    (old / "source.tar.gz").write_bytes(b"private source")
    secret = tmp_path / "secret"
    secret.write_text("private secret")
    (old / "assets/escape-AbCdEf12.js").symlink_to(secret)
    (old / "assets/source-AbCdEf12.js").symlink_to(old / "source.tar.gz")
    assert client.get("/assets/source-AbCdEf12.js").status_code == 404
    assert client.get("/assets/escape-AbCdEf12.js").status_code == 404
    assert client.get("/assets/../source.tar.gz").status_code == 401
    assert b"private" not in client.get("/assets/%2e%2e/%2e%2e/secret").content
