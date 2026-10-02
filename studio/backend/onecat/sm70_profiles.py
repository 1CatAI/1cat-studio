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
    fallback = json.loads(
        (Path(__file__).parent / "data/sm70-qwen38-release-profile.json").read_text()
    )
    recipe = runtime.get("capabilities", {}).get("sm70_profiles", {}).get(PROFILE_NAME)
    if (
        isinstance(recipe, dict)
        and recipe.get("name") == PROFILE_NAME
        and isinstance(recipe.get("args"), dict)
        and fallback["args"].keys() <= recipe["args"].keys()
        and isinstance(recipe["args"].get("speculative_config"), dict)
        and isinstance(recipe.get("draft"), dict)
        and fallback["draft"].keys() <= recipe["draft"].keys()
    ):
        return copy.deepcopy(recipe)
    return fallback


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


def qualified_draft(runtime: dict, model_path: str | None = None) -> str | None:
    """Reuse only the release payload whose verified files remain unchanged."""
    from . import catalog, db

    draft = release_profile(runtime)["draft"]
    for record in db.all_records("models"):
        path = record.get("path")
        if not path or (model_path is not None and path != model_path):
            continue
        if record.get("repo_id") != draft["repo"]:
            continue
        item = catalog.model_entry(path)
        hashes = {row["path"]: row.get("sha256") for row in record.get("manifest", [])}
        if (
            item
            and item["id"] == draft["repo"]
            and all(
                hashes.get(name) == draft[key]
                for name, key in (
                    ("config.json", "config_sha256"),
                    ("model.safetensors", "weights_sha256"),
                )
            )
        ):
            return path
    return None


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
    values = recommendation(runtime)
    is_legacy = all(profile.get(key) == value for key, value in legacy.items())
    spec = profile.get("speculative_config")
    is_current = not spec and all(profile.get(key) == values[key] for key in legacy)
    if not is_legacy and not is_current:
        return None
    if spec and (spec.get("method") != "dflash" or spec.get("num_speculative_tokens") != 7):
        return None
    # Preserve names and feature choices; migration only updates the recipe fields.
    for key in ("served_model_name", "tool_calling", "tool_parser"):
        values.pop(key)
    if spec:
        wanted = default_speculation(runtime, spec["model"])
        draft = release_profile(runtime)["draft"]
        if spec.get("revision") not in {
            None,
            "master",
            draft["revision"],
            draft["modelscope_revision"],
        }:
            return None
        # Existing explicit draft tuning belongs to the user.
        if any(
            key not in {"model", "revision"} and value != wanted.get(key)
            for key, value in spec.items()
        ):
            return None
        values["speculative_config"] = wanted
    else:
        draft_path = qualified_draft(runtime)
        if draft_path:
            values["speculative_config"] = default_speculation(runtime, draft_path)
        elif is_current:
            return None
    return values
