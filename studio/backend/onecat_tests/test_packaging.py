# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Release packaging must never silently reuse stale frontend assets."""

import importlib.util
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest


def test_failed_frontend_build_cannot_publish_existing_dist(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / "scripts/package.py"
    spec = importlib.util.spec_from_file_location("studio_package_test", script)
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    studio = tmp_path / "studio"
    dist = studio / "frontend/dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("old frontend which must not be published")
    npm = tmp_path / "npm"
    npm.write_text("#!/bin/sh\nexit 23\n")
    npm.chmod(0o755)
    output = tmp_path / "release"
    monkeypatch.setattr(package, "STUDIO", studio)
    monkeypatch.setattr(package.shutil, "which", lambda name: str(npm) if name == "npm" else None)
    monkeypatch.setattr(sys, "argv", [str(script), "--output", str(output)])
    with pytest.raises(subprocess.CalledProcessError) as failed:
        package.main()
    assert failed.value.returncode == 23
    assert not output.exists(), "A failed build must not produce a stale release"


def package_module():
    script = Path(__file__).resolve().parents[2] / "scripts/package.py"
    spec = importlib.util.spec_from_file_location("studio_source_package_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_archive_is_closed_and_repackable_without_git(tmp_path):
    package = package_module()
    root = tmp_path / "source"
    included = ["LICENSE", "studio/LICENSE", "SOURCE_ORIGIN.md",
                "studio/backend/onecat/app.py", "studio/frontend/src/onecat/app.tsx",
                "studio/third_party/example/LICENSE"]
    excluded = ["unsloth/__init__.py", "studio/backend/storage/studio_db.py",
                "studio/frontend/src/components/button.tsx", ".env",
                "studio/backend/onecat/__pycache__/app.pyc", "studio/vendor/runtime/file"]
    for name in included + excluded:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    archive = tmp_path / "source.tar.gz"
    package.source_archive(archive, root)
    with tarfile.open(archive) as source:
        assert set(source.getnames()) == {"onecat-studio-source/" + name for name in included}
        source.extractall(tmp_path / "extracted", filter="data")
    extracted = tmp_path / "extracted/onecat-studio-source"
    assert {p.relative_to(extracted).as_posix() for p in package.source_paths(extracted)} == set(included)


def test_source_archive_rejects_external_symlinks(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    external = tmp_path / "secret"
    external.write_text("must not be published")
    (root / "LICENSE").symlink_to(external)
    with pytest.raises(ValueError, match="outside the release"):
        package_module().source_paths(root)
