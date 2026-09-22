# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Model choices and request controls grounded in installed weights and recipes."""

from __future__ import annotations

import ast
import json
import re
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from . import db


@lru_cache(maxsize=64)
def _template_support(files: tuple) -> tuple[bool, tuple[str, ...]]:
    supported = False
    efforts = ()
    for filename, _, _ in files:
        path = Path(filename)
        try:
            if path.stat().st_size > 10 * 1024 * 1024:
                continue
            text = path.read_text()
            if path.suffix == ".json":
                template = json.loads(text).get("chat_template", "")
                text = template if isinstance(template, str) else json.dumps(template)
            if "enable_thinking" in text:
                supported = True
            # Only advertise levels explicitly accepted by the deployed template.
            accepted = re.search(r"\b(?:resolved_)?reasoning_effort\s+not\s+in\s+(\([^)]*\))", text)
            if accepted:
                values = ast.literal_eval(accepted[1])
                if isinstance(values, (tuple, list)):
                    efforts = tuple(v for v in ("low", "medium", "high", "xhigh") if v in values)
        except (OSError, ValueError, SyntaxError):
            continue
    return supported, efforts


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
    supported, efforts = _template_support(tuple(files))
    return {
        "supported": supported,
        "efforts": list(efforts) if supported else [],
        "default_effort": "xhigh" if "xhigh" in efforts else (efforts[-1] if efforts else None),
        "reason": None
        if supported
        else "当前模型模板未声明思考开关 / This model template does not expose a thinking switch",
    }


def thinking_kwargs(profile: dict, thinking: bool | None, effort: str | None = None) -> dict:
    if thinking is None:
        return {}
    support = thinking_support(profile)
    if not support["supported"]:
        if thinking:
            raise HTTPException(
                409,
                "当前模型不支持切换思考，请选择支持此功能的模型 / Select a model with a thinking switch",
            )
        return {}
    result = {"enable_thinking": thinking}
    if thinking and effort is not None:
        if effort not in support["efforts"]:
            raise HTTPException(
                409, "当前模型不支持此思考强度 / This model does not support this thinking level"
            )
        result["reasoning_effort"] = effort
    return result


def identity(model: dict) -> dict:
    return {
        "name": model.get("name") or model.get("served_model_name") or "—",
        "repo_id": model.get("repo_id") or model.get("catalog_id"),
        "source": model.get("source"),
    }


def profile_identity(profile: dict) -> dict | None:
    if not profile.get("model_path"):
        return None
    path = Path(profile["model_path"]).expanduser().resolve()
    model = next(
        (m for m in db.all_records("models") if Path(m["path"]).expanduser().resolve() == path),
        None,
    )
    return identity(model or profile)


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
                **identity(model),
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
