# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import copy
import json
import re

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
    for key in ("VLLM_HOST_TUNING", "PYTHONPATH", "ONECAT_VLLM_EXTENSION_DIR"):
        monkeypatch.setenv(key, "inherited")
    assert not any(key.startswith("VLLM_") for key in runtime_environment({}))
    assert "PYTHONPATH" not in runtime_environment({})
    assert "ONECAT_VLLM_EXTENSION_DIR" not in runtime_environment({})
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
