# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""One prompt-first preparation flow, reusing canvas runs and native services."""

from __future__ import annotations

import fcntl
import hashlib
import json
import time
from pathlib import Path

from fastapi import HTTPException
from pydantic import Field, StrictBool, model_validator

from .. import db, engine, gpu
from ..config import state_root
from ..jobs import TERMINAL, Cancelled, create_job, list_jobs, request_cancel
from . import assets, components, fasth3, presets, runs, services
from .output_sizes import (
    H3_SIZES,
    IMAGE_SIZES,
    SMALL_H3_RECIPES,
    SMALL_SHORT_EDGE,
    h3_sizes,
    service_sizes,
)
from .schema import Document, Edge, Node, Parameters, Service, Strict
from .store import require

H3_VARIANTS = {
    "h3": ("turbo4", 4),  # Preserve the original model ID and saved drafts.
    "h3-turbo8": ("turbo8", 8),
    "h3-int8-20step": ("base", 20),
}


def is_base_variant(identity):
    """The undistilled model owns no adapter, so the official table governs it."""
    variant = H3_VARIANTS.get(identity)
    return bool(variant) and variant[0] == "base"


def output_sizes_for(identity, partition):
    return h3_sizes(identity, partition, base=is_base_variant(identity))

MODELS = [
    {
        "id": "h3",
        "name": "MiniMax-H3 + LightX2V Turbo 4step",
        "kind": "video",
        "default": True,
        "recipe": "Turbo 4",
        "gpu_count": 4,
        "sizes": H3_SIZES,
        "frames": [107, 147, 243, 360],
        "fps": 24,
    },
    {
        "id": "z-image-turbo",
        "name": "Z-Image Turbo",
        "kind": "image",
        "default": True,
        "recipe": "Turbo",
        "gpu_count": 1,
        "sizes": IMAGE_SIZES,
        "frames": [],
    },
    {
        "id": "z-image",
        "name": "Z-Image",
        "kind": "image",
        "default": False,
        "recipe": "Original",
        "gpu_count": 1,
        "sizes": IMAGE_SIZES,
        "frames": [],
    },
]
for identity, name, recipe in (
    ("h3-turbo8", "MiniMax-H3 + LightX2V Turbo 8step", "Turbo 8"),
    ("h3-int8-20step", "MiniMax-H3 INT8 ConvRot · 20 步采样", "INT8 · 20 steps"),
):
    MODELS.insert(
        1 if identity == "h3-turbo8" else 2,
        {
            **MODELS[0],
            "id": identity,
            "name": name,
            "recipe": recipe,
            "default": False,
        },
    )


def configured_models():
    """Also expose native configurations; never assume every H3 weight is INT8."""
    for service in db.all_records("creative_services"):
        if service.get("kind") != "h3-local":
            continue
        if presets.model_id(service) is not None:
            continue  # Already represented by a bundled checkpoint/adapter recipe.
        runtime = db.get("runtimes", service.get("runtime_id"), {})
        if not workbench_runtime(runtime, "h3-local"):
            continue
        yield {
            **MODELS[0],
            "id": "native:" + service["id"],
            "name": service["name"],
            "default": False,
            "recipe": None,
            "service_id": service["id"],
            "partition": service["partition"],
            "gpu_count": len(service["gpu_uuids"]),
            "sizes": service_sizes(service),
            "text_only": fasth3.is_service(service),
        }


MODELS.insert(3, {
    **MODELS[0], "id": fasth3.MODEL_ID,
    "name": "Minimax-H3",
    "default": True, "recipe": "FastH3 Data-Free", "sizes": fasth3.SIZES,
    "frames": fasth3.FRAMES, "text_only": True,
})
MODELS[0]["default"] = False


def model_definition(identity):
    built_in = next((m for m in MODELS if m["id"] == identity), None)
    return built_in or next((m for m in configured_models() if m["id"] == identity), None)


def is_video(identity):
    return (model_definition(identity) or {}).get("kind") == "video"


def configuration_digest(service):
    return hashlib.sha256(json.dumps(service, sort_keys=True).encode()).hexdigest()


