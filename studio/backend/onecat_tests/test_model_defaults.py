# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from onecat import db, engine, model_controls, models


def test_model_default_is_persisted_and_used_without_restarting(client, tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    db.put("models", "model", {"id": "model", "name": "Model", "state": "downloaded", "path": str(model)})
    for name, tools in (("current", True), ("preferred", False), ("foreign", True)):
        db.put("profiles", name, {"id": name, "name": name, "model_path": str(model if name != "foreign" else tmp_path / "other"), "tool_calling": tools})
    active = {"state": "ready", "profile_id": "current", "profile": db.get("profiles", "current")}
    db.put("engine", "active", active)
    monkeypatch.setattr(engine, "validate_profile", lambda profile: ({}, {}))

    assert client.put("/api/models/model/default-profile", json={"profile_id": "preferred"}).status_code == 200
    assert db.get("engine", "active") == active
    assert db.get("models", "model")["default_profile_id"] == "preferred"
    (model / "weights.safetensors").write_bytes(b"fixture")
    assert models.inspect_model(str(model))["default_profile_id"] == "preferred"
    assert model_controls.resolve_profile("model")["id"] == "preferred"
    # Agent still requires tools, even when the saved default has tools disabled.
    assert model_controls.resolve_profile("model", agent=True)["id"] == "current"

    for invalid in ("foreign", "missing", None):
        assert client.put("/api/models/model/default-profile", json={"profile_id": invalid}).status_code == 409
        assert db.get("models", "model")["default_profile_id"] == "preferred"
    assert client.put("/api/models/missing/default-profile", json={"profile_id": "current"}).status_code == 404

    db.delete("profiles", "preferred")
    assert model_controls.resolve_profile("model")["id"] == "current"


def test_model_default_requires_admin(client):
    client.cookies.clear()
    assert client.put("/api/models/missing/default-profile", json={"profile_id": "p"}).status_code == 401
