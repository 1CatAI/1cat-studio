import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def helper(tmp_path, monkeypatch):
    file = Path(__file__).resolve().parents[2] / "scripts/onecat-gpu-helper.py"
    spec = importlib.util.spec_from_file_location("gpu_helper", file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    uuid = "GPU-11111111-1111-1111-1111-111111111111"
    other = "GPU-22222222-2222-2222-2222-222222222222"
    config = tmp_path / "config"
    config.write_text(json.dumps({"users": {"tester": {"mode": "uuids", "uuids": [uuid]}}}))

    class Config:
        def stat(self):
            return type("Stat", (), {"st_uid": 0, "st_mode": 0o644})()

        def read_text(self):
            return config.read_text()

    monkeypatch.setattr(module, "CONFIG", Config())
    monkeypatch.setattr(module, "STATE", tmp_path / "ledger")
    original_stat = Path.stat

    def stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == module.STATE:
            values = list(value)
            values[4] = 0
            import os

            return os.stat_result(values)
        return value

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(module, "boot_id", lambda: "test-boot")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "tester")
    monkeypatch.setattr(
        module,
        "device",
        lambda u: {
            "minimum": 100,
            "maximum": 300,
            "default": 300,
            "power_limit_w": 250,
            "memory_mhz": 877,
        },
    )
    monkeypatch.setattr(module, "clocks", lambda *a: {930, 975})
    monkeypatch.setattr(
        module,
        "gpu_inventory",
        lambda: {
            uuid: {"compute_capability": [7, 0], "memory_mib": 16384, "manageable": True},
            other: {"compute_capability": [7, 0], "memory_mib": 16384, "manageable": True},
        },
    )
    return module, uuid


def invoke(module, monkeypatch, request):
    import io

    monkeypatch.setattr(sys, "argv", ["helper", "apply"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    module.main()


def test_helper_requires_explicit_clock_initialization(helper, monkeypatch):
    module, uuid = helper
    calls = []
    state = module.device(uuid)
    monkeypatch.setattr(module, "device", lambda u: state)

    def apply(u, setting):
        calls.append((u, setting))
        if setting.get("power_limit_w") is not None:
            state["power_limit_w"] = setting["power_limit_w"]

    monkeypatch.setattr(module, "set_device", apply)
    with pytest.raises(ValueError, match="unknown"):
        invoke(module, monkeypatch, {"uuids": [uuid], "setting": {"graphics_clock_mhz": 975}})
    assert not calls
    invoke(module, monkeypatch, {"uuids": [uuid], "setting": {"reset_clocks": True}})
    invoke(
        module,
        monkeypatch,
        {"uuids": [uuid], "setting": {"graphics_clock_mhz": 975, "power_limit_w": 280}},
    )
    assert len(calls) == 2
    with pytest.raises(ValueError, match="outside"):
        invoke(module, monkeypatch, {"uuids": [uuid], "setting": {"power_limit_w": 999}})
    assert len(calls) == 2


def test_helper_restores_after_partial_failure(helper, monkeypatch):
    module, uuid = helper
    module.STATE.mkdir(mode=0o755)
    (module.STATE / "clock-policies.json").write_text(
        json.dumps({"boot_id": "test-boot", "devices": {uuid: {"graphics_clock_mhz": 930}}})
    )
    calls = []

    def apply(u, setting):
        calls.append(setting)
        if len(calls) == 1:
            raise RuntimeError("driver rejected second operation")

    monkeypatch.setattr(module, "set_device", apply)
    with pytest.raises(ValueError):
        invoke(
            module,
            monkeypatch,
            {"uuids": [uuid], "setting": {"graphics_clock_mhz": 975, "power_limit_w": 280}},
        )
    assert calls[1]["graphics_clock_mhz"] == 930 and calls[1]["power_limit_w"] == 250


def test_helper_initializes_unknown_and_marks_boot(helper, monkeypatch, capsys):
    module, uuid = helper
    calls = []
    monkeypatch.setattr(module, "set_device", lambda u, setting: calls.append(setting))
    invoke(
        module,
        monkeypatch,
        {"uuids": [uuid], "setting": {"graphics_clock_mhz": 975}, "initialize_unknown": True},
    )
    assert calls == [{"reset_clocks": True}, {"graphics_clock_mhz": 975}]
    value = json.loads((module.STATE / "clock-policies.json").read_text())
    assert value["boot_id"] == "test-boot"
    assert value["devices"][uuid]["graphics_clock_mhz"] == 975
    monkeypatch.setattr(module, "boot_id", lambda: "new-boot")
    import io

    monkeypatch.setattr(sys, "argv", ["helper", "snapshot"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"uuids": [uuid]})))
    capsys.readouterr()
    module.main()
    assert not json.loads(capsys.readouterr().out)[uuid]["clock_policy_known"]


