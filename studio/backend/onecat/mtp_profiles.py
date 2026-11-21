# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Family MTP defaults for 1Cat-vLLM; checkpoint files determine eligibility."""
import json
import re
from functools import lru_cache
from pathlib import Path

from packaging.version import Version

from . import db
from .activation import _runtime_version


@lru_cache(maxsize=64)
def _weight_layout(path, size, mtime_ns):
    if path.suffix == ".json":
        # The Flash-Next NVFP4 tensor index is over 32 MiB.
        if size > 128 * 1024 * 1024:
            return (), False
        weights = json.loads(path.read_bytes())["weight_map"]
    else:
        with path.open("rb") as stream:
            length = int.from_bytes(stream.read(8), "little")
            if not 0 < length <= min(size - 8, 32 * 1024 * 1024):
                return (), False
            weights = {name: path.name for name in json.loads(stream.read(length))}
    heads = {shard for name, shard in weights.items() if name.startswith(("mtp.", "model.mtp."))}
    body = any(".layers.0." in name and "mtp." not in name for name in weights)
    return tuple(heads), body


def family_mtp(runtime, path, repo=None):
    distributions = (runtime.get("capabilities") or {}).get("distributions") or {}
    if distributions and "1cat-vllm" not in distributions:
        return None
    if not runtime.get("validated") or _runtime_version(runtime) < Version("1.5.4"):
        return None
    folder = Path(path)
    try:
        config = json.loads((folder / "config.json").read_bytes())
        text = config.get("text_config", config)
        if not text.get("mtp_num_hidden_layers", 0):
            return None
        if not repo and text.get("model_type") in {"qwen3_5", "qwen3_5_text"}:
            repo = next((m.get("repo_id") or m.get("catalog_id") for m in db.all_records("models")
                         if m.get("path") == str(folder.resolve())), None)
        identity = " ".join(str(v) for v in (repo, folder.name, config.get("_name_or_path")))
        flash = text.get("model_type") in {"qwen4_exp", "qwen4_exp_text"}
        dense = (text.get("model_type") in {"qwen3_5", "qwen3_5_text"}
                 and text.get("hidden_size") == 5120 and text.get("num_hidden_layers") == 64
                 and re.search(r"qwen3[.]8[-_]27b\b", identity.replace("_", "-"), re.I))
        if not (flash or dense):
            return None
        index = folder / "model.safetensors.index.json"
        if not index.is_file():
            index = folder / "model.safetensors"
        stat = index.stat()
        shards, body = _weight_layout(index, stat.st_size, stat.st_mtime_ns)
        if not body or not shards or not all(Path(name).name == name and (folder / name).is_file() for name in shards):
            return None
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        return None
    if flash:
        return {"method": "mtp", "num_speculative_tokens": 5, "draft_sample_method": "greedy",
                "sampling_policy": {"adaptive_mtp": "on", "adaptive_mtp_min_k": 2}}
    return {"method": "mtp", "num_speculative_tokens": 4}