class Reference(Strict):
    asset_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    role: str = Field(pattern=r"^(first|last|reference)$")


class Intent(Strict):
    model: str = "h3"
    prompt: str = Field(min_length=1, max_length=20000)
    width: int = 1344
    height: int = 768
    num_frames: int = 107
    seed: int = Field(default=42, ge=0, le=2147483647)
    references: list[Reference] = Field(default_factory=list, max_length=12)
    fast: StrictBool = False

    @model_validator(mode="after")
    def valid(self):
        model = model_definition(self.model)
        if self.fast and self.model != fasth3.MODEL_ID:
            raise ValueError("Fast VSA requires the FastH3 Data-Free model, not an INT8/LightX2V recipe")
        if (model or {}).get("text_only") and self.references:
            raise ValueError("FastH3 Data-Free currently supports text to video only")
        partition = "ref2va" if any(r.role == "reference" for r in self.references) else "fl2va"
        sizes = (
            output_sizes_for(self.model, partition)
            if self.model in H3_VARIANTS
            else (model or {}).get("sizes", [])
        )
        if not model or [self.width, self.height] not in sizes:
            raise ValueError("Choose an output size supported by this native model")
        if not self.prompt.strip():
            raise ValueError("Write a prompt before generating")
        if model["kind"] == "video" and self.num_frames not in model["frames"]:
            raise ValueError("Choose a supported video duration")
        if model["kind"] == "image" and self.references:
            raise ValueError("Z-Image currently supports text to image only")
        roles = [r.role for r in self.references]
        if roles.count("first") > 1 or roles.count("last") > 1:
            raise ValueError("Only one first frame and one last frame are supported")
        if "reference" in roles and ("first" in roles or "last" in roles):
            raise ValueError("Use either keyframes or reference materials in one generation")
        if model.get("partition") and model["partition"] != (
            "ref2va" if "reference" in roles else "fl2va"
        ):
            raise ValueError(
                "This configured model uses a different input workflow; choose a compatible model"
            )
        return self


class Submit(Strict):
    preparation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    request_key: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,80}$")
    confirm_switch: bool = False


def workflow(intent: Intent) -> str:
    if not is_video(intent.model):
        return "image-text"
    roles = {r.role for r in intent.references}
    if "reference" in roles:
        return "h3-reference"
    if roles == {"first", "last"}:
        return "h3-frames"
    return {"first": "h3-first", "last": "h3-last"}.get(next(iter(roles), ""), "h3-t2va")


def recipe_id(intent):
    if intent.model == fasth3.MODEL_ID:
        return fasth3.RECIPES[intent.fast]
    if intent.model not in H3_VARIANTS:
        return intent.model
    partition = "ref2va" if workflow(intent) == "h3-reference" else "fl2va"
    # Any canvas in the small region has to run the adapter distilled for it.
    if min(intent.width, intent.height) <= SMALL_SHORT_EDGE:
        return SMALL_H3_RECIPES[(intent.model, partition)]
    return f"h3-{partition}-{H3_VARIANTS[intent.model][0]}"


def required_components(identity):
    if identity.startswith("native:"):
        return []  # Files belong to the explicitly configured native service.
    recipe = next((r for r in presets.RECIPES if r["id"] == identity), None)
    return presets.required(recipe) if recipe else [identity]


def workbench_runtime(runtime, kind):
    if not services.runtime_support(runtime, kind):
        return False
    if kind == "image-local":
        return True
    from pathlib import Path

    source = Path(runtime.get("capabilities", {}).get("source_path", ""))
    if source.name == "__init__.py":
        source = source.parent
    try:
        return "denoise_progress" in (source / "video" / "server.py").read_text()
    except OSError:
        return False


def runtime_for(kind, *, fasth3_fast=None):
    candidates = [r for r in db.all_records("runtimes") if workbench_runtime(r, kind)]
    if fasth3_fast is not None:
        candidates = [r for r in candidates if fasth3.supported(r, fasth3_fast)]
    # Prefer a runtime already used by a local creative service, preserving explicit setup.
    existing_ids = {
        s.get("runtime_id") for s in db.all_records("creative_services") if s["kind"] == kind
    }
    return next(
        (r for r in candidates if r["id"] in existing_ids), candidates[0] if candidates else None
    )


