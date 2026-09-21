# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Exact checkpoint evidence and immutable ModelScope download manifests."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from . import db, gpu

SOURCE_SNAPSHOT_BASE_VERSIONS = {"pr417-a09e332bcd": "1.3.0"}


def runtime_family(value) -> str:
    """The compatibility family of a runtime: builds sharing a major.minor.

    A runtime is a replaceable component: it gets upgraded in place and reinstalled
    under a new id. Acceptance is therefore attached to the checkpoint and this
    family, so an upgrade inside the family keeps working while a new major or
    minor still has to prove itself.
    """
    if isinstance(value, dict):
        capabilities = value.get("capabilities") or {}
        text = str(
            (value.get("release") or {}).get("version")
            or capabilities.get("distribution_version")
            or capabilities.get("vllm_version")
            or value.get("id")
            or ""
        )
    else:
        text = str(value or "")
    match = re.search(r"(\d+)\.(\d+)", text)
    return "%s.%s" % (match.group(1), match.group(2)) if match else ""


@lru_cache(maxsize=1)
def directory():
    return json.loads((Path(__file__).parent / "data/verified-models-v1.json").read_text())


def entry(id):
    return next((e for e in directory()["entries"] if e["id"] == id), None)


def runtime_matches(item, runtime):
    capabilities = runtime.get("capabilities", {})
    snapshot = capabilities.get("source_snapshot")
    if snapshot:
        versions = {SOURCE_SNAPSHOT_BASE_VERSIONS.get(snapshot)}
    else:
        versions = {
            runtime.get("release", {}).get("version"),
            capabilities.get("distribution_version"),
            capabilities.get("vllm_version"),
        }
    return runtime.get("validated") and (
        bool(versions.intersection(item["runtime_versions"]))
        or snapshot in item.get("runtime_snapshots", [])
    )


def recommended_devices(devices, topology):
    """The verified layout when it exists, otherwise the closest hardware present."""
    same = [
        d for d in devices if d.get("compute_capability") == topology["compute_capability"]
    ]
    chosen = sorted(
        (d for d in same if (d.get("memory_total_mib") or 0) >= topology["memory_mib"]),
        key=lambda d: d["index"],
    )[: topology["count"]]
    if len(chosen) < topology["count"]:
        taken = {d["uuid"] for d in chosen}
        chosen += sorted(
            (d for d in same if d["uuid"] not in taken),
            key=lambda d: (-(d.get("memory_total_mib") or 0), d["index"]),
        )[: topology["count"] - len(chosen)]
    if not chosen:
        # A verified recipe recommends hardware; it must not gate silicon this
        # installation does not have, or the preset is unusable off the build host.
        chosen = sorted(devices, key=lambda d: d["index"])[: topology["count"]]
    return sorted(chosen, key=lambda d: d["index"])


def compatibility(item, runtime_id=None, devices=None):
    """Blockers only: what this installation cannot do at all."""
    reasons = []
    runtimes = [db.get("runtimes", runtime_id, {})] if runtime_id else db.all_records("runtimes")
    if not any(runtime_matches(item, r) for r in runtimes):
        reasons.append("需要运行环境 / Requires runtime: " + ", ".join(item["runtime_versions"]))
    if item["gpu"]["pp"] != 1:
        reasons.append(
            f"需要 TP{item['gpu']['tp']} / PP{item['gpu']['pp']}；Studio 尚不支持流水线并行 / "
            "Pipeline parallelism is not available"
        )
    devices = gpu.snapshot()["gpus"] if devices is None else devices
    if not devices:
        reasons.append("未检测到 NVIDIA GPU / No NVIDIA GPU detected")
    return reasons


