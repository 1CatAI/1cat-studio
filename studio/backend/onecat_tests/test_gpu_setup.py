import ast
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from onecat import db, gpu_setup

U1 = "GPU-11111111-1111-1111-1111-111111111111"
U2 = "GPU-22222222-2222-2222-2222-222222222222"
U3 = "GPU-33333333-3333-3333-3333-333333333333"


def board(uuid, capability=(7, 0), memory=32768, manageable=True, name="Tesla V100-SXM2-32GB"):
    return {
        "uuid": uuid,
        "compute_capability": list(capability),
        "memory_mib": memory,
        "manageable": manageable,
        "name": name,
    }


@pytest.fixture
def installer(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/configure-gpu-helper.py"
    spec = importlib.util.spec_from_file_location("configure_gpu", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("HELPER", "CONFIG", "SUDOERS"):
        monkeypatch.setattr(module, name, tmp_path / name.lower())
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=1000))
    monkeypatch.setattr(module, "detected_gpus", lambda: [board(U1), board(U2)])
    monkeypatch.setattr(module, "trusted_directory", lambda path: path.mkdir(exist_ok=True))
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.delenv("PKEXEC_UID", raising=False)
    # The fixture runs unprivileged; emulate root ownership of a policy it wrote.
    original_stat = Path.stat

    def stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == module.CONFIG:
            return SimpleNamespace(st_uid=0, st_mode=value.st_mode)
        return value

    monkeypatch.setattr(Path, "stat", stat)
    return module


def test_preinstall_binds_a_compute_class_not_this_machine(installer):
    result = installer.configure("tester", [])
    assert result["allowed_uuids"] == [U1, U2]
    assert result["clock_policy_changed"] is False
    # The whole point: nothing machine-specific is written, so the same package
    # installs everywhere and re-binds itself when the cards change.
    assert json.loads(installer.CONFIG.read_text())["users"] == {
        "tester": {"mode": "class", "compute_capability": [[7, 0]]}
    }
    rules = installer.SUDOERS.read_text()
    assert " check," in rules and " snapshot," in rules and rules.endswith(" apply\n")
    assert "ALL=(root) NOPASSWD: ALL" not in rules
    assert installer.HELPER.read_bytes() == installer.SOURCE.read_bytes()
    assert installer.SUDOERS.stat().st_mode & 0o777 == 0o440


def test_class_binding_rebinds_after_a_card_swap(installer, monkeypatch):
    installer.configure("tester", [])
    monkeypatch.setattr(installer, "detected_gpus", lambda: [board(U3, memory=16384)])
    result = installer.configure("tester", [])
    assert result["allowed_uuids"] == [U3]


def test_unmanageable_boards_are_left_out_of_the_binding(installer, monkeypatch):
    display = board(U3, capability=(6, 1), memory=2048, manageable=False, name="Quadro P400")
    monkeypatch.setattr(installer, "detected_gpus", lambda: [board(U1), display])
    result = installer.configure("tester", [])
    assert result["allowed_uuids"] == [U1]


def test_rebind_replaces_an_explicit_pin(installer):
    installer.configure("tester", [U1])
    assert installer.configure("tester", [U2])["allowed_uuids"] == [U1]
    assert installer.configure("tester", [], rebind=True)["allowed_uuids"] == [U1, U2]


def test_upgrade_preserves_restricted_allowlists(installer):
    installer.CONFIG.write_text(json.dumps({"users": {"tester": [U1], "other": [U2]}}))
    installer.CONFIG.chmod(0o644)
    assert installer.configure("tester", [U1, U2])["allowed_uuids"] == [U1]
    assert json.loads(installer.CONFIG.read_text())["users"]["other"] == [U2]


def test_install_rejects_unknown_uuid_before_writes(installer):
    with pytest.raises(ValueError, match="present"):
        installer.configure("tester", ["GPU-unknown"])
    assert not installer.CONFIG.exists()
    assert not installer.HELPER.exists()


def test_failed_sudoers_validation_installs_nothing(installer, monkeypatch):
    def reject(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "visudo")

    monkeypatch.setattr(installer.subprocess, "run", reject)
    with pytest.raises(subprocess.CalledProcessError):
        installer.configure("tester", [])
    assert not installer.CONFIG.exists()
    assert not installer.HELPER.exists()