def validation_status(model, runtime, installed):
    if model["id"] == fasth3.MODEL_ID:
        return "unverified"  # PR #583 explicitly failed combined VSA acceptance.
    evidence = db.get("creative_validations", model["id"], {})
    fingerprint = (
        (runtime or {})
        .get("capabilities", {})
        .get("source_fingerprints", {})
        .get("python_tree_sha256")
    )
    variant = H3_VARIANTS.get(model["id"])
    recipes = [f"h3-{p}-{variant[0]}" for p in ("fl2va", "ref2va")] if variant else [model["id"]]
    recipes += [
        recipe for (identity, _), recipe in SMALL_H3_RECIPES.items() if identity == model["id"]
    ]
    required = {identity for recipe in recipes for identity in required_components(recipe)}
    manifests = {
        identity: (installed.get(identity, {}).get("installed") or {}).get("manifest_sha256")
        for identity in required
    }
    workflows = {"h3-t2va", "h3-frames", "h3-reference"} if variant else {"image-text"}
    if (
        evidence.get("state") == "verified"
        and fingerprint
        and evidence.get("source_fingerprint") == fingerprint
        and all(manifests.values())
        and evidence.get("component_manifests") == manifests
        and workflows <= set(evidence.get("workflows", []))
    ):
        return "verified"
    return "unverified"


def catalog():
    installed = {c["id"]: c for c in components.listed()}
    devices = presets.candidates()[1]
    result = []
    for model in [*MODELS, *configured_models()]:
        kind = "h3-local" if model["kind"] == "video" else "image-local"
        variant = H3_VARIANTS.get(model["id"])
        is_fasth3 = model["id"] == fasth3.MODEL_ID
        ids = required_components(fasth3.RECIPES[False] if is_fasth3 else f"h3-fl2va-{variant[0]}" if variant else model["id"])
        missing = [i for i in ids if not installed.get(i, {}).get("installed")]
        runtime = runtime_for(kind, fasth3_fast=False if is_fasth3 else None)
        metadata = variant_metadata(model["id"], installed)
        if is_fasth3:
            metadata = {
                "variants": {"fl2va": fasth3_variant(installed, False)},
                "fast_variant": fasth3_variant(installed, True),
                "fast_available": bool(runtime_for(kind, fasth3_fast=True)),
            }
        if model.get("service_id"):
            service = require("creative_services", model["service_id"])
            runtime = db.get("runtimes", service["runtime_id"], {})
            files = [
                {
                    "role": "transformer",
                    "repository": "Local",
                    "revision": "",
                    "filename": Path(service["transformer_path"]).name
                    if service.get("transformer_path")
                    else service["partition"] + "/transformer",
                }
            ]
            if service.get("lora_path"):
                files.append(
                    {
                        "role": "lora",
                        "repository": "Local",
                        "revision": "",
                        "filename": Path(service["lora_path"]).name,
                    }
                )
            metadata = {
                "variants": {
                    service["partition"]: {
                        "name": service["name"],
                        "name_en": service["name"],
                        "artifacts": files,
                        "denoise_steps": None,
                        "download_bytes": 0,
                    }
                }
            }
        result.append(
            {
                **model,
                "native": True,
                "runtime_available": bool(runtime),
                "hardware_available": len(devices) >= model["gpu_count"],
                "missing_components": missing,
                "download_bytes": sum(
                    installed[i].get("download_bytes", installed[i]["bytes"]) for i in missing
                ),
                "validation": validation_status(model, runtime, installed),
                **metadata,
            }
        )
    return result