def recommendations(item, devices=None):
    """How far this machine is from the verified recipe. Always only advisory."""
    topology = item["gpu"]
    devices = gpu.snapshot()["gpus"] if devices is None else devices
    chosen = recommended_devices(devices, topology)
    notes = []
    if len(chosen) < topology["count"]:
        notes.append(
            f"验证配置使用 {topology['count']} 张显卡，本机仅 {len(chosen)} 张 / "
            f"Verified with {topology['count']} GPUs; {len(chosen)} available"
        )
    thinner = [
        d for d in chosen if (d.get("memory_total_mib") or 0) < topology["memory_mib"]
    ]
    if thinner:
        notes.append(
            f"验证配置为每卡 {topology['memory_mib'] // 1024} GB，本机最小 "
            f"{min(d.get('memory_total_mib') or 0 for d in thinner) // 1024} GB，"
            "长上下文并发会低于验证值 / Smaller cards than verified; long-context "
            "concurrency will be lower"
        )
    if chosen and not any(
        d.get("compute_capability") == topology["compute_capability"] for d in chosen
    ):
        notes.append(
            "本机架构与验证架构不同，量化或算子可能不受支持 / Architecture differs from the "
            "verified one; quantization or kernels may be unsupported"
        )
    if chosen and len(chosen) != topology["tp"]:
        notes.append(
            f"验证配置使用 TP{topology['tp']}，将按本机使用 TP{len(chosen)} / "
            f"Verified with TP{topology['tp']}; this machine will use TP{len(chosen)}"
        )
    return notes


def search(query="", runtime_id=None, all_verified=False):
    devices = gpu.snapshot()["gpus"]
    result = []
    for item in directory()["entries"]:
        if (
            item["role"] != "target"
            or query.casefold()
            not in " ".join(
                [item["name"], item["id"], item["family"], item["quantization"]]
            ).casefold()
        ):
            continue
        reasons = compatibility(item, runtime_id, devices)
        if reasons and not all_verified:
            continue
        result.append(
            {k: v for k, v in item.items() if k != "files"}
            | {
                "compatible": not reasons,
                "downloadable": True,
                "reasons": reasons,
                "recommendations": recommendations(item, devices),
                "bytes": sum(f["bytes"] for f in item["files"]),
                "verified": True,
            }
        )
    return result


def supported():
    """Downloading weights does not require a compatible inference installation."""
    devices = gpu.snapshot()["gpus"]
    downloads = sorted(
        (j for j in db.all_records("jobs") if j["kind"] == "download_model"),
        key=lambda j: j.get("created_at", 0),
        reverse=True,
    )
    local = db.all_records("models")
    result = []
    for item in directory().get("support", []):
        checkpoint = entry(item.get("catalog_id"))
        reasons = compatibility(checkpoint, devices=devices) if checkpoint else []
        model = next(
            (
                m
                for m in local
                if m.get("catalog_id") == item.get("catalog_id")
                and checkpoint
                and model_entry(m["path"])
            ),
            None,
        )
        job = next(
            (
                j
                for j in downloads
                if checkpoint and j.get("payload", {}).get("repo_id") == checkpoint["id"]
            ),
            None,
        )
        result.append(
            {
                **item,
                "downloadable": bool(checkpoint),
                "compatible": bool(checkpoint) and not reasons,
                "reasons": reasons,
                "recommendations": recommendations(checkpoint, devices) if checkpoint else [],
                "bytes": sum(f["bytes"] for f in checkpoint["files"]) if checkpoint else None,
                "recommended": checkpoint.get("recommended", {}) if checkpoint else None,
                "gpu": checkpoint["gpu"] if checkpoint else None,
                "model_id": model["id"] if model else None,
                "job": {
                    k: job[k]
                    for k in (
                        "id",
                        "state",
                        "stage",
                        "progress",
                        "error",
                        "expected_bytes",
                        "downloaded_bytes",
                        "bytes_per_second",
                        "result",
                        "cancel_requested",
                    )
                    if k in job
                }
                if job
                else None,
            }
        )
    return {
        "version": directory()["version"],
        "audited_at": directory()["audited_at"],
        "items": result,
    }


