#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Refresh Studio's offline recipe from the installed release wheel."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "studio/backend"))
from onecat.sm70_profiles import MODEL_IDS, PROFILE_NAME, recommendation  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-python", required=True)
    args = parser.parse_args()
    result = subprocess.run(
        [
            args.runtime_python,
            "-m",
            "vllm.sm70_profiles",
            "show",
            PROFILE_NAME,
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    recipe = json.loads(result.stdout)
    if recipe.get("name") != PROFILE_NAME:
        raise ValueError("Unexpected runtime profile")
    values = recommendation({"capabilities": {"sm70_profiles": {PROFILE_NAME: recipe}}})
    data = ROOT / "studio/backend/onecat/data"
    (data / "sm70-qwen38-release-profile.json").write_text(
        json.dumps(recipe, indent=2) + "\n"
    )
    catalog = data / "verified-models-v1.json"
    rows = json.loads(catalog.read_text())
    for row in rows["entries"]:
        if row["id"] in MODEL_IDS:
            row["recommended"].update(values)
        elif row["id"] == recipe["draft"]["repo"]:
            row["revision"] = recipe["draft"]["modelscope_revision"]
            row["upstream_revision"] = recipe["draft"]["revision"]
    catalog.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
