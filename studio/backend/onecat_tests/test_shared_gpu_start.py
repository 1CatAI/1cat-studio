# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import pytest

from onecat import catalog, db, engine, gpu


@pytest.mark.parametrize("dtype", ["half", "bfloat16"])
def test_model_start_accepts_external_cuda_processes(tmp_path, monkeypatch, dtype):
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    (model / "weights.safetensors").write_bytes(b"fixture")
    db.put("runtimes", "r", {"id": "r", "validated": True})
    device = {"uuid": "GPU-11111111-1111-1111-1111-111111111111", "index": 0, "compute_capability": [7, 0],
              "processes": [{"pid": 987654, "name": "external-worker"}]}
    monkeypatch.setattr(gpu, "selected_devices", lambda ids: [device])
    monkeypatch.setattr(catalog, "validate_features", lambda p: None)
    profile = {"name": "shared", "runtime_id": "r", "model_path": str(model),
               "gpu_uuids": [device["uuid"]], "dtype": dtype}
    if dtype == "bfloat16":
        with pytest.raises(ValueError, match="V100 requires half precision"):
            engine.validate_profile(profile)
    else:
        validated, runtime = engine.validate_profile(profile)
        assert validated["gpu_uuids"] == [device["uuid"]]
        assert runtime["id"] == "r"
        # Model admission does not authorize whole-board calibration.
        with pytest.raises(RuntimeError, match="other processes"):
            gpu.check_ownership(validated["gpu_uuids"])
