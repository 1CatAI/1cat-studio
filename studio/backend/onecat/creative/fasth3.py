# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Explicit FastH3 Data-Free recipes; VSA is never an INT8/LightX2V flag."""

import hashlib
import json
import tempfile
from pathlib import Path

from ..config import state_root

MODEL_ID = "h3-fasth3"
SIZES = [[1280, 736], [1344, 768], [768, 1344], [768, 768], [960, 544], [544, 960]]
FRAMES = [107, 120, 147, 243, 360]
RECIPES = {
    False: "h3-fasth3-dense",
    True: "h3-fasth3-vsa",
}


def supported(runtime, fast=False):
    capability = (runtime or {}).get("capabilities", {}).get("h3_fastpath", {})
    detail = capability.get("fasth3", {})
    return bool(
        (runtime or {}).get("validated")
        and capability.get("profile") == "sm70-dense-v1"
        and capability.get("available")
        and detail.get("vsa_available" if fast else "available")
    )


def is_service(service):
    return (
        service.get("kind") == "h3-local"
        and Path(service.get("lora_path") or "").name == "adapter_model.safetensors"
    )


def validate_adapter(record, path):
    if record["partition"] != "fl2va" or record.get("transformer_path"):
        raise ValueError("FastH3 requires original FL2VA weights, without a transformer override")
    with path.open("rb") as stream:
        size = int.from_bytes(stream.read(8), "little")
        if not 0 < size <= 16 * 1024**2:
            raise ValueError("Invalid FastH3 adapter header")
        metadata = json.loads(stream.read(size)).get("__metadata__", {})
    sparse = record["attention_backend"] == "FASTVIDEO_VSA"
    identities = (
        {f"fastvideo/fastvideo-fasth3-4-step-{v}" for v in ("v1", "v1.1", "v1.2")}
        if sparse
        else {"fastvideo/fastvideo-fasth3-dense-4-step-v1"}
    )
    if (
        metadata.get("format") != "fastvideo-lora-v2"
        or metadata.get("base_model", "").lower() != "minimaxai/minimax-h3"
        or metadata.get("finetuned_model", "").lower() not in identities
        or metadata.get("rank") != "64"
    ):
        raise ValueError("The FastH3 Dense/VSA adapter must match the selected attention backend")


def model_root(paths):
    """An immutable directory view reuses shared components without copying weights."""
    shared = Path(paths["h3-shared"]).resolve()
    original = Path(paths["h3-original-fl2va"]).resolve()
    identity = hashlib.sha256(f"{shared}\n{original}".encode()).hexdigest()[:32]
    parent = state_root() / "creative-models"
    parent.mkdir(exist_ok=True)
    root = parent / identity
    if root.exists():
        return str(root)
    with tempfile.TemporaryDirectory(dir=parent) as temp:
        target = Path(temp) / "model"
        target.mkdir()
        for child in shared.iterdir():
            if child.name != "FL2VA":
                (target / child.name).symlink_to(child, target_is_directory=child.is_dir())
        part = target / "FL2VA"
        part.mkdir()
        for child in (shared / "FL2VA").iterdir():
            if child.name != "transformer":
                (part / child.name).symlink_to(child, target_is_directory=child.is_dir())
        (part / "transformer").symlink_to(original / "FL2VA/transformer", target_is_directory=True)
        try:
            target.rename(root)
        except FileExistsError:
            pass
    return str(root)
