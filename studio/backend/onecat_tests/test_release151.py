# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import copy
import json
import re
import sys
from types import SimpleNamespace

import pytest
from onecat import engine, runtimes, sm70_profiles
from onecat.runtime_environment import runtime_environment
from onecat.schemas import Profile


def test_release151_is_concrete():
    release = next(row for row in runtimes.RELEASES if row["version"] == "1.5.1")
    assert re.fullmatch(r"[a-f0-9]{64}", release["sha256"])
    assert release["url"].endswith("/v1.5.1/1cat_vllm-1.5.1-cp312-cp312-linux_x86_64.whl")
    assert "TODO" not in json.dumps(release)
    assert release["capabilities"]["prompt_token_details"] is True


def test_environment_filters_host_and_preserves_explicit(monkeypatch):
    for key in (
        "VLLM_HOST_TUNING",
        "PYTHONPATH",
        "ONECAT_VLLM_EXTENSION_DIR",
        "FLASH_QLA_SM70_PREBUILT_EXTENSION_PATH",
    ):
        monkeypatch.setenv(key, "inherited")
    assert not any(key.startswith("VLLM_") for key in runtime_environment({}))
    assert "PYTHONPATH" not in runtime_environment({})
    assert "ONECAT_VLLM_EXTENSION_DIR" not in runtime_environment({})
    assert "FLASH_QLA_SM70_PREBUILT_EXTENSION_PATH" not in runtime_environment({})
    explicit = {"environment": {"FLASH_QLA_SM70_PREBUILT_EXTENSION_PATH": "explicit"}}
    assert runtime_environment(explicit)["FLASH_QLA_SM70_PREBUILT_EXTENSION_PATH"] == "explicit"
    assert (
        runtime_environment({"environment": {"VLLM_EXPLICIT": "1", "PYTHONPATH": "explicit"}})[
            "VLLM_EXPLICIT"
        ]
        == "1"
    )
    assert (
        runtime_environment({"environment": {"PYTHONPATH": "explicit"}})["PYTHONPATH"] == "explicit"
    )
    result = engine.runtime_env(
        {"id": "test", "environment": {"VLLM_EXPLICIT": "1"}}, {"gpu_uuids": []}
    )
    assert "VLLM_HOST_TUNING" not in result
    assert result["VLLM_EXPLICIT"] == "1"


def _profile(runtime):
    return Profile(
        name="test",
        model_path="/target",
        runtime_id="test",
        **sm70_profiles.recommendation(runtime),
        speculative_config=sm70_profiles.default_speculation(runtime, "/draft"),
    ).model_dump()


def _options(argv):
    result = {}
    i = argv.index("--host")
    while i < len(argv):
        token = argv[i]
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            result[token] = argv[i + 1]
            i += 2
        else:
            result[token] = True
            i += 1
    return result


def test_studio_argv_matches_release_profile():
    runtime = {"python_path": "/venv/bin/python", "capabilities": {"prompt_token_details": True}}
    p = _profile(runtime)
    args = _options(engine.build_argv(p, runtime, 8000, None))
    recipe = sm70_profiles.release_profile(runtime)["args"]
    for key, value in recipe.items():
        flag = "--" + key.replace("_", "-")
        actual = args[flag]
        if key == "speculative_config":
            assert json.loads(actual) == sm70_profiles.default_speculation(runtime, "/draft")
        elif key == "limit_mm_per_prompt":
            assert {k: v for k, v in json.loads(actual).items() if k != "audio"} == value
        elif isinstance(value, dict):
            assert json.loads(actual) == value
        elif isinstance(value, bool):
            assert actual == value
        else:
            assert actual == str(value)
    assert "--enable-prompt-tokens-details" in args


