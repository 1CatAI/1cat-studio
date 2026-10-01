# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Use the installed runtime's release recipe, with a bundled offline fallback."""

import copy
import json
from pathlib import Path

PROFILE_NAME = "qwen38_27b_nvfp4_dflash2"
MODEL_IDS = {"QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4", "unsloth/Qwen3.8-27B-NVFP4"}
PROFILE_FIELDS = (
    "dtype",
    "tensor_parallel_size",
    "kv_cache_dtype",
    "attention_backend",
    "max_model_len",
    "max_num_batched_tokens",
    "max_num_seqs",
    "gpu_memory_utilization",
    "enable_prefix_caching",
    "served_model_name",
)


def release_profile(runtime: dict) -> dict:
    recipe = runtime.get("capabilities", {}).get("sm70_profiles", {}).get(PROFILE_NAME)
    if isinstance(recipe, dict) and recipe.get("name") == PROFILE_NAME and "args" in recipe:
        return copy.deepcopy(recipe)
    return json.loads((Path(__file__).parent / "data/sm70-qwen38-release-profile.json").read_text())


def recommendation(runtime: dict) -> dict:
    args = release_profile(runtime)["args"]
    values = {key: args[key] for key in PROFILE_FIELDS}
    values["tool_calling"] = args["enable_auto_tool_choice"]
    values["tool_parser"] = args["tool_call_parser"]
    extra = []
    for key in (
        "trust_remote_code",
        "mamba_cache_mode",
        "block_size",
        "mamba_block_size",
        "reasoning_parser",
        "default_chat_template_kwargs",
        "seed",
    ):
        value = args[key]
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                extra.append(flag)
        else:
            extra.extend(
                [
                    flag,
                    json.dumps(value, separators=(",", ":"))
                    if isinstance(value, dict)
                    else str(value),
                ]
            )
    values["extra_args"] = extra
    return values


def default_speculation(runtime: dict, draft_model: str) -> dict:
    spec = copy.deepcopy(release_profile(runtime)["args"]["speculative_config"])
    spec["model"] = draft_model
    return spec


def recommended_update(profile: dict, runtime: dict) -> dict | None:
    if profile.get("catalog_id") not in MODEL_IDS:
        return None
    legacy = {
        "dtype": "half",
        "tensor_parallel_size": 4,
        "kv_cache_dtype": "fp8_e5m2",
        "attention_backend": "FLASH_ATTN_V100",
        "max_model_len": 262144,
        "max_num_batched_tokens": 4096,
        "max_num_seqs": 4,
        "gpu_memory_utilization": 0.92,
        "enable_prefix_caching": True,
        "extra_args": [
            "--trust-remote-code",
            "--mamba-cache-mode",
            "align",
            "--reasoning-parser",
            "qwen3",
        ],
    }
    if any(profile.get(key) != value for key, value in legacy.items()):
        return None
    spec = profile.get("speculative_config")
    if spec and (spec.get("method") != "dflash" or spec.get("num_speculative_tokens") != 7):
        return None
    values = recommendation(runtime)
    # Preserve names and feature choices; migration only updates the recipe fields.
    for key in ("served_model_name", "tool_calling", "tool_parser"):
        values.pop(key)
    if spec:
        wanted = default_speculation(runtime, spec["model"])
        # Existing explicit draft tuning belongs to the user.
        if any(
            key not in {"model", "revision"} and value != wanted.get(key)
            for key, value in spec.items()
        ):
            return None
        values["speculative_config"] = wanted
    return values