def test_partial_install_rolls_back(installer, monkeypatch):
    replace = installer.replace_file

    def fail_policy(path, data, mode):
        if path == installer.SUDOERS:
            raise OSError("disk full")
        replace(path, data, mode)

    monkeypatch.setattr(installer, "replace_file", fail_policy)
    with pytest.raises(OSError, match="disk full"):
        installer.configure("tester", [])
    assert not installer.HELPER.exists()
    assert not installer.CONFIG.exists()


def test_installer_rejects_symlinked_target(installer, tmp_path):
    target = tmp_path / "unrelated"
    target.write_text("unchanged")
    installer.HELPER.symlink_to(target)
    with pytest.raises(ValueError, match="symlinks"):
        installer.configure("tester", [])
    assert target.read_text() == "unchanged"


def test_status_does_not_treat_missing_authorization_as_installed(monkeypatch):
    monkeypatch.setattr(gpu_setup, "helper_check", lambda: {})
    monkeypatch.setattr(gpu_setup.gpu, "snapshot", lambda: {"gpus": [{"uuid": U1}, {"uuid": U2}]})
    status = gpu_setup.status()
    assert status["bundled"] and not status["available"]
    assert status["gpu_count"] == 2 and status["allowed_gpu_count"] == 0
    assert status["stage"] == "unbound" and status["required_protocol"] == 3
    assert status["protocol"] == 0


def test_status_guides_a_replaced_machine_back_to_binding(monkeypatch):
    gpu_setup.POLICY.write_text(json.dumps({"users": {"tester": {"mode": "uuids", "uuids": [U1]}}}))
    monkeypatch.setattr(gpu_setup.getpass, "getuser", lambda: "tester")
    monkeypatch.setattr(gpu_setup.gpu, "snapshot", lambda: {"gpus": [board(U2)]})
    status = gpu_setup.status()
    assert status["hardware_changed"] and status["missing_gpu_count"] == 1
    assert status["stage"] == "changed" and status["binding_mode"] == "uuids"


def test_status_reports_the_step_the_wizard_must_show(monkeypatch):
    monkeypatch.setattr(gpu_setup.gpu, "snapshot", lambda: {"gpus": []})
    assert gpu_setup.status()["stage"] == "no_gpus"
    monkeypatch.setattr(gpu_setup.gpu, "snapshot", lambda: {"gpus": [board(U1, capability=(7, 0))]})
    monkeypatch.setattr(
        gpu_setup, "helper_check", lambda: {"available": True, "protocol": 2, "allowed_uuids": [U1]}
    )
    status = gpu_setup.status()
    assert status["stage"] == "upgrade" and status["upgrade_required"]
    monkeypatch.setattr(
        gpu_setup,
        "helper_check",
        lambda: {
            "available": True,
            "protocol": 3,
            "allowed_uuids": [U1],
            "mode": "class",
            "binding": {"mode": "class", "compute_capability": [[7, 0]]},
        },
    )
    status = gpu_setup.status()
    assert status["stage"] == "ready" and status["binding_mode"] == "class"
    assert status["options"]["compute_classes"] == [
        {"compute_capability": [7, 0], "count": 1, "names": ["Tesla V100-SXM2-32GB"],
         "name": "Tesla V100-SXM2-32GB"}
    ]


def test_binding_request_rejects_anything_the_helper_cannot_enforce(monkeypatch):
    monkeypatch.setattr(gpu_setup.gpu, "snapshot", lambda: {"gpus": [board(U1)]})
    assert gpu_setup.binding_request({}) == {"mode": "class"}
    assert gpu_setup.binding_request({"mode": "all"}) == {"mode": "all"}
    assert gpu_setup.binding_request({"mode": "uuids", "uuids": [U1]}) == {
        "mode": "uuids",
        "uuids": [U1],
    }
    for bad in (
        {"mode": "uuids"},
        {"mode": "uuids", "uuids": [U2]},
        {"mode": "uuids", "uuids": ["GPU-not-a-uuid"]},
        {"mode": "everything"},
        {"mode": "class", "surprise": 1},
    ):
        with pytest.raises(ValueError):
            gpu_setup.binding_request(bad)