def test_prompt_details_capability_beats_version():
    runtime = {
        "python_path": "/python",
        "release": {"version": "1.5.0", "capabilities": {"prompt_token_details": True}},
        "capabilities": {"prompt_token_details": False},
    }
    assert "--enable-prompt-tokens-details" not in engine.build_argv(
        _profile(runtime), runtime, 8000, None
    )
    runtime["capabilities"] = {"prompt_token_details": True}
    runtime["release"]["version"] = "9.9.9"
    assert "--enable-prompt-tokens-details" in engine.build_argv(
        _profile(runtime), runtime, 8000, None
    )


def test_installed_wheel_recipe_preferred():
    recipe = sm70_profiles.release_profile({})
    recipe["args"]["max_num_batched_tokens"] = 12345
    runtime = {"capabilities": {"sm70_profiles": {sm70_profiles.PROFILE_NAME: recipe}}}
    assert sm70_profiles.recommendation(runtime)["max_num_batched_tokens"] == 12345
    assert sm70_profiles.recommendation({})["max_num_batched_tokens"] == 8192


def test_migration_only_offered_for_old_defaults():
    old = _profile({})
    old.update(
        catalog_id="QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4",
        kv_cache_dtype="fp8_e5m2",
        max_num_batched_tokens=4096,
        gpu_memory_utilization=0.92,
        extra_args=[
            "--trust-remote-code",
            "--mamba-cache-mode",
            "align",
            "--reasoning-parser",
            "qwen3",
        ],
    )
    snapshot = copy.deepcopy(old)
    update = sm70_profiles.recommended_update(old, {})
    assert update["max_num_batched_tokens"] == 8192
    assert old == snapshot
    old["max_num_batched_tokens"] = 2048
    assert sm70_profiles.recommended_update(old, {}) is None
    old = snapshot
    old["extra_args"].append("--enforce-eager")
    assert sm70_profiles.recommended_update(old, {}) is None


def test_acceleration_read_is_optional_and_authenticated():
    import httpx

    report = {
        "paths": {
            "long_context": {"enabled": False, "reason": "kv_dtype"},
            "batch_gemm": {"enabled": True, "reason": None},
            "irrelevant": {"enabled": False, "reason": "not_applicable"},
        }
    }
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json=report)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = engine.read_acceleration({"port": 8000, "api_key": "test-key"}, client)
    assert seen == ["Bearer test-key"]
    assert result["enabled"] == 1 and result["total"] == 2
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client:
        assert engine.read_acceleration({"port": 8000, "api_key": "test-key"}, client) is None


def test_catalog_draft_pin_matches_upstream_payload():
    from onecat import catalog

    draft = sm70_profiles.release_profile({})["draft"]
    entry = catalog.entry(draft["repo"])
    assert entry["revision"] == draft["modelscope_revision"]
    assert entry["upstream_revision"] == draft["revision"]
    hashes = {f["path"]: f["sha256"] for f in entry["files"]}
    assert hashes["config.json"] == draft["config_sha256"]
    assert hashes["model.safetensors"] == draft["weights_sha256"]


def test_upgrade_refreshes_existing_runtime_capabilities(monkeypatch):
    from onecat import db

    old = {"id": "old", "validated": True, "capabilities": {"distribution_version": "1.5.0"}}
    complete = {
        "id": "complete",
        "validated": True,
        "capabilities": {
            "distribution_version": "1.5.1",
            "prompt_token_details": False,
            "sm70_profiles": {},
        },
    }
    for row in (old, complete):
        db.put("runtimes", row["id"], row)
    seen = []
    monkeypatch.setattr(runtimes, "inspect_runtime", lambda row: seen.append(row["id"]) or row)
    assert runtimes.refresh_incomplete_metadata() == ["old"]
    assert seen == ["old"]


