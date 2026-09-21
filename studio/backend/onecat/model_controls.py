# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Model choices and request controls grounded in installed weights and recipes."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from . import db


@lru_cache(maxsize=64)
def _template_support(files: tuple) -> bool:
    for filename, _, _ in files:
        path = Path(filename)
        try:
            if path.stat().st_size > 10 * 1024 * 1024:
                continue
            text = path.read_text()
            if path.suffix == ".json":
                template = json.loads(text).get("chat_template", "")
                text = json.dumps(template)
            if "enable_thinking" in text:
                return True
        except (OSError, ValueError):
            continue
    return False


def thinking_support(profile: dict) -> dict:
    path = profile.get("model_path")
    files = []
    if path:
        for name in ("chat_template.jinja", "tokenizer_config.json"):
            file = Path(path).expanduser() / name
            try:
                stat = file.stat()
                files.append((str(file), stat.st_mtime_ns, stat.st_size))
            except OSError:
                pass
    supported = _template_support(tuple(files))
    return {
        "supported": supported,
        "reason": None
        if supported
        else "当前模型模板未声明思考开关 / This model template does not expose a thinking switch",
    }


def thinking_kwargs(profile: dict, thinking: bool | None) -> dict:
    if thinking is None:
        return {}
    if not thinking_support(profile)["supported"]:
        if thinking:
            raise HTTPException(
                409,
                "当前模型不支持切换思考，请选择支持此功能的模型 / Select a model with a thinking switch",
            )
        return {}
    return {"enable_thinking": thinking}


def choices(agent: bool = False) -> dict:
    from .catalog import launch_defaults

    state = db.get("engine", "active", {})
    active_profile = state.get("profile") or {}
    profiles = db.all_records("profiles")
    items = []
    for model in db.all_records("models"):
        if model.get("state") != "downloaded" or model.get("role") == "draft":
            continue
        path = Path(model["path"]).expanduser().resolve()
        matching = [
            p
            for p in profiles
            if p.get("model_path")
            and Path(p["model_path"]).expanduser().resolve() == path
            and (not agent or p.get("tool_calling"))
        ]
        matching.sort(
            key=lambda p: (
                p["id"] != model.get("default_profile_id"),
                p["id"] != state.get("profile_id"),
                p.get("source") != "catalog-default",
                p["name"],
            )
        )
        reason = None
        can_default = False
        if not path.is_dir() or not (path / "config.json").is_file():
            reason = "模型文件已移动或不完整 / Model files are missing or incomplete"
            matching = []
        elif not matching:
            if model.get("catalog_id"):
                try:
                    defaults = launch_defaults(model["catalog_id"], model["id"])
                    can_default = defaults["can_create"] and (
                        not agent or bool((defaults["profile"] or {}).get("tool_calling"))
                    )
                    if not can_default:
                        reason = " · ".join(defaults["reasons"]) or (
                            "缺少工具调用配置 / A tool-calling recipe is required"
                            if agent
                            else "默认配置暂不可用 / Default recipe unavailable"
                        )
                except ValueError as error:
                    reason = str(error)
            else:
                reason = "缺少适配此模型的启动配置 / No compatible launch recipe for this model"
        items.append(
            {
                "id": model["id"],
                "name": model["name"],
                "default_profile_id": model.get("default_profile_id"),
                "bytes": model.get("bytes"),
                "active": bool(
                    active_profile.get("model_path")
                    and Path(active_profile["model_path"]).expanduser().resolve() == path
                ),
                "profiles": [{"id": p["id"], "name": p["name"]} for p in matching],
                "can_load": bool(matching or can_default),
                "reason": reason,
                "thinking": thinking_support({"model_path": str(path)}),
            }
        )
    return {"items": items, "thinking": thinking_support(state.get("profile") or {})}


def resolve_profile(model_id: str, profile_id: str | None = None, agent: bool = False) -> dict:
    """Never accept an unrelated profile or silently load companion draft weights."""
    from .catalog import create_default_profile
    from .engine import validate_profile

    choice = next((m for m in choices(agent)["items"] if m["id"] == model_id), None)
    if not choice:
        raise HTTPException(404, "已下载模型不存在 / Downloaded model not found")
    if not choice["can_load"]:
        raise HTTPException(409, choice["reason"])
    if profile_id and not any(p["id"] == profile_id for p in choice["profiles"]):
        raise HTTPException(
            409,
            "预设与模型或所需能力不匹配 / Profile does not match the model or required capabilities",
        )
    profile = (
        db.get("profiles", profile_id or choice["profiles"][0]["id"])
        if choice["profiles"]
        else create_default_profile(model_id)
    )
    if agent and not profile.get("tool_calling"):
        raise HTTPException(409, "Agent 需要工具调用 / Agent requires tool calling")
    # Runtimes are replaceable; a preset follows the validated runtime that is installed.
    from . import activation

    activation.ensure_runtime(profile)
    # All launch checks precede the job that would stop the current service.
    validate_profile(profile)
    return profile
