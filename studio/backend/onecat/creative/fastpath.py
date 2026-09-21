# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Use the packaged H3 dense fast path on its supported deployment hardware."""


def launch_options(runtime, service, devices):
    from .fasth3 import supported

    sparse = service.get("attention_backend") == "FASTVIDEO_VSA"
    if sparse and not supported(runtime, True):
        raise ValueError("Native VSA kernels are missing; choose standard mode or update the runtime")
    capability = runtime.get("capabilities", {}).get("h3_fastpath", {})
    if (
        service.get("kind") != "h3-local"
        or service.get("execution_mode", "fast") != "fast"
        or not runtime.get("validated")
        or capability.get("profile") != "sm70-dense-v1"
        or capability.get("available") is not True
        or service.get("attention_backend") not in {"FLASH_ATTN_V100", "FASTVIDEO_VSA"}
        or len(devices) != 4
        or not all(
            device.get("compute_capability") == [7, 0] and "V100" in str(device.get("name", ""))
            for device in devices
        )
    ):
        return []
    return (["--fastvideo-vsa-topk", "64"] if sparse else []) + [
        "--attention-query-tile",
        "64" if sparse else "128",
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