@pytest.mark.parametrize("error", [FileNotFoundError("recipe missing"), ValueError("invalid JSON")])
def test_inspection_can_fall_back_when_optional_recipe_is_unreadable(
    tmp_path, monkeypatch, capsys, error
):
    root = tmp_path / "vllm"
    (root / "sm70_profiles").mkdir(parents=True)
    (root / "sm70_profiles/profile.py").touch()
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(__version__="2.10.0", version=SimpleNamespace(cuda="12.8")),
    )
    monkeypatch.setitem(
        sys.modules,
        "vllm",
        SimpleNamespace(__file__=str(root / "__init__.py"), __version__="1.5.1"),
    )

    def fail():
        raise error

    monkeypatch.setitem(
        sys.modules,
        "vllm.sm70_profiles",
        SimpleNamespace(PROFILE_NAME=sm70_profiles.PROFILE_NAME, load_profile=fail),
    )
    exec(runtimes.INSPECT, {})  # noqa: S102 - Exercise the repository-owned inspection program.
    result = json.loads(capsys.readouterr().out.split("ONECAT_RUNTIME=", 1)[1])
    assert result["vllm_version"] == "1.5.1"
    assert result["sm70_profiles"] == {}


@pytest.mark.parametrize("bad_recipe", [{"args": None}, {"args": {}}, {"args": {"dtype": "half"}}])
def test_incomplete_wheel_recipe_uses_static_fallback(bad_recipe):
    recipe = {"name": sm70_profiles.PROFILE_NAME, **bad_recipe}
    runtime = {"capabilities": {"sm70_profiles": {sm70_profiles.PROFILE_NAME: recipe}}}
    assert sm70_profiles.recommendation(runtime) == sm70_profiles.recommendation({})


def test_migration_preserves_manual_draft_revision():
    old = _profile({})
    old.update(
        catalog_id="QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4",
        kv_cache_dtype="fp8_e5m2",
        max_num_batched_tokens=4096,
        gpu_memory_utilization=0.92,
        extra_args=[
            "--trust-remote-code",
            "--mamba-cache-mode",
            "align",
            "--reasoning-parser",
            "qwen3",
        ],
    )
    old["speculative_config"]["revision"] = "b" * 40
    assert sm70_profiles.recommended_update(old, {}) is None


def test_acceleration_count_uses_release_expected_paths():
    import httpx

    report = {
        "expected_acceleration": ["dflash2_verifier", "long_context"],
        "paths": {
            "dflash2_verifier": {"enabled": True, "reason": None},
            "long_context": {"enabled": False, "reason": "operator_missing"},
            "compile_cache": {"enabled": False, "reason": "compile_cache_disabled"},
        },
    }
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=report))
    ) as client:
        status = engine.read_acceleration({"port": 8000, "api_key": "key"}, client)
    assert status["enabled"] == 1 and status["total"] == 2
    assert "compile_cache" not in status["paths"]


