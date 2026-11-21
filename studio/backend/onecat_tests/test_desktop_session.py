"""The native Agent session belongs to the desktop listener that consumed its ticket."""
from fastapi.testclient import TestClient

from onecat.agent import desktop


def test_desktop_ticket_and_project_visibility(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ONECAT_LOCAL_DESKTOP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    token = desktop.issue_ticket()
    assert client.post("/desktop-activate", content=token).status_code == 404

    with TestClient(client.app, base_url=desktop.ORIGIN) as local:
        assert local.post("/desktop-activate", content=token).status_code == 403
        headers = {"Origin": desktop.ORIGIN}
        assert local.post("/desktop-activate", headers=headers, content=token).status_code == 204
        assert local.post("/desktop-activate", headers=headers, content=token).status_code == 403
        project = local.post("/api/agent/projects", json={"name": "Desktop test"})
        assert project.status_code == 200, project.text
        project_id = project.json()["id"]
        assert desktop.private_id(project_id)
        assert local.get(f"/api/agent/projects/{project_id}/files").status_code == 200
        assert any(p["id"] == project_id for p in local.get("/api/agent/projects").json()["items"])

    assert all(p["id"] != project_id for p in client.get("/api/agent/projects").json()["items"])
    assert client.get(f"/api/agent/projects/{project_id}/files").status_code == 404
