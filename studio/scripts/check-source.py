#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Reject imports and release files that cross the independent source boundary."""
import ast
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def check(root=ROOT):
    failures = []
    backend = root / "studio/backend/onecat"
    frontend = root / "studio/frontend/src/onecat"
    count = 0
    legacy = {"storage", "utils", "loggers", "unsloth", "unsloth_cli", "auth"}
    for path in backend.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and not node.level and node.module:
                if node.module.split(".")[0] in legacy:
                    failures.append(f"{path.relative_to(root)}:{node.lineno}: legacy import {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in legacy:
                        failures.append(f"{path.relative_to(root)}:{node.lineno}: legacy import {alias.name}")
    for folder in [backend, frontend, root / "studio/scripts"]:
        for path in folder.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx", ".css", ".sh", ".mjs"}:
                continue
            count += 1
            text = path.read_text()
            if "Copyright 2026-present the Unsloth AI Inc." in text and path != Path(__file__):
                failures.append(f"{path.relative_to(root)}: upstream implementation attribution")
            if "SPDX-License-Identifier: AGPL-3.0" in text and path != Path(__file__):
                failures.append(f"{path.relative_to(root)}: old application license")
            if path.is_relative_to(frontend):
                for target in re.findall(r'''(?:from\s+|import\s*\(\s*)["']([^"']+)["']''', text):
                    if target.startswith("@/") and not target.startswith("@/onecat/"):
                        failures.append(f"{path.relative_to(root)}: external source alias {target}")
                    if target.startswith(".") and not (path.parent / target).resolve().is_relative_to(frontend):
                        failures.append(f"{path.relative_to(root)}: external source path {target}")
    spec = importlib.util.spec_from_file_location("onecat_package_boundary", root / "studio/scripts/package.py")
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    files = package.source_paths(root)
    for path in files:
        name = path.relative_to(root).as_posix()
        if name.startswith(("unsloth/", "unsloth_cli/", "studio/backend/storage/", "studio/frontend/src/components/")):
            failures.append("Legacy release file: " + name)
    return {"application_files": count, "release_source_files": len(files), "failures": failures}


if __name__ == "__main__":
    result = check()
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result["failures"]))
