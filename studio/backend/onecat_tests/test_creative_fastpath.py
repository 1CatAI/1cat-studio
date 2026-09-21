import pytest
from onecat.creative.fastpath import launch_options
from onecat.creative.schema import Service


def fixtures():
    return (
        {
            "validated": True,
            "capabilities": {
                "h3_fastpath": {
                    "profile": "sm70-dense-v1",
                    "available": True,
                }
            },
        },
        {"kind": "h3-local", "attention_backend": "FLASH_ATTN_V100"},
        [{"name": "Tesla V100-SXM2-32GB", "compute_capability": [7, 0]} for _ in range(4)],
    )


def test_new_h3_launch_uses_dense_fast_path_without_changing_recipe():
    runtime, service, devices = fixtures()
    for partition, adapter in (("fl2va", "turbo4"), ("fl2va", "turbo8"), ("ref2va", "turbo4")):
        service.update(partition=partition, lora_path=adapter)
        options = launch_options(runtime, service, devices)
        assert options == [
            "--attention-query-tile",
            "128",
            "--fp16-weight-layout",
            "column",
            "--residual-sequence-parallel",
            "--residual-reduction",
            "peer",
            "--residual-reduction-memory-gib",
            "4",
            "--disable-host-weight-pinning",
            "--share-host-vae-weights",
        ]
        assert service["lora_path"] == adapter
    assert Service(name="H3").execution_mode == "fast"


@pytest.mark.parametrize(
    "case",
    ["old", "unbuilt", "version", "tp2", "gpu", "backend", "image", "standard", "unvalidated"],
)
def test_incompatible_or_explicit_standard_services_keep_existing_arguments(case):
    runtime, service, devices = fixtures()
    if case == "old":
        runtime["capabilities"] = {}
    if case == "unbuilt":
        runtime["capabilities"]["h3_fastpath"]["available"] = False
    if case == "version":
        runtime["capabilities"]["h3_fastpath"]["profile"] = "unknown"
    if case == "tp2":
        devices = devices[:2]
    if case == "gpu":
        devices[0]["compute_capability"] = [8, 0]
    if case == "backend":
        service["attention_backend"] = "FLASHINFER_SM70"
    if case == "image":
        service["kind"] = "image-local"
    if case == "standard":
        service["execution_mode"] = "standard"
    if case == "unvalidated":
        runtime["validated"] = False
    assert launch_options(runtime, service, devices) == []
