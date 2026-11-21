import json

import pytest

from onecat.mtp_profiles import family_mtp


@pytest.mark.parametrize(("repo", "model_type", "depth"), [
    ("Vendor/Qwen3.8-27B-NVFP4", "qwen3_5_text", 4),
    ("Vendor/Qwen3.8-Flash-Next-NVFP4", "qwen4_exp_text", 5),
])
def test_family_defaults_follow_weights_and_runtime(tmp_path, repo, model_type, depth):
    runtime = {"validated": True, "release": {"version": "1.5.4"}}
    config = {"text_config": {"model_type": model_type, "mtp_num_hidden_layers": 1,
                              "hidden_size": 5120, "num_hidden_layers": 64}}
    (tmp_path / "config.json").write_text(json.dumps(config))
    index = {"weight_map": {"model.layers.0.weight": "main.safetensors",
                            "mtp.layers.0.weight": "head.safetensors"}}
    # Exercise the size class of the real sharded Flash-Next index.
    padding = " " * (32 * 1024 * 1024) if depth == 5 else ""
    (tmp_path / "model.safetensors.index.json").write_text(json.dumps(index) + padding)
    assert family_mtp(runtime, tmp_path, repo) is None
    (tmp_path / "head.safetensors").write_bytes(b"head")
    (tmp_path / "main.safetensors").write_bytes(b"main")
    spec = family_mtp(runtime, tmp_path, repo)
    assert spec["num_speculative_tokens"] == depth
    if depth == 5:
        assert spec["sampling_policy"] == {"adaptive_mtp": "on", "adaptive_mtp_min_k": 2}
    else:
        assert "sampling_policy" not in spec
        assert family_mtp(runtime, tmp_path, "Vendor/Qwen3.6-27B-NVFP4") is None
    runtime["release"]["version"] = "1.5.1"
    assert family_mtp(runtime, tmp_path, repo) is None