def require_download(repo, revision=None, runtime_id=None):
    item = entry(repo)
    if not item:
        raise ValueError("Only checkpoints in the verified ModelScope directory can be downloaded")
    if revision and revision != item["revision"]:
        raise ValueError("Revision does not match the verified checkpoint manifest")
    # Hardware/runtime compatibility is enforced when creating or starting a
    # profile, not when obtaining an otherwise verified checkpoint.
    if (
        not item.get("files")
        or not any(f["path"].endswith((".safetensors", ".bin")) for f in item["files"])
        or any(f["path"].lower().endswith(".gguf") for f in item["files"])
    ):
        raise ValueError("Verified checkpoint has no supported download manifest")
    return item


def launch_defaults(repo, model_id=None):
    """Prepare a reviewable profile, selecting only a documented runtime/topology."""
    from .schemas import Profile

    item = require_download(repo)
    if item["role"] != "target":
        raise ValueError("A companion draft has no independent chat launch profile")
    model = next(
        (
            m
            for m in db.all_records("models")
            if m.get("catalog_id") == repo
            and (not model_id or m["id"] == model_id)
            and model_entry(m["path"])
        ),
        None,
    )
    installed = [r for r in db.all_records("runtimes") if runtime_matches(item, r)]
    active_id = db.get("engine", "active", {}).get("profile", {}).get("runtime_id")
    runtime = next(
        (r for r in installed if r["id"] == active_id), installed[0] if installed else {}
    )
    devices = gpu.snapshot()["gpus"]
    reasons = compatibility(item, runtime.get("id"), devices)
    notes = recommendations(item, devices)
    topology = item["gpu"]
    selected = recommended_devices(devices, topology)
    profile = None
    if topology["pp"] == 1:
        values = Profile(
            name=item["name"] + " · 默认",
            model_path=model["path"] if model else "/pending-download",
            runtime_id=runtime.get("id") or "pending-runtime",
            served_model_name=repo.split("/")[-1],
        ).model_dump()
        values.update(item.get("recommended", {}))
        values.update(
            {
                "catalog_id": repo,
                "source": "catalog-default",
                "model_path": model["path"] if model else "",
                "runtime_id": runtime.get("id") or "",
                # The verified topology sizes the preset; this machine's boards
                # decide what it actually runs on.
                "tensor_parallel_size": max(1, len(selected)),
                "gpu_uuids": [d["uuid"] for d in selected],
                "speculative_config": None,
            }
        )
        profile = values
    return {
        "catalog_id": repo,
        "model_id": model["id"] if model else None,
        "profile": profile,
        "recommended": item.get("recommended", {}),
        "gpu": topology,
        "runtime_versions": item["runtime_versions"],
        "runtime_options": [{"id": r["id"], "name": r["name"]} for r in installed],
        "reasons": reasons,
        "recommendations": notes,
        "can_create": bool(model and profile and not reasons),
    }


def create_default_profile(model_id):
    from .schemas import Profile

    model = db.get("models", model_id, {})
    if not model.get("catalog_id"):
        raise ValueError("This local model has no verified default launch recipe")
    defaults = launch_defaults(model["catalog_id"], model_id)
    if not defaults["can_create"]:
        raise ValueError(
            "; ".join(defaults["reasons"]) or "Finish the verified model download first"
        )
    profile = Profile.model_validate(defaults["profile"]).model_dump()
    validate_features(profile)
    id = (
        "catalog-"
        + hashlib.sha256((model_id + ":" + profile["runtime_id"]).encode()).hexdigest()[:24]
    )
    existing = next(
        (
            p
            for p in db.all_records("profiles")
            if p.get("source") == "catalog-default"
            and p.get("model_path") == profile["model_path"]
            and p.get("runtime_id") == profile["runtime_id"]
            and p.get("catalog_id") == profile["catalog_id"]
        ),
        None,
    )
    if existing:
        # Repeated downloads/visits must preserve the user's edits to this preset.
        validate_features(existing)
        return existing
    if db.get("profiles", id):
        # An edited preset may now target a different model. Never launch it from
        # this model's card or overwrite the user's repurposed configuration.
        id = db.uid()
    profile["id"] = id
    db.put("profiles", id, profile)
    return profile


