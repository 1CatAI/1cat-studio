# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Local H3 recipes; resolve only installed, catalog-verified components."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .. import db, gpu
from . import components, fasth3, services
from .schema import Service

RECIPES = [
    {
        "id": "h3-fl2va-base",
        "name": "H3 · 文字与首尾帧",
        "name_en": "H3 · Text and keyframes",
        "partition": "fl2va",
        "turbo": False,
    },
    {
        "id": "h3-fl2va-turbo4",
        "name": "H3 · 文字与首尾帧 · Turbo 4",
        "name_en": "H3 · Text and keyframes · Turbo 4",
        "partition": "fl2va",
        "turbo": True,
    },
    {
        "id": "h3-ref2va-base",
        "name": "H3 · 参考素材",
        "name_en": "H3 · References",
        "partition": "ref2va",
        "turbo": False,
    },
    {
        "id": "h3-ref2va-turbo4",
        "name": "H3 · 参考素材 · Turbo 4",
        "name_en": "H3 · References · Turbo 4",
        "partition": "ref2va",
        "turbo": True,
    },
    {
        "id": "h3-fl2va-turbo8",
        "name": "H3 · 文字与首尾帧 · LightX2V Turbo 8step v1.0 768p",
        "name_en": "H3 · Text and keyframes · LightX2V Turbo 8step v1.0 768p",
        "partition": "fl2va",
        "turbo": True,
    },
    {
        "id": "h3-ref2va-turbo8",
        "name": "H3 · 参考素材 · LightX2V Turbo 8step v1.0 768p",
        "name_en": "H3 · References · LightX2V Turbo 8step v1.0 768p",
        "partition": "ref2va",
        "turbo": True,
    },
]

for steps, version in ((4, "0.1"), (8, "1.0")):
    RECIPES.append(
        {
            "id": f"h3-fl2va-turbo{steps}-544p",
            "name": f"H3 · 文字与首尾帧 · LightX2V Turbo {steps}step v{version} 544p",
            "name_en": f"H3 · Text and keyframes · LightX2V Turbo {steps}step v{version} 544p",
            "partition": "fl2va",
            "turbo": True,
        }
    )

for fast, identity in fasth3.RECIPES.items():
    RECIPES.append({
        "id": identity,
        "name": "FastH3 Preview v1 · " + ("VSA" if fast else "Dense") + " Data-Free · 4 步",
        "name_en": "FastH3 Preview v1 · " + ("VSA" if fast else "Dense") + " Data-Free · 4 steps",
        "partition": "fl2va", "turbo": True, "fasth3": True, "vsa": fast,
    })


def model_id(service: dict) -> str | None:
    if service.get("kind") == "image-local":
        return service.get("checkpoint", "z-image-turbo")
    if service.get("kind") != "h3-local":
        return None
    if fasth3.is_service(service):
        return fasth3.MODEL_ID if any(
            service.get("lora_path") == db.get("creative_components", identity, {}).get("path")
            for identity in fasth3.RECIPES.values()
        ) else None
    manifest = {c["id"]: c for c in components.catalog()}
    partition = service.get("partition", "fl2va")
    base = manifest.get(f"h3-{partition}-int8", {})
    if (
        Path(service.get("transformer_path") or "").name
        != Path((base.get("files") or [{}])[0].get("path", "")).name
    ):
        return None
    filename = Path(service.get("lora_path") or "").name
    if not filename:
        return "h3-int8-20step"
    for suffix, identity in (
        ("turbo4", "h3"),
        ("turbo8", "h3-turbo8"),
        ("turbo4-544p", "h3"),
        ("turbo8-544p", "h3-turbo8"),
    ):
        adapter = manifest.get(f"h3-{partition}-{suffix}")
        if adapter and filename == Path(adapter["files"][0]["path"]).name:
            return identity
    return None


def required(recipe: dict) -> list[str]:
    if recipe.get("fasth3"):
        return ["h3-shared", "h3-original-fl2va", recipe["id"]]
    ids = ["h3-shared", f"h3-{recipe['partition']}-int8"]
    if recipe["turbo"]:
        ids.append(recipe["id"])
    return ids


def candidates() -> tuple[list[dict], list[dict]]:
    runtimes = [r for r in db.all_records("runtimes") if services.runtime_support(r)]
    devices = sorted(
        [
            d
            for d in gpu.snapshot()["gpus"]
            if "V100" in d.get("name", "") and (d.get("memory_total_mib") or 0) >= 31000
        ],
        key=lambda d: d["index"],
    )
    return runtimes, devices


def listed() -> list[dict]:
    installed = {c["id"] for c in components.listed() if c.get("installed")}
    runtimes, devices = candidates()
    return [
        {
            **r,
            "components": required(r),
            "missing_components": [i for i in required(r) if i not in installed],
            "runtime_available": any(fasth3.supported(runtime, r["vsa"]) for runtime in runtimes)
            if r.get("fasth3") else bool(runtimes),
            "hardware_available": len(devices) >= 4,
            "gpu_count": 4,
            "experimental": True,
        }
        for r in RECIPES
    ]


def create(
    identity: str,
    runtime_id: str = "",
    gpu_uuids: list[str] | None = None,
    *,
    service_id_override: str | None = None,
) -> dict:
    recipe = next((r for r in RECIPES if r["id"] == identity), None)
    if not recipe:
        raise ValueError("Unknown H3 recipe")
    paths = {c["id"]: c["installed"]["path"] for c in components.listed() if c.get("installed")}
    missing = [i for i in required(recipe) if i not in paths]
    if missing:
        raise ValueError("Download the ModelScope components first: " + ", ".join(missing))
    runtimes, devices = candidates()
    preferred = runtime_id
    runtime = next((r for r in runtimes if r["id"] == preferred), None)
    if not runtime_id and not runtime and len(runtimes) == 1:
        runtime = runtimes[0]
    if not runtime:
        raise ValueError("Select a validated H3 source runtime in Setup")
    if recipe.get("fasth3") and not fasth3.supported(runtime, recipe["vsa"]):
        raise ValueError("Update the native FastH3 runtime before preparing this recipe")
    uuids = [d["uuid"] for d in devices[:4]] if gpu_uuids is None else gpu_uuids
    if len(uuids) != 4 or len(set(uuids)) != 4 or not set(uuids) <= {d["uuid"] for d in devices}:
        raise ValueError("This H3 recipe requires four available V100 32 GB GPUs")
    # Stable identity makes repeated creation idempotent, preserving user edits.
    service_id = (
        service_id_override
        or hashlib.sha256(
            (identity + runtime["id"] + ",".join(sorted(uuids))).encode()
        ).hexdigest()[:32]
    )
    existing = db.get("creative_services", service_id)
    if existing:
        return services.public(existing)
    partition = recipe["partition"]
    return services.save(
        Service(
            id=service_id,
            name=recipe["name"],
            runtime_id=runtime["id"],
            gpu_uuids=uuids,
            model=fasth3.model_root(paths) if recipe.get("fasth3") else paths["h3-shared"],
            partition=partition,
            transformer_path="" if recipe.get("fasth3") else paths[f"h3-{partition}-int8"],
            lora_path=paths[recipe["id"]] if recipe["turbo"] else "",
            attention_backend="FASTVIDEO_VSA" if recipe.get("vsa") else "FLASH_ATTN_V100",
        )
    )
