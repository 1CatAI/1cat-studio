# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import json

import pytest
from onecat.creative import fasth3, fastpath
from onecat.creative.schema import WORKFLOWS, compatible


def test_original_model_directory_reuses_shared_files_without_mutating_them(state, tmp_path):
    shared, original = tmp_path / "shared", tmp_path / "original"
    (shared / "FL2VA/text_encoder").mkdir(parents=True)
    (shared / "FL2VA/transformer").mkdir()
    (original / "FL2VA/transformer").mkdir(parents=True)
    (original / "FL2VA/transformer/weight.safetensors").write_bytes(b"original")
    paths = {"h3-shared": str(shared), "h3-original-fl2va": str(original)}
    from pathlib import Path

    root = Path(fasth3.model_root(paths))
    assert root == Path(fasth3.model_root(paths))
    assert (root / "FL2VA/transformer").resolve() == original / "FL2VA/transformer"
    assert (root / "FL2VA/text_encoder").resolve() == shared / "FL2VA/text_encoder"
    assert not list((shared / "FL2VA/transformer").iterdir())


@pytest.mark.parametrize("fast", [False, True])
def test_fasth3_metadata_backend_and_original_weight_guard(tmp_path, fast):
    metadata = {
        "format": "fastvideo-lora-v2",
        "base_model": "MiniMaxAI/MiniMax-H3",
        "rank": "64",
        "finetuned_model": "fastvideo/fastvideo-fasth3-"
        + ("4-step-v1" if fast else "dense-4-step-v1"),
    }
    header = json.dumps({"__metadata__": metadata}).encode()
    path = tmp_path / "adapter_model.safetensors"
    path.write_bytes(len(header).to_bytes(8, "little") + header)
    service = {
        "partition": "fl2va",
        "attention_backend": "FASTVIDEO_VSA" if fast else "FLASH_ATTN_V100",
    }
    fasth3.validate_adapter(service, path)
    with pytest.raises(ValueError, match="original"):
        fasth3.validate_adapter({**service, "transformer_path": "int8.safetensors"}, path)
    with pytest.raises(ValueError, match="match"):
        fasth3.validate_adapter(
            {**service, "attention_backend": "FLASH_ATTN_V100" if fast else "FASTVIDEO_VSA"}, path
        )
    with pytest.raises(ValueError, match="original"):
        fasth3.validate_adapter({**service, "partition": "ref2va"}, path)


def test_fast_service_only_accepts_text_workflow():
    service = {
        "kind": "h3-local",
        "partition": "fl2va",
        "lora_path": "/vsa/adapter_model.safetensors",
    }
    assert [w["id"] for w in WORKFLOWS if compatible(service, w)] == ["h3-t2va"]


def test_vsa_launch_keeps_shared_exact_optimizations_and_reports_missing_kernel():
    runtime = {
        "validated": True,
        "capabilities": {
            "h3_fastpath": {
                "profile": "sm70-dense-v1",
                "available": True,
                "fasth3": {"available": True, "vsa_available": True},
            }
        },
    }
    service = {"kind": "h3-local", "attention_backend": "FASTVIDEO_VSA"}
    devices = [{"compute_capability": [7, 0], "name": "V100"}] * 4
    args = fastpath.launch_options(runtime, service, devices)
    assert args[args.index("--attention-query-tile") + 1] == "64"
    assert args[args.index("--fastvideo-vsa-topk") + 1] == "64"
    assert "--share-host-vae-weights" in args
    assert "--residual-sequence-parallel" in args
    runtime["capabilities"]["h3_fastpath"]["fasth3"]["vsa_available"] = False
    with pytest.raises(ValueError, match="kernels"):
        fastpath.launch_options(runtime, service, devices)