def test_helper_reports_inexact_restore_and_failed_recovery(helper, monkeypatch):
    module, uuid = helper
    calls = []

    def apply(u, setting):
        calls.append(setting)
        if len(calls) == 2:
            raise RuntimeError("Cannot lock clock")

    monkeypatch.setattr(module, "set_device", apply)
    with pytest.raises(module.ControlError) as error:
        invoke(
            module,
            monkeypatch,
            {"uuids": [uuid], "setting": {"graphics_clock_mhz": 975}, "initialize_unknown": True},
        )
    assert error.value.result["devices"][uuid] == {"state": "restored", "restored_exact": False}
    assert not (module.STATE / "pending-control.json").exists()

    def broken(*args):
        raise RuntimeError("Driver unavailable")

    monkeypatch.setattr(module, "set_device", broken)
    with pytest.raises(module.ControlError) as error:
        invoke(
            module,
            monkeypatch,
            {"uuids": [uuid], "setting": {"graphics_clock_mhz": 975}, "initialize_unknown": True},
        )
    assert error.value.result["devices"][uuid]["state"] == "restore_failed"
    assert (module.STATE / "pending-control.json").exists()
    monkeypatch.setattr(module, "set_device", lambda *args: None)
    invoke(module, monkeypatch, {"uuids": [uuid], "recover": True})
    assert not (module.STATE / "pending-control.json").exists()


def test_all_devices_preflight_before_first_mutation(helper, monkeypatch):
    module, u1 = helper
    u2 = u1.replace("1", "2")
    monkeypatch.setattr(
        module.CONFIG, "read_text", lambda: json.dumps({"users": {"tester": [u1, u2]}})
    )
    monkeypatch.setattr(module, "clocks", lambda u, memory: {975} if u == u1 else {930})
    calls = []
    monkeypatch.setattr(module, "set_device", lambda *args: calls.append(args))
    with pytest.raises(ValueError, match="unsupported"):
        invoke(
            module,
            monkeypatch,
            {"uuids": [u1, u2], "setting": {"graphics_clock_mhz": 975}, "initialize_unknown": True},
        )
    assert calls == []
    assert not (module.STATE / "pending-control.json").exists()


def test_multi_gpu_failure_restores_exact_snapshot(helper, monkeypatch):
    module, u1 = helper
    u2 = u1.replace("1", "2")
    monkeypatch.setattr(
        module.CONFIG, "read_text", lambda: json.dumps({"users": {"tester": [u1, u2]}})
    )
    module.STATE.mkdir(mode=0o755)
    (module.STATE / "clock-policies.json").write_text(
        json.dumps(
            {
                "boot_id": "test-boot",
                "devices": {u1: {"graphics_clock_mhz": 930}, u2: {"graphics_clock_mhz": None}},
            }
        )
    )
    before = {u1: module.device(u1), u2: {**module.device(u2), "power_limit_w": 300}}
    import copy

    current = copy.deepcopy(before)
    monkeypatch.setattr(module, "device", lambda u: current[u])

    def apply(u, value):
        if u == u2 and value.get("power_limit_w") == 185:
            raise ValueError("Second GPU rejected")
        if value.get("power_limit_w") is not None:
            current[u]["power_limit_w"] = value["power_limit_w"]

    monkeypatch.setattr(module, "set_device", apply)
    with pytest.raises(module.ControlError) as error:
        invoke(
            module,
            monkeypatch,
            {"uuids": [u1, u2], "setting": {"power_limit_w": 185, "reset_clocks": True}},
        )
    assert current == before
    assert all(
        v == {"state": "restored", "restored_exact": True}
        for v in error.value.result["devices"].values()
    )
    ledger = json.loads((module.STATE / "clock-policies.json").read_text())
    assert ledger["devices"][u1]["graphics_clock_mhz"] == 930
    assert not (module.STATE / "pending-control.json").exists()


