#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Build a Linux x86_64 offline manager bundle; model/runtime downloads stay separate."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDIO = ROOT / "studio"


def source_paths(root=ROOT):
    """The independent release has a closed source boundary, also without Git."""
    top = ["LICENSE", "README.md", "SOURCE_ORIGIN.md", ".gitignore"]
    groups = ["studio/backend/onecat", "studio/backend/onecat_tests", "studio/scripts",
              "studio/frontend/src/onecat", "studio/frontend/tests", "studio/frontend/scripts",
              "studio/third_party", "studio/docs", "studio/releases", ".github/workflows"]
    candidates = [root / name for name in top]
    candidates += list((root / "studio").glob("*.md"))
    candidates += [root / "studio/LICENSE", root / "studio/pyproject.toml", root / "studio/requirements.lock"]
    frontend = root / "studio/frontend"
    candidates += [frontend / name for name in ["package.json", "package-lock.json", "index.html",
                   "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json", "tsconfig.onecat.json",
                   "vite.onecat.config.ts", "src/vite-env.d.ts", "public/onecat.jpg", "public/crypto-boot.js"]]
    for group in groups:
        candidates.extend((root / group).rglob("*"))
    result = []
    for path in sorted(set(candidates)):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Source file points outside the release: " + str(path))
        result.append(path)
    return result


def source_archive(destination, root=ROOT):
    with tarfile.open(destination, "w:gz") as archive:
        for path in source_paths(root):
            archive.add(path, arcname=str(Path("onecat-studio-source") / path.relative_to(root)), recursive=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".artifacts/releases")
    args = parser.parse_args()
    # Never publish an old dist beside newer source/backend code. The installer
    # must receive assets built from the same working tree being archived below.
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("Node.js/npm is required to build the Studio frontend")
    subprocess.run([npm, "run", "build:onecat"], cwd=STUDIO / "frontend", check=True)
    subprocess.run([shutil.which("python3"), str(STUDIO / "scripts/check-source.py")], check=True)
    subprocess.run([shutil.which("python3"), str(STUDIO / "scripts/prepare-agent.py")], check=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = ROOT / ".artifacts/package-base"
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is required to build the offline bundle")
    env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(base / "python")}
    subprocess.run([uv, "python", "install", "--no-bin", "3.12"], env=env, check=True)
    python = subprocess.check_output(
        [uv, "python", "find", "--managed-python", "3.12"], env=env, text=True
    ).strip()
    if not (base / "env").exists():
        subprocess.run(
            [uv, "venv", "--relocatable", "--python", python, str(base / "env")], check=True
        )
    subprocess.run(
        [
            uv,
            "pip",
            "sync",
            "--python",
            str(base / "env/bin/python"),
            "--require-hashes",
            str(STUDIO / "requirements.lock"),
        ],
        check=True,
    )
    digest = hashlib.sha256()
    for path in source_paths():
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    version = "0.4.0-" + digest.hexdigest()[:10]
    stage = output / ("onecat-studio-" + version)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    shutil.copytree(base / "python", stage / "python", symlinks=True)
    shutil.copytree(base / "env", stage / "env", symlinks=True)
    shutil.copy2(uv, stage / "uv")
    for name in ("onecat",):
        shutil.copytree(
            STUDIO / "backend" / name,
            stage / "studio/backend" / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shutil.copytree(STUDIO / "frontend/dist", stage / "studio/frontend/dist")
    shutil.copytree(STUDIO / "vendor/codex", stage / "studio/vendor/codex", symlinks=True)
    shutil.copytree(
        STUDIO / "scripts", stage / "scripts", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copy2(ROOT / "LICENSE", stage / "LICENSE")
    for name in ("requirements.lock", "OPERATIONS.md", "NOTICE.md", "AGENT.md"):
        if (STUDIO / name).exists():
            shutil.copy2(STUDIO / name, stage / name)
    if (STUDIO / "third_party").is_dir():
        shutil.copytree(STUDIO / "third_party", stage / "third_party")
    if (STUDIO / "CREATIVE.md").is_file():
        shutil.copy2(STUDIO / "CREATIVE.md", stage / "CREATIVE.md")
    archive_source = stage / "studio/frontend/dist/source.tar.gz"
    source_archive(archive_source)
    home = Path(
        subprocess.check_output([python, "-c", "import sys;print(sys.prefix)"], text=True).strip()
    )
    manifest = {
        "format": "onecat-studio-linux-v1",
        "version": version,
        "original_prefix": str(base),
        "python_home": str(home.relative_to(base)),
        "source_license": "LicenseRef-1Cat-Community-1.0",
        "implementation": "onecat-independent-v1",
    }
    (stage / "manifest.json").write_text(json.dumps(manifest, indent=2))

    def portable(member):
        if member.issym() and os.path.isabs(member.linkname):
            target = Path(member.linkname)
            if target.is_relative_to(base):
                local_target = stage / target.relative_to(base)
                member.linkname = os.path.relpath(
                    local_target, (stage / Path(member.name).relative_to(stage.name)).parent
                )
            elif not target.is_relative_to(stage):
                raise ValueError("External symlink: " + member.name)
        return member

    target = output / (stage.name + "-linux-x86_64.tar.gz")
    with tarfile.open(target, "w:gz", dereference=False) as archive:
        archive.add(stage, arcname=stage.name, filter=portable)
    with target.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    target.with_suffix(target.suffix + ".sha256").write_text(sha + "  " + target.name + "\n")
    print(
        json.dumps(
            {
                "archive": str(target),
                "sha256": sha,
                "version": version,
                "bytes": target.stat().st_size,
            }
        )
    )


if __name__ == "__main__":
    main()