def fasth3_variant(installed, fast):
    recipe = next(r for r in presets.RECIPES if r["id"] == fasth3.RECIPES[fast])
    manifest = {c["id"]: c for c in components.catalog()}
    required = presets.required(recipe)
    return {
        "name": recipe["name"], "name_en": recipe["name_en"],
        "short_name": recipe["name"], "short_name_en": recipe["name_en"],
        "denoise_steps": 4, "sizes": fasth3.SIZES,
        "download_bytes": sum(installed[c].get("download_bytes", manifest[c]["bytes"])
                              for c in required if not installed[c].get("installed")),
        "artifacts": [
            {"role": manifest[c]["role"], "repository": manifest[c]["repo_id"],
             "filename": "FL2VA/transformer/ (13 BF16 shards)" if c == "h3-original-fl2va"
             else manifest[c]["files"][0]["path"], "revision": manifest[c]["revision"]}
            for c in required if c != "h3-shared"
        ],
    }


def variant_metadata(identity, installed):
    """Name the real checkpoint/adapter for each automatic task partition."""
    variant = H3_VARIANTS.get(identity)
    if not variant:
        return {}
    manifests = {c["id"]: c for c in components.catalog()}
    choices = {}

    def describe(partition, recipe):
        required = required_components(recipe)
        files = [
            {
                "role": manifests[cid]["role"],
                "repository": manifests[cid]["repo_id"],
                "filename": manifests[cid]["files"][0]["path"],
                "revision": manifests[cid]["revision"],
            }
            for cid in required
            if cid != "h3-shared"
        ]
        family = "FL2VA" if partition == "fl2va" else "Ref2VA"
        label = f"MiniMax-H3 {family} · INT8 ConvRot"
        short_label = "MiniMax-H3 · INT8 ConvRot · 20 步"
        if variant[0] != "base":
            filename = manifests[recipe]["files"][0]["path"]
            release = (
                filename.split("_turbo_", 1)[1]
                .removesuffix(".safetensors")
                .removesuffix("_bf16")
                .replace("_", " ")
            )
            label += f" · LightX2V Turbo {release}"
            short_label = "MiniMax-H3 · Turbo " + " ".join(release.split()[:2])
        else:
            label += " · 20 步采样"
        missing = [cid for cid in required if not installed.get(cid, {}).get("installed")]
        return {
            "name": label,
            "name_en": label.replace("20 步采样", "20 sampling steps"),
            "short_name": short_label,
            "short_name_en": short_label.replace("20 步", "20 steps"),
            "artifacts": files,
            "denoise_steps": variant[1],
            "download_bytes": sum(
                installed[cid].get("download_bytes", installed[cid]["bytes"]) for cid in missing
            ),
        }

    for partition in ("fl2va", "ref2va"):
        choices[partition] = describe(partition, f"h3-{partition}-{variant[0]}")
        choices[partition]["sizes"] = output_sizes_for(identity, partition)
        if (identity, partition) in SMALL_H3_RECIPES:
            choices[partition]["resolutions"] = {
                "544": describe(partition, SMALL_H3_RECIPES[(identity, partition)])
            }

    return {"variants": choices}


