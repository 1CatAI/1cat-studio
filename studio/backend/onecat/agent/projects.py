# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import io
import os
import stat
import uuid
import zipfile
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

from .. import db
from ..config import state_root

MAX_FILE = 10 * 1024 * 1024
MAX_PROJECT = 256 * 1024 * 1024
EXCLUDED = {".git", "node_modules", ".venv", "__pycache__", ".codex"}


def get(project_id: str) -> dict:
    project = db.get("agent_projects", project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def root(project_id: str) -> Path:
    get(project_id)
    return state_root() / "agent/projects" / project_id / "workspace"


def parts(path: str) -> tuple[str, ...]:
    candidate = PurePosixPath(path)
    if (
        not path
        or "\x00" in path
        or "\\" in path
        or candidate.is_absolute()
        or any(p in {".", ".."} or p in EXCLUDED for p in candidate.parts)
        or len(path) > 500
        or not candidate.parts
    ):
        raise ValueError("Invalid project file path")
    return candidate.parts


@contextmanager
def parent_directory(project_id: str, path: str, *, create=False):
    # Walk directory descriptors with O_NOFOLLOW, including the leaf. Resolving a
    # Path then opening it permits symlink races while the agent edits files.
    names = parts(path)
    fd = os.open(root(project_id), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in names[:-1]:
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, names[-1]
    finally:
        os.close(fd)


def open_file(project_id: str, path: str):
    with parent_directory(project_id, path) as (fd, name):
        leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        info = os.fstat(leaf)
        if not stat.S_ISREG(info.st_mode):
            os.close(leaf)
            raise ValueError("Only regular project files are available")
        return os.fdopen(leaf, "rb")


def read(project_id, path):
    try:
        with open_file(project_id, path) as stream:
            data = stream.read(MAX_FILE + 1)
    except OSError as error:
        raise HTTPException(404, "File is unavailable or is a symbolic link") from error
    if len(data) > MAX_FILE:
        raise ValueError("File exceeds the 10 MiB preview/download limit")
    return data


def files(project_id):
    result = []

    def walk(fd, prefix=""):
        # Descriptor traversal also protects listing against directory-to-symlink
        # replacements while an Agent is modifying its workspace.
        for name in sorted(os.listdir(fd)):
            path = prefix + name
            if name in EXCLUDED or len(path) > 500:
                continue
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        if walk(child, path + "/"):
                            return True
                    finally:
                        os.close(child)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                if len(result) == 2000:
                    return True
                result.append({"path": path, "bytes": info.st_size, "modified": info.st_mtime})
        return False

    fd = os.open(root(project_id), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        truncated = walk(fd)
    finally:
        os.close(fd)
    return {"items": result, "truncated": truncated}


def export(project_id):
    listing = files(project_id)
    if listing["truncated"] or sum(f["bytes"] for f in listing["items"]) > MAX_PROJECT:
        raise ValueError("Project export is limited to 2,000 files / 256 MiB")
    output, total = io.BytesIO(), 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in listing["items"]:
            # Export uses the project budget, not the small source-view limit.
            # Stream entries to avoid another full-size copy of generated files.
            with (
                open_file(project_id, file["path"]) as source,
                archive.open(file["path"], "w") as target,
            ):
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_PROJECT:
                        raise ValueError("Project changed while exporting; limit exceeded")
                    target.write(chunk)
    return output.getvalue()


def upload(project_id, path, data):
    path = "/".join(parts(path))
    if len(data) > MAX_FILE:
        raise ValueError("Each file is limited to 10 MiB")
    listing = files(project_id)
    existing = next((f for f in listing["items"] if f["path"] == path), None)
    if (
        listing["truncated"]
        or (existing is None and len(listing["items"]) >= 2000)
        or sum(f["bytes"] for f in listing["items"])
        - (existing["bytes"] if existing else 0)
        + len(data)
        > MAX_PROJECT
    ):
        raise ValueError("Project upload is limited to 2,000 files / 256 MiB")
    with parent_directory(project_id, path, create=True) as (fd, name):
        mode = 0o600
        with suppress(FileNotFoundError):
            original = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(original.st_mode):
                raise ValueError("Only regular project files can be replaced")
            mode = stat.S_IMODE(original.st_mode) & 0o777
        temporary = ".onecat-upload-" + uuid.uuid4().hex
        try:
            leaf = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
            )
            with os.fdopen(leaf, "wb") as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=fd)