def backfill_default_profiles() -> list[str]:
    """Create defaults for downloads completed before a compatible runtime appeared."""
    created = []
    for model in db.all_records("models"):
        if not model.get("catalog_id") or not model_entry(model.get("path", "")):
            continue
        try:
            profile = create_default_profile(model["id"])
        except ValueError:
            continue
        created.append(profile["id"])
    return created


def model_entry(path):
    model = next(
        (
            m
            for m in db.all_records("models")
            if m["path"] == str(Path(path).expanduser().resolve())
        ),
        {},
    )
    if not model.get("verified") or not model.get("file_identity"):
        return None
    folder = Path(model["path"])
    for name, identity in model["file_identity"].items():
        file = folder / name
        if not file.is_file() or [file.stat().st_size, file.stat().st_mtime_ns] != identity:
            return None
    return entry(model.get("catalog_id"))


def capabilities(path, runtime_id):
    runtime = db.get("runtimes", runtime_id, {})
    item = model_entry(path)
    if item and runtime_matches(item, runtime):
        accelerators = item.get("accelerators", [])
        return {
            "verified": True,
            "tool_parser": item.get("tool_parser"),
            "tool_reason": item.get("tool_reason")
            or (
                ""
                if item.get("tool_parser")
                else (
                    "当前检查点与运行环境没有已验证的工具解析器 / "
                    "No validated tool parser for this checkpoint and runtime"
                )
            ),
            "vision": item.get("vision", False),
            "vision_reason": item.get("vision_reason")
            or (
                ""
                if item.get("vision")
                else (
                    "当前检查点与运行环境尚未验证图片输入 / "
                    "Image input is not validated for this checkpoint and runtime"
                )
            ),
            "max_images": item.get("max_images", 4),
            "accelerators": accelerators,
            "mtp_token_options": [1, 2, 3, 4] if "mtp" in accelerators else [],
            "draft_repo_id": item.get("draft_repo_id"),
            "recommended": item.get("recommended", {}),
            "reason": item.get("vision_reason", ""),
            "evidence": item["evidence"],
        }
    # Local validation belongs to this installation and exact file identity, never
    # grants a family-wide or public download-directory verification badge.
    folder = Path(path).expanduser().resolve()
    cfg = folder / "config.json"
    if cfg.is_file():
        digest = hashlib.sha256(cfg.read_bytes()).hexdigest()
        record = db.get("model_validation", digest + ":" + runtime_id, {})
        inherited_from = None
        if not record or record.get("model_path") != str(folder):
            # The exact runtime id is not the identity of the inference stack: a
            # runtime may be upgraded in place or replaced by an equivalent build.
            family = runtime_family(runtime) or runtime_family(runtime_id)
            if family:
                with db.connect() as conn:
                    rows = conn.execute(
                        "SELECT id, data FROM records WHERE bucket=? AND id LIKE ?",
                        ("model_validation", digest + ":%"),
                    ).fetchall()
                for candidate_id, raw in rows:
                    candidate = json.loads(raw)
                    if candidate.get("model_path") != str(folder):
                        continue
                    source = candidate.get("runtime_family") or candidate_id.split(":", 1)[1]
                    if runtime_family(source) == family:
                        record, inherited_from = candidate, candidate_id.split(":", 1)[1]
                        break
        if record and record.get("model_path") == str(folder):
            files = {
                f.name: [f.stat().st_size, f.stat().st_mtime_ns]
                for f in folder.iterdir()
                if f.suffix in {".safetensors", ".bin", ".json"}
            }
            if files == record.get("files"):
                result = dict(record["capabilities"])
                if inherited_from:
                    result["inherited_from"] = inherited_from
                accelerators = result.get("accelerators", [])
                result.setdefault(
                    "mtp_token_options", [1, 2, 3, 4] if "mtp" in accelerators else []
                )
                result.setdefault(
                    "tool_reason",
                    ""
                    if result.get("tool_parser")
                    else "当前本地验证没有工具解析器 / Local validation has no tool parser",
                )
                result.setdefault(
                    "vision_reason",
                    ""
                    if result.get("vision")
                    else "当前本地验证没有图片输入 / Local validation has no image input",
                )
                return result
    return {
        "verified": False,
        "vision": False,
        "max_images": 0,
        "tool_parser": None,
        "tool_reason": (
            "当前检查点与运行环境尚未验证工具调用 / "
            "Tool calling is not validated for this checkpoint and runtime"
        ),
        "accelerators": [],
        "mtp_token_options": [],
        "vision_reason": (
            "当前检查点与运行环境尚未验证图片输入 / "
            "Image input is not validated for this checkpoint and runtime"
        ),
        "reason": (
            "尚无此检查点与运行环境的验证记录 / "
            "No validation for this checkpoint and runtime"
        ),
        "recommended": {},
    }