def selection(intent):
    definition = model_definition(intent.model)
    if definition.get("service_id"):
        service = require("creative_services", definition["service_id"])
        services.local_paths(dict(service))
        available = {d["uuid"] for d in presets.candidates()[1]}
        if not service["gpu_uuids"] or not set(service["gpu_uuids"]) <= available:
            raise ValueError("Configured model GPUs are unavailable")
        return {
            "service_id": service["id"],
            "runtime_id": service["runtime_id"],
            "gpu_uuids": service["gpu_uuids"],
            "recipe_id": intent.model,
            "configuration_sha256": configuration_digest(service),
        }
    identity = recipe_id(intent)
    kind = "h3-local" if is_video(intent.model) else "image-local"
    runtime = runtime_for(kind, fasth3_fast=intent.fast if intent.model == fasth3.MODEL_ID else None)
    if not runtime:
        raise ValueError(
            "未找到原生创作运行环境，请在安装引导中更新 / Update the native creative runtime in Setup"
        )
    devices = presets.candidates()[1]
    needed = 4 if kind == "h3-local" else 1
    matching = []
    paths = {c["id"]: c["installed"]["path"] for c in components.listed() if c.get("installed")}
    for service in db.all_records("creative_services"):
        if service["kind"] != kind or not workbench_runtime(
            db.get("runtimes", service.get("runtime_id"), {}), kind
        ):
            continue
        if kind == "image-local":
            match = service.get("checkpoint") == intent.model and service.get("model") == paths.get(
                intent.model
            )
        elif intent.model == fasth3.MODEL_ID:
            model_path = Path(service.get("model") or "") / "FL2VA/transformer"
            original = Path(paths.get("h3-original-fl2va", "")) / "FL2VA/transformer"
            match = (
                fasth3.supported(db.get("runtimes", service.get("runtime_id"), {}), intent.fast)
                and service["partition"] == "fl2va" and not service.get("transformer_path")
                and bool(paths.get(identity)) and service.get("lora_path") == paths[identity]
                and bool(paths.get("h3-original-fl2va")) and model_path.resolve() == original.resolve()
                and service.get("attention_backend") == ("FASTVIDEO_VSA" if intent.fast else "FLASH_ATTN_V100")
            )
        else:
            partition = "ref2va" if workflow(intent) == "h3-reference" else "fl2va"
            adapter = H3_VARIANTS[intent.model][0]
            adapter_matches = (
                not service.get("lora_path")
                if adapter == "base"
                else bool(paths.get(identity)) and service.get("lora_path") == paths[identity]
            )
            match = (
                service["partition"] == partition
                and service.get("model") == paths.get("h3-shared")
                and service.get("transformer_path") == paths.get(f"h3-{partition}-int8")
                and adapter_matches
            )
        if (
            match
            and len(service.get("gpu_uuids", [])) == needed
            and set(service["gpu_uuids"]) <= {d["uuid"] for d in devices}
        ):
            matching.append(service)
    matching.sort(key=lambda s: services.instance(s["id"]).get("state") != "ready")
    if matching:
        record = matching[0]
        return {
            "service_id": record["id"],
            "runtime_id": record["runtime_id"],
            "gpu_uuids": record["gpu_uuids"],
            "recipe_id": identity,
            "configuration_sha256": configuration_digest(record),
        }
    if len(devices) < needed:
        raise ValueError(f"This recipe needs {needed} V100 32 GB GPUs")
    # Prefer a free single card. Never select the display GPU or change selection while queued.
    if needed == 1:
        devices.sort(key=lambda d: bool(d.get("processes")))
    uuids = [d["uuid"] for d in devices[:needed]]
    sid = hashlib.sha256((identity + runtime["id"] + ",".join(sorted(uuids))).encode()).hexdigest()[
        :32
    ]
    if db.get("creative_services", sid):
        # The original preset was edited. Keep it and prepare the requested
        # recipe as a separate service rather than silently using another LoRA.
        sid = db.uid()
    return {
        "service_id": sid,
        "runtime_id": runtime["id"],
        "gpu_uuids": uuids,
        "recipe_id": identity,
    }


def impacts(selected):
    scope = set(selected["gpu_uuids"])
    affected = []
    for service in db.all_records("creative_services"):
        if service["id"] == selected["service_id"] or not scope.intersection(
            service.get("gpu_uuids", [])
        ):
            continue
        if service["kind"] in {"h3-local", "image-local"} and services.requires_stop(service["id"]):
            instance = services.instance(service["id"])
            affected.append(
                {
                    "kind": "creative",
                    "id": service["id"],
                    "name": service["name"],
                    "instance": instance.get("process_created"),
                }
            )
    active = engine.private_state()
    if active.get("pid") and scope.intersection((active.get("profile") or {}).get("gpu_uuids", [])):
        profile = active.get("profile") or {}
        affected.append(
            {
                "kind": "chat",
                "id": profile.get("id", "chat"),
                "name": profile.get("name", "聊天模型 / Chat model"),
                "instance": active.get("process_created"),
            }
        )
    return sorted(affected, key=lambda x: (x["kind"], x["id"]))