@pytest.fixture
def qualified_release_models(tmp_path, monkeypatch):
    import hashlib

    from onecat import db, gpu

    recipe = sm70_profiles.release_profile({})
    for role, repo in (
        ("target", "QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4"),
        ("draft", recipe["draft"]["repo"]),
    ):
        folder = tmp_path / role
        folder.mkdir()
        files = {"config.json": b"{}", "model.safetensors": role.encode()}
        for name, data in files.items():
            (folder / name).write_bytes(data)
        manifest = [
            {"path": name, "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in files.items()
        ]
        row = {
            "id": role,
            "path": str(folder),
            "repo_id": repo,
            "catalog_id": repo,
            "verified": True,
            "revision": "master",
            "manifest": manifest,
            "file_identity": {
                name: [(folder / name).stat().st_size, (folder / name).stat().st_mtime_ns]
                for name in files
            },
        }
        db.put("models", role, row)
        if role == "draft":
            recipe["draft"]["config_sha256"] = manifest[0]["sha256"]
            recipe["draft"]["weights_sha256"] = manifest[1]["sha256"]
    monkeypatch.setattr(sm70_profiles, "release_profile", lambda _: copy.deepcopy(recipe))
    runtime = {
        "id": "test",
        "name": "release",
        "validated": True,
        "python_path": "/venv/bin/python",
        "release": {"version": "1.5.1"},
        "capabilities": {"prompt_token_details": True},
    }
    db.put("runtimes", runtime["id"], runtime)
    devices = [
        {
            "uuid": f"GPU-{i:036d}",
            "index": i,
            "compute_capability": [7, 0],
            "memory_total_mib": 32768,
        }
        for i in range(4)
    ]
    monkeypatch.setattr(gpu, "snapshot", lambda: {"gpus": devices})
    monkeypatch.setattr(gpu, "selected_devices", lambda _: devices)
    return runtime, recipe, str(tmp_path / "draft")


def test_real_catalog_default_automatically_selects_qualified_draft(qualified_release_models):
    from onecat import catalog

    runtime, recipe, draft_path = qualified_release_models
    profile = catalog.create_default_profile("target")
    assert profile["speculative_config"] == sm70_profiles.default_speculation(runtime, draft_path)
    assert not catalog.launch_defaults(profile["catalog_id"], "target")["recommendations"]
    args = _options(engine.build_argv(profile, runtime, 8000, None))
    assert json.loads(args["--speculative-config"])["revision"] == recipe["draft"]["revision"]
    for key in (
        "kv_cache_dtype",
        "max_num_batched_tokens",
        "max_num_seqs",
        "block_size",
        "mamba_block_size",
    ):
        assert args["--" + key.replace("_", "-")] == str(recipe["args"][key])


def test_draft_not_available_is_explicit_and_preserves_existing_choice(qualified_release_models):
    from onecat import catalog, db

    runtime, _, draft_path = qualified_release_models
    profile = catalog.create_default_profile("target")
    profile["speculative_config"] = None
    db.put("profiles", profile["id"], profile)
    assert catalog.create_default_profile("target")["speculative_config"] is None
    # Offer a reviewable update, never silently restore speculation.
    assert (
        sm70_profiles.recommended_update(profile, runtime)["speculative_config"]["model"]
        == draft_path
    )
    db.delete("models", "draft")
    defaults = catalog.launch_defaults(profile["catalog_id"], "target")
    assert defaults["profile"]["speculative_config"] is None
    assert any("DFlash2" in note for note in defaults["recommendations"])
    card = next(
        row
        for row in catalog.supported()["items"]
        if row.get("catalog_id") == profile["catalog_id"]
    )
    assert any("DFlash2" in note for note in card["recommendations"])
    assert sm70_profiles.recommended_update(profile, runtime) is None


def test_changed_draft_weights_are_not_selected(qualified_release_models):
    from pathlib import Path

    from onecat import catalog

    _, _, draft_path = qualified_release_models
    (Path(draft_path) / "model.safetensors").write_bytes(b"modified weights")
    defaults = catalog.launch_defaults("QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4", "target")
    assert defaults["profile"]["speculative_config"] is None
    assert any("DFlash2" in note for note in defaults["recommendations"])


def test_pinned_revision_does_not_bypass_changed_payload(qualified_release_models):
    from pathlib import Path

    from onecat import catalog, db

    _, recipe, draft_path = qualified_release_models
    profile = catalog.create_default_profile("target")
    db.patch("models", "draft", {"revision": recipe["draft"]["modelscope_revision"]})
    (Path(draft_path) / "model.safetensors").write_bytes(b"changed pinned payload")
    with pytest.raises(ValueError, match="pinned release DFlash2 draft"):
        catalog.validate_features(profile)


def test_incomplete_acceleration_report_cannot_claim_all_enabled():
    import httpx

    report = {
        "expected_acceleration": ["present", "missing"],
        "paths": {
            "present": {"enabled": True, "reason": None},
        },
    }
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=report))
    ) as client:
        assert engine.read_acceleration({"port": 8000, "api_key": "key"}, client) is None