def validate_features(profile):
    selected = gpu.selected_devices(profile["gpu_uuids"]) if profile["gpu_uuids"] else []
    if profile["dtype"] == "bfloat16" and any(
        (d.get("compute_capability") or [0])[0] < 8 for d in selected
    ):
        raise ValueError("Selected GPUs do not support BF16; use FP16")
    # auto must not inherit BF16 from a V100 checkpoint's config.
    if profile["dtype"] == "auto" and any(d.get("compute_capability") == [7, 0] for d in selected):
        profile["dtype"] = "half"
    caps = capabilities(profile["model_path"], profile["runtime_id"])
    registered = next(
        (
            m
            for m in db.all_records("models")
            if m["path"] == str(Path(profile["model_path"]).expanduser().resolve())
        ),
        {},
    )
    if registered.get("role") == "draft":
        raise ValueError(
            "This is a companion draft checkpoint; select it in DFlash2 settings instead of launching it as a chat model"
        )
    if registered.get("catalog_id") and not profile.get("catalog_id"):
        profile["catalog_id"] = registered["catalog_id"]
    if profile.get("catalog_id"):
        item = model_entry(profile["model_path"])
        if not item or item["id"] != profile["catalog_id"]:
            raise ValueError("Verified checkpoint does not match the selected local model")
        reasons = compatibility(item, profile["runtime_id"], selected)
        if reasons:
            # Deviating from the verified topology is the operator's call; the
            # preset reports it as a recommendation instead of refusing to run.
            raise ValueError("; ".join(reasons))
    if profile.get("tool_calling") and (
        not caps.get("tool_parser") or profile.get("tool_parser") != caps["tool_parser"]
    ):
        raise ValueError("Tool parser is not validated for this checkpoint and runtime")
    if profile.get("vision_enabled") and not caps.get("vision"):
        raise ValueError("Vision is not validated for this checkpoint and runtime")
    if profile.get("vision_enabled") and profile["max_images"] > caps["max_images"]:
        raise ValueError("Image count exceeds the validated model limit")
    spec = profile.get("speculative_config")
    if spec:
        if spec.get("method") not in caps.get("accelerators", []):
            raise ValueError("Acceleration is not validated for this checkpoint and runtime")
        if spec.get("method") == "dflash":
            draft = Path(str(spec.get("model", ""))).expanduser()
            if (
                not draft.is_absolute()
                or not (draft / "config.json").is_file()
                or not (list(draft.glob("*.safetensors")) or list(draft.glob("*.bin")))
            ):
                raise ValueError(
                    "Download the DFlash2 draft from ModelScope and select its local directory"
                )
            expected = caps.get("draft_repo_id")
            registered = next(
                (m for m in db.all_records("models") if m["path"] == str(draft.resolve())), {}
            )
            if expected and registered.get("repo_id") != expected:
                raise ValueError("Draft does not match the verified DFlash2 recipe")
            spec.pop("revision", None)
        elif spec.get("method") == "mtp":
            count = spec.get("num_speculative_tokens")
            if isinstance(count, bool) or count not in caps.get("mtp_token_options", []):
                raise ValueError("MTP speculative tokens must be one of 1, 2, 3 or 4")
    return profile