def document(intent, sid):
    target = Node(
        id="generation",
        kind="generate",
        x=0,
        y=0,
        text=intent.prompt,
        workflow=workflow(intent),
        service_id=sid,
        parameters=Parameters(
            width=intent.width,
            height=intent.height,
            num_frames=intent.num_frames,
            seed=intent.seed,
            num_inference_steps=4 if intent.model == fasth3.MODEL_ID else H3_VARIANTS[intent.model][1] + 1
            if intent.model in H3_VARIANTS
            else None,
        ),
    )
    nodes, edges = [target], []
    ordered = sorted(
        intent.references, key=lambda r: {"first": 0, "last": 1, "reference": 2}[r.role]
    )
    for index, ref in enumerate(ordered):
        asset, _ = assets.path_for(ref.asset_id)
        if ref.role != "reference" and asset["kind"] != "image":
            raise ValueError("First and last frames must be images")
        identity = f"reference-{index}"
        nodes.append(
            Node(id=identity, kind=asset["kind"], asset_id=ref.asset_id, x=-300, y=index * 240)
        )
        edges.append(Edge(id="edge-" + identity, source=identity, target="generation"))
    return Document(nodes=nodes, edges=edges).model_dump()


def prepare(intent: Intent, chosen=None, canvas=None):
    chosen = chosen or selection(intent)
    # Validate references even when weights have not been downloaded yet.
    doc = document(intent, chosen["service_id"])
    existing = db.get("creative_services", chosen["service_id"])
    provisional = existing or {
        "kind": "h3-local" if is_video(intent.model) else "image-local",
        "partition": "ref2va" if workflow(intent) == "h3-reference" else "fl2va",
        "lora_path": next(
            (
                c["files"][0]["path"]
                for c in components.catalog()
                if c["id"] == chosen["recipe_id"] and c["role"] == "lora"
            ),
            "",
        ),
    }
    runs.validate_request(doc, "generation", provisional)
    required = required_components(chosen["recipe_id"])
    missing = [c for c in components.listed() if c["id"] in required and not c.get("installed")]
    record = {
        "id": db.uid(),
        "canvas": canvas,
        "intent": intent.model_dump(),
        "selection": chosen,
        "impacts": impacts(chosen),
        "created_at": time.time(),
        "expires_at": time.time() + 900,
        "missing_components": [c["id"] for c in missing],
        "download_bytes": sum(c["bytes"] for c in missing),
    }
    db.put("creative_preparations", record["id"], record)
    return record


def validate_switch(selection, allowed):
    if (
        selection.get("configuration_sha256")
        and configuration_digest(db.get("creative_services", selection["service_id"]))
        != selection["configuration_sha256"]
    ):
        raise ValueError("Model configuration changed; select the model and submit again")
    if any(effect not in allowed for effect in impacts(selection)):
        raise services.ServiceBusy(
            "运行中的服务已改变，请重新确认模型切换 / Running services changed; confirm the switch again"
        )