def test_snapshot_metadata_is_opt_in_for_older_studio(helper, monkeypatch, capsys):
    module, uuid = helper
    import io

    for detailed in (False, True):
        monkeypatch.setattr(sys, "argv", ["helper", "snapshot"])
        monkeypatch.setattr(
            sys, "stdin", io.StringIO(json.dumps({"uuids": [uuid], "details": detailed}))
        )
        module.main()
        value = json.loads(capsys.readouterr().out)[uuid]
        assert ("boot_id" in value) == detailed
        assert ("recovery_pending" in value) == detailed
        assert value["clock_policy_known"] is False


def check(module, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["helper", "check"])
    capsys.readouterr()
    module.main()
    return json.loads(capsys.readouterr().out)


def test_class_binding_follows_replacement_cards(helper, monkeypatch, capsys):
    """The whole point of a class binding: new serials keep working unbound."""
    module, _ = helper
    monkeypatch.setattr(
        module.CONFIG,
        "read_text",
        lambda: json.dumps({"users": {"tester": {"mode": "class", "compute_capability": [[7, 0]]}}}),
    )
    fresh = "GPU-33333333-3333-3333-3333-333333333333"
    monkeypatch.setattr(
        module,
        "gpu_inventory",
        lambda: {
            fresh: {"compute_capability": [7, 0], "memory_mib": 32768, "manageable": True},
            "GPU-44444444-4444-4444-4444-444444444444": {
                "compute_capability": [6, 1],
                "memory_mib": 2048,
                "manageable": False,
            },
        },
    )
    value = check(module, monkeypatch, capsys)
    assert value["available"] and value["protocol"] == 3 and value["mode"] == "class"
    assert value["allowed_uuids"] == [fresh]
    assert value["missing_uuids"] == []


def test_check_reports_an_unbound_machine_instead_of_failing(helper, monkeypatch, capsys):
    module, uuid = helper
    monkeypatch.setattr(module.CONFIG, "read_text", lambda: json.dumps({"users": {}}))
    value = check(module, monkeypatch, capsys)
    assert not value["available"] and value["protocol"] == 3
    assert "no GPU binding policy" in value["reason"]


def test_replaced_pinned_cards_are_reported_as_absent(helper, monkeypatch, capsys):
    module, uuid = helper
    monkeypatch.setattr(
        module,
        "gpu_inventory",
        lambda: {"GPU-99999999-9999-9999-9999-999999999999": {
            "compute_capability": [7, 0], "memory_mib": 32768, "manageable": True}},
    )
    value = check(module, monkeypatch, capsys)
    assert not value["available"] and value["missing_uuids"] == [uuid]
    assert "absent from this host" in value["reason"]
    # Hardware control must stay refused while the pin no longer resolves.
    monkeypatch.setattr(sys, "argv", ["helper", "snapshot"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"uuids": [uuid]})))
    with pytest.raises(ValueError, match="absent from this host"):
        module.main()


def test_unmanageable_boards_are_never_bound(helper, monkeypatch, capsys):
    module, uuid = helper
    monkeypatch.setattr(
        module.CONFIG,
        "read_text",
        lambda: json.dumps({"users": {"tester": {"mode": "all"}}}),
    )
    monkeypatch.setattr(
        module,
        "gpu_inventory",
        lambda: {
            uuid: {"compute_capability": [6, 1], "memory_mib": 2048, "manageable": False},
        },
    )
    value = check(module, monkeypatch, capsys)
    assert not value["available"] and value["allowed_uuids"] == []
    assert "controllable power limit" in value["reason"]