def test_setup_api_is_authenticated_and_idempotent(client, monkeypatch):
    monkeypatch.setattr(
        gpu_setup, "status", lambda: {"available": False, "bundled": True, "gpu_count": 2}
    )
    from onecat import app

    created = []

    def job(kind, payload):
        created.append(kind)
        value = {"id": "gpu-install", "kind": kind, "payload": payload, "state": "running"}
        db.put("jobs", value["id"], value)
        return value

    monkeypatch.setattr(app, "create_job", job)
    first = client.post("/api/gpu/control/setup", json={})
    assert first.status_code == 200
    assert client.post("/api/gpu/control/setup", json={}).json()["id"] == first.json()["id"]
    assert created == ["configure_gpu_helper"]
    client.cookies.clear()
    assert client.post("/api/gpu/control/setup", json={}).status_code == 401


def test_no_elevation_when_authorization_is_unavailable(installer, monkeypatch, capsys):
    monkeypatch.setattr(installer.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(installer.sys, "argv", ["configure-gpu-helper.py", "--auto"])
    monkeypatch.setattr(installer.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    monkeypatch.setattr(installer, "SMI", str(installer.SOURCE))
    assert installer.main() == 3
    assert json.loads(capsys.readouterr().out)["state"] == "needs_authorization"
    assert not installer.HELPER.exists()


def test_old_protocol_exposes_failed_upgrade_and_retry(monkeypatch):
    monkeypatch.setattr(gpu_setup, "helper_check", lambda: {
        "available": True, "protocol": 1, "allowed_uuids": [U1],
    })
    monkeypatch.setattr(gpu_setup.gpu, "snapshot", lambda: {"gpus": [{"uuid": U1}]})
    db.put("jobs", "failed-upgrade", {"id": "failed-upgrade", "kind": "configure_gpu_helper",
                                     "state": "failed", "error": "Authorization expired"})
    value = gpu_setup.status()
    assert not value["available"] and value["upgrade_required"]
    assert value["error"] == "Authorization expired" and value["job"] is None


def test_setup_rejects_successful_installer_with_old_protocol(monkeypatch):
    monkeypatch.setattr(gpu_setup, "helper_check", lambda: {"available": True, "protocol": 1})
    monkeypatch.setattr(gpu_setup.subprocess, "Popen", lambda *a, **k: SimpleNamespace(
        poll=lambda: 0, communicate=lambda: (b"", b""), returncode=0, stdout=None, stderr=None,
    ))
    from onecat import gpu_control
    recovered = []
    monkeypatch.setattr(gpu_control, "reconcile_saved", lambda: recovered.append(True))
    job = SimpleNamespace(update=lambda *a: None, payload={})
    with pytest.raises(ValueError, match="update did not complete"):
        gpu_setup.configure(job)
    assert not recovered


@pytest.mark.parametrize("hold,installer_result,recover", [(True, 0, False), (False, 0, True), (False, 3, False)])
def test_bundle_upgrade_installs_helper_without_ignoring_hardware_hold(hold, installer_result, recover):
    # Load the actual post-install function without running installation against
    # the host filesystem, then substitute subprocesses that would elevate/write.
    path = Path(__file__).resolve().parents[2] / "scripts/install.py"
    tree = ast.parse(path.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "configure_bundled_gpu")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=installer_result)

    scope = {"subprocess": SimpleNamespace(run=run)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), scope)
    environment = {"ONECAT_AUTO_GPU_ACTIONS": "0" if hold else "1"}
    scope["configure_bundled_gpu"](Path("/release/env/python"), Path("/release"), environment)
    assert calls[0][0][1:] == ["/release/scripts/configure-gpu-helper.py", "--auto", "--authorize"]
    assert calls[0][1]["env"] == environment
    assert len(calls) == (2 if recover else 1)
    if recover:
        assert calls[1][0][2] == "from onecat.gpu_control import reconcile_saved; reconcile_saved()"