def submit(payload: Submit):
    with (state_root() / "creative-admission.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for record in db.all_records("creative_runs"):
            if record.get("preparation_id") and record.get("request_key") == payload.request_key:
                if record.get("preparation_id") != payload.preparation_id:
                    raise HTTPException(409, "Request key already used for a different generation")
                return runs.public(record)
        prepared = require("creative_preparations", payload.preparation_id)
        if prepared["expires_at"] < time.time():
            raise HTTPException(409, "Preparation expired; submit again")
        if prepared["impacts"] and not payload.confirm_switch:
            raise HTTPException(409, "Confirm the affected model services first")
        validate_switch(prepared["selection"], prepared["impacts"])
        intent = Intent.model_validate(prepared["intent"])
        canvas = prepared.get("canvas")
        if canvas and require("canvases", canvas["project_id"])["revision"] != canvas["revision"]:
            raise HTTPException(
                409, "Canvas changed during preparation; save it and generate again"
            )
        doc = document(intent, prepared["selection"]["service_id"])
        service = db.get("creative_services", prepared["selection"]["service_id"])
        placeholder = service or {
            "id": prepared["selection"]["service_id"],
            "name": next(m["name"] for m in MODELS if m["id"] == intent.model),
        }
        from .schema import WORKFLOWS

        record = {
            "id": db.uid(),
            "source": "canvas" if canvas else "workbench",
            "project_id": canvas["project_id"] if canvas else None,
            "node_id": canvas["node_id"] if canvas else "generation",
            "service_id": placeholder["id"],
            "state": "queued",
            "stage": "waiting_resources",
            "stage_started_at": time.time(),
            "created_at": time.time(),
            "updated_at": time.time(),
            "preparation_id": prepared["id"],
            "request_key": payload.request_key,
            "assets": [],
            "intent": intent.model_dump(),
            "snapshot": {
                "node": doc["nodes"][0],
                "workflow": next(w for w in WORKFLOWS if w["id"] == workflow(intent)),
                "service": placeholder,
                "prompt": intent.prompt,
                "parameters": doc["nodes"][0]["parameters"],
                "references": [],
            },
        }
        if canvas:
            record["snapshot"] = canvas["snapshot"]
        db.put("creative_runs", record["id"], record)
        try:
            job = create_job("creative_prepare_generate", {"run_id": record["id"]})
            record = db.patch("creative_runs", record["id"], {"job_id": job["id"]})
        except Exception as error:
            db.patch("creative_runs", record["id"], {"state": "failed", "error": str(error)})
            raise
        return runs.public(record)


def wait_child(job, identity, child, stage, *, cancel_child=True):
    db.patch("creative_runs", identity, {"preparation_job_id": child["id"]})
    try:
        while True:
            job.check_cancelled()
            list_jobs()
            current = require("jobs", child["id"])
            if current["state"] == "completed":
                return current.get("result", {})
            if current["state"] in TERMINAL:
                raise ValueError(current.get("error") or "Preparation failed")
            child_stage = current.get("stage", stage)
            if stage == "downloading" and child_stage not in {"downloading", "checking_model"}:
                child_stage = "downloading"
            runs.progress(
                job,
                identity,
                child_stage,
                preparation_progress={
                    k: current.get(k)
                    for k in (
                        "stage",
                        "phase_progress",
                        "detail",
                        "downloaded_bytes",
                        "expected_bytes",
                        "bytes_per_second",
                    )
                },
            )
            time.sleep(0.5)
    except Cancelled:
        if not cancel_child:
            # This download was started elsewhere; cancelling this generation
            # must not interrupt the original caller's component verification.
            raise
        request_cancel(child["id"])
        # Cooperative lifecycle cleanup owns the model subprocess; wait for it.
        while require("jobs", child["id"])["state"] not in TERMINAL:
            list_jobs()
            time.sleep(0.25)
        raise


def execute(job):
    record = require("creative_runs", job.payload["run_id"])
    prepared = require("creative_preparations", record["preparation_id"])
    chosen = prepared["selection"]
    intent = Intent.model_validate(prepared["intent"])
    try:
        # Serialize orchestration by creation order. Tasks keep their explicit UUIDs.
        with (state_root() / "creative-preparation.lock").open("a") as lock:
            while True:
                job.check_cancelled()
                runs.reconcile()
                older = any(
                    r.get("source") in {"workbench", "canvas"}
                    and r.get("preparation_id")
                    and r["state"] not in TERMINAL
                    and r["created_at"] < record["created_at"]
                    for r in db.all_records("creative_runs")
                )
                try:
                    if older:
                        raise BlockingIOError()
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    runs.progress(
                        job,
                        record["id"],
                        "waiting_resources",
                        detail="等待前一项创作 / Waiting for the previous creation",
                    )
                    time.sleep(0.5)
            for component in required_components(chosen["recipe_id"]):
                if next((c for c in components.listed() if c["id"] == component), {}).get(
                    "installed"
                ):
                    continue
                child, owned = components.schedule_download(component)
                wait_child(job, record["id"], child, "downloading", cancel_child=owned)
            if not db.get("creative_services", chosen["service_id"]):
                if intent.model.startswith("native:"):
                    raise ValueError("The selected native model configuration was removed")
                if intent.model in H3_VARIANTS or intent.model == fasth3.MODEL_ID:
                    presets.create(
                        chosen["recipe_id"],
                        chosen["runtime_id"],
                        chosen["gpu_uuids"],
                        service_id_override=chosen["service_id"],
                    )
                else:
                    services.save(
                        Service(
                            id=chosen["service_id"],
                            name=next(m["name"] for m in MODELS if m["id"] == intent.model),
                            kind="image-local",
                            model=require("creative_components", intent.model)["path"],
                            checkpoint=intent.model,
                            runtime_id=chosen["runtime_id"],
                            gpu_uuids=chosen["gpu_uuids"],
                        )
                    )
            snapshot = (
                prepared["canvas"]["snapshot"]
                if prepared.get("canvas")
                else runs.validate_request(document(intent, chosen["service_id"]), "generation")
            )
            db.patch("creative_runs", record["id"], {"snapshot": snapshot})
            while True:
                job.check_cancelled()
                validate_switch(chosen, prepared["impacts"])
                ours = services.owned_pids() | engine.owned_pids()
                devices = gpu.selected_devices(chosen["gpu_uuids"])
                external = [
                    {"gpu": d["index"], "pid": p["pid"]}
                    for d in devices
                    for p in d.get("processes", [])
                    if p["pid"] not in ours
                ]
                active = [
                    r
                    for r in db.all_records("creative_runs")
                    if r["id"] != record["id"]
                    and r["state"] not in TERMINAL
                    and (r.get("upstream_id") or not r.get("preparation_id"))
                    and any(e["id"] == r["service_id"] for e in prepared["impacts"])
                ]
                if (
                    external
                    or active
                    or any(
                        services.pending_lifecycle(e["id"])
                        for e in prepared["impacts"]
                        if e["kind"] == "creative"
                    )
                ):
                    runs.progress(
                        job,
                        record["id"],
                        "waiting_resources",
                        external_processes=external,
                        detail="外部程序正在使用显卡，任务会保留 / Waiting for external GPU work"
                        if external
                        else "等待当前生成完成 / Waiting for the current generation",
                    )
                    time.sleep(1)
                    continue
                if services.instance(chosen["service_id"]).get("state") == "ready":
                    break
                child = create_job(
                    "creative_load",
                    {
                        "service_id": chosen["service_id"],
                        "generation_run_id": record["id"],
                        "allowed_impacts": prepared["impacts"],
                    },
                )
                wait_child(job, record["id"], child, "loading_weights")
            runs.progress(job, record["id"], "checking_service", preparation_progress=None)
            services.check(snapshot["service"])
            return runs.execute(job)
    except BaseException as error:
        db.patch(
            "creative_runs",
            record["id"],
            {
                "state": "cancelled" if isinstance(error, Cancelled) else "failed",
                "error": str(error) or type(error).__name__,
                "finished_at": time.time(),
                "updated_at": time.time(),
            },
        )
        raise


def prepare_canvas(project_id, node_id, revision):
    project = require("canvases", project_id)
    if project["revision"] != revision:
        raise HTTPException(409, "Save the current canvas before generating")
    snapshot = runs.validate_request(project, node_id)
    service = snapshot["service"]
    if service["kind"] not in {"h3-local", "image-local"}:
        raise ValueError("This preparation flow requires a native image or video service")
    indices = snapshot["workflow"].get("keyframe_indices", [])
    refs = [
        Reference(
            asset_id=asset["id"],
            role=("first" if indices[i] == 0 else "last") if indices else "reference",
        )
        for i, asset in enumerate(snapshot["references"])
    ]
    value = Intent(
        model=presets.model_id(service) or "native:" + service["id"],
        fast=presets.model_id(service) == fasth3.MODEL_ID and service.get("attention_backend") == "FASTVIDEO_VSA",
        prompt=snapshot["prompt"],
        references=refs,
        **{k: snapshot["parameters"][k] for k in ("width", "height", "num_frames", "seed")},
    )
    recipe = recipe_id(value)
    return prepare(
        value,
        chosen={
            "service_id": service["id"],
            "runtime_id": service["runtime_id"],
            "gpu_uuids": service["gpu_uuids"],
            "recipe_id": recipe,
            "configuration_sha256": configuration_digest(service),
        },
        canvas={
            "project_id": project_id,
            "node_id": node_id,
            "revision": revision,
            "snapshot": snapshot,
        },
    )
