# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import contextlib
import email
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tarfile
import time
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import platform_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from . import db
from .config import state_root
from .jobs import Job

RELEASE = {
    "id": "1cat-vllm-1.3.0",
    "version": "1.3.0",
    "python": "3.12",
    "url": "https://github.com/1CatAI/1Cat-vLLM/releases/download/v1.3.0/1cat_vllm-1.3.0-cp312-cp312-linux_x86_64.whl",
    "sha256": "2bdb14a9c44f83ee6a766d88ed0d85b11390d6f5d65747e8dbe80a8e2d5d63e0",
    "platform": "linux_x86_64",
    "compute_capabilities": [[7, 0]],
    "torch": "2.10.0",
    "cuda": "12.8",
}

RELEASES = [
    {
        **RELEASE,
        "id": "1cat-vllm-1.5.0",
        "version": "1.5.0",
        "url": "https://github.com/1CatAI/1Cat-vLLM/releases/download/v1.5.0/1cat_vllm-1.5.0-cp312-cp312-linux_x86_64.whl",
        "sha256": "2a4d6bee4e19d315b142f2c563059f3064ddeeca563a6bdc828c33e1073c825b",
        "evidence": "https://github.com/1CatAI/1Cat-vLLM/releases/tag/v1.5.0",
    },
    RELEASE,
]

PR417_CONFIG_SHA256 = "4f606bf0edb27fec22cd993cf5c46c3824d90950ea6c62b296ed3db7ab08c16c"
PR417_PYTHON_TREE_SHA256 = "578b596c6bfbab9d9d5701037d68c2cbc379417ba78657c40f079b270696a635"

INSPECT = r"""
import hashlib, importlib.metadata, json, pathlib, sys
import torch, vllm
root=pathlib.Path(vllm.__file__).resolve().parent
fingerprints={}
for name in ['config/vllm.py','model_executor/layers/quantization/compressed_tensors/schemes/compressed_tensors_w4a4_nvfp4.py']:
 p=root/name
 if p.is_file(): fingerprints[name]=hashlib.sha256(p.read_bytes()).hexdigest()
tree=hashlib.sha256()
for p in sorted(root.rglob('*.py')):
 tree.update(str(p.relative_to(root)).encode());tree.update(p.read_bytes())
fingerprints['python_tree_sha256']=tree.hexdigest()
native={str(p.relative_to(root)):[p.stat().st_size,p.stat().st_mtime_ns] for p in root.rglob('*.so')}
distributions={}
for name in ['1cat-vllm','vllm']:
 try: distributions[name]=importlib.metadata.version(name)
 except importlib.metadata.PackageNotFoundError: pass
creative_fastpath={}
if (root/'video/fastpath.py').is_file():
 from vllm.video.fastpath import studio_capabilities
 creative_fastpath=studio_capabilities()
print('ONECAT_RUNTIME='+json.dumps({'python_version':sys.version.split()[0],
 'prompt_token_details':(root/'entrypoints/openai/cli_args.py').is_file() and 'enable_prompt_tokens_details' in (root/'entrypoints/openai/cli_args.py').read_text(),
 'python_base':sys.base_prefix,'vllm_version':getattr(vllm,'__version__','unknown'),
 'distribution_version':distributions.get('1cat-vllm') or distributions.get('vllm'),
 'distributions':distributions,
 'torch_version':torch.__version__,'cuda_version':torch.version.cuda,
 'source_path':str(root),'source_fingerprints':fingerprints,'native_files':native,
 'h3_fastpath':creative_fastpath}))
"""


def run_command(job: Job, argv: list[str], env=None, cwd=None, timeout=3600):
    job.check_cancelled()
    process = subprocess.Popen(
        argv, env=env, cwd=cwd, stdout=None, stderr=subprocess.STDOUT, start_new_session=True
    )
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            job.check_cancelled()
            if time.monotonic() > deadline:
                raise RuntimeError("Command exceeded its time limit")
            time.sleep(0.3)
        if process.returncode:
            raise RuntimeError(
                f"{Path(argv[0]).name} failed with exit code {process.returncode}; see task log"
            )
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise


def inspect_runtime(record: dict) -> dict:
    python = Path(record["python_path"]).expanduser().absolute()
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("Python executable does not exist or is not executable")
    env = {**os.environ, **record.get("environment", {}), "PYTHONNOUSERSITE": "1"}
    if "PYTHONPATH" not in record.get("environment", {}):
        env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [str(python), "-c", INSPECT],
        cwd=record.get("working_directory") or None,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    marker = next(
        (line[15:] for line in result.stdout.splitlines() if line.startswith("ONECAT_RUNTIME=")),
        None,
    )
    if result.returncode or marker is None:
        raise ValueError(
            "Runtime could not import torch/vllm: " + (result.stderr or result.stdout)[-1800:]
        )
    capabilities = json.loads(marker)
    expected = record.get("release", {})
    if expected.get("torch_install_version"):
        if Version(capabilities["torch_version"]) != Version(expected["torch_install_version"]):
            raise ValueError("Installed PyTorch does not match the wheel's pinned CUDA build")
        if capabilities["cuda_version"] != expected["cuda"]:
            raise ValueError("Installed PyTorch CUDA ABI does not match the release")
    fingerprints = capabilities["source_fingerprints"]
    if (
        fingerprints.get("config/vllm.py") == PR417_CONFIG_SHA256
        and fingerprints.get("python_tree_sha256") == PR417_PYTHON_TREE_SHA256
    ):
        capabilities["source_snapshot"] = "pr417-a09e332bcd"
    record.update(
        {
            "id": record.get("id") or db.uid(),
            "python_path": str(python),
            "validated": True,
            "capabilities": capabilities,
            "validated_at": time.time(),
        }
    )
    db.put("runtimes", record["id"], record)
    return record


def refresh_incomplete_metadata() -> list[str]:
    """Backfill package versions for source-overlay runtimes imported by older Studio builds."""
    refreshed = []
    for record in db.all_records("runtimes"):
        capabilities = record.get("capabilities", {})
        if not record.get("validated") or capabilities.get("distribution_version"):
            continue
        try:
            refreshed.append(inspect_runtime(record)["id"])
        except (OSError, subprocess.SubprocessError, ValueError):
            # Keep the last validated record available if its environment is
            # temporarily inaccessible while Studio starts.
            continue
    return refreshed


def download_file(job: Job, url: str, target: Path, expected_sha: str | None = None):
    partial = target.with_suffix(target.suffix + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    offset = partial.stat().st_size if partial.exists() else 0
    job.check_cancelled()
    if offset and expected_sha:
        with partial.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == expected_sha:
                partial.replace(target)
                job.update(
                    "downloading",
                    100,
                    downloaded_bytes=offset,
                    expected_bytes=offset,
                    bytes_per_second=0,
                )
                return
    with requests.get(
        url,
        headers={
            "Accept-Encoding": "identity",
            **({"Range": f"bytes={offset}-"} if offset else {}),
        },
        stream=True,
        timeout=(20, 60),
    ) as response:
        if response.status_code == 416 and offset:
            partial.unlink()
            return download_file(job, url, target, expected_sha)
        response.raise_for_status()
        if response.status_code == 206:
            content_range = re.fullmatch(
                r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("content-range", "")
            )
            if (
                not content_range
                or int(content_range[1]) != offset
                or not offset <= int(content_range[2]) < int(content_range[3])
            ):
                raise ValueError("Download response range does not match the saved partial file")
            total = int(content_range[3])
        else:
            offset = 0
            total = int(response.headers.get("content-length", 0))
        started, initial = time.monotonic(), offset
        last = 0
        with partial.open("ab" if offset else "wb") as stream:
            for chunk in response.iter_content(1024 * 1024):
                job.check_cancelled()
                stream.write(chunk)
                offset += len(chunk)
                now = time.monotonic()
                if now - last > 0.5:
                    rate = (offset - initial) / max(0.01, now - started)
                    job.update(
                        "downloading",
                        100 * offset / total if total else 0,
                        downloaded_bytes=offset,
                        expected_bytes=total,
                        bytes_per_second=rate,
                    )
                    last = now
    if total and offset != total:
        raise ValueError("Download is incomplete; retry to resume the saved partial file")
    if expected_sha:
        with partial.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected_sha:
            partial.unlink()
            raise ValueError("Downloaded runtime checksum does not match the release manifest")
    partial.replace(target)
    job.update(
        "downloading",
        100,
        downloaded_bytes=offset,
        expected_bytes=offset,
        bytes_per_second=(offset - initial) / max(0.01, time.monotonic() - started),
    )


def read_wheel_metadata(path: Path, asset: dict):
    with zipfile.ZipFile(path) as archive:
        metadata = [i for i in archive.infolist() if i.filename.endswith(".dist-info/METADATA")]
        if len(metadata) != 1 or metadata[0].file_size > 2 * 1024 * 1024:
            raise ValueError("Wheel must contain one bounded METADATA file")
        info = email.message_from_bytes(archive.read(metadata[0]))
    if canonicalize_name(info.get("Name", "")) != asset["package"]:
        raise ValueError("Wheel package name does not match its published filename")
    Version(info.get("Version", ""))
    return info


def normalize_wheel_path(path: Path, asset: dict) -> Path:
    # Some historical releases mislabel the version in the filename. Preserve the
    # SHA256-verified bytes, and give the installer a metadata-consistent local name.
    info = read_wheel_metadata(path, asset)
    version = Version(info["Version"])
    if version == Version(asset["package_version"]):
        return path
    parts = asset["name"][:-4].split("-")
    parts[1] = str(version)
    destination = path.parent / "install" / ("-".join(parts) + ".whl")
    destination.parent.mkdir(exist_ok=True)
    if destination.exists():
        with destination.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == asset["sha256"]:
                return destination
        destination.unlink()
    os.link(path, destination)
    return destination


def wheel_dependencies(
    paths: list[Path], assets: list[dict], python_version: str, cuda: str
) -> dict[str, str]:
    """Read wheel metadata as data; never import a downloaded package to inspect it."""
    environment = {
        **default_environment(),
        "python_version": ".".join(python_version.split(".")[:2]),
        "python_full_version": python_version,
        "extra": "",
    }
    requirements = []
    for path, asset in zip(paths, assets, strict=True):
        info = read_wheel_metadata(path, asset)
        if python_version not in SpecifierSet(info.get("Requires-Python", "")):
            raise ValueError(
                "Wheel requires a different Python version: " + info["Requires-Python"]
            )
        for value in info.get_all("Requires-Dist", []):
            requirement = Requirement(value)
            if not requirement.marker or requirement.marker.evaluate(environment):
                requirements.append(requirement)
    pinned = {}
    for name in ("torch", "torchvision", "torchaudio"):
        relevant = [r for r in requirements if canonicalize_name(r.name) == name]
        if not relevant:
            continue
        pins = {
            s.version
            for r in relevant
            for s in r.specifier
            if s.operator == "==" and "*" not in s.version
        }
        suffix = "cu" + cuda.replace(".", "")
        for requirement in relevant:
            if not requirement.url:
                continue
            url = urlsplit(requirement.url)
            if (
                url.scheme != "https"
                or url.netloc != "download.pytorch.org"
                or not url.path.startswith(f"/whl/{suffix}/")
            ):
                raise ValueError(f"{name} direct wheel URL is not an official matching CUDA build")
            package, version, _, tags = parse_wheel_filename(unquote(url.path.rsplit("/", 1)[-1]))
            if package != name or not any(
                tag.interpreter == "cp" + environment["python_version"].replace(".", "")
                and tag.platform in set(platform_tags())
                for tag in tags
            ):
                raise ValueError(f"{name} direct wheel has incompatible package/Python tags")
            pins.add(str(version))
        targets = set()
        for pin in pins:
            version = Version(pin)
            if version.local and version.local != suffix:
                raise ValueError(f"{name} wheel dependency and release CUDA build disagree")
            targets.add(version.public + "+" + suffix)
        if len(targets) != 1:
            raise ValueError(f"Wheel does not pin one unambiguous {name} version")
        target = targets.pop()
        if any(target not in r.specifier for r in relevant):
            raise ValueError(f"Conflicting {name} requirements in companion wheels")
        pinned[name] = target
    if "torch" not in pinned:
        raise ValueError(
            "Wheel metadata does not pin PyTorch; automatic installation cannot choose its ABI"
        )
    return pinned


def install(job: Job):
    from . import gpu, runtime_releases

    release = job.payload.get("release")
    if not release:
        release = runtime_releases.resolve(
            job.payload.get("release_id", RELEASES[0]["id"]), gpu.snapshot()["gpus"]
        )
    problems = runtime_releases.availability(release, gpu.snapshot()["gpus"])
    if problems:
        raise ValueError("; ".join(problems))
    if not re.fullmatch(r"(?:github-\d+-[a-f0-9]{12}|1cat-vllm-[\d.]+)", release["id"]):
        raise ValueError("Invalid runtime identifier")
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is missing; use the 1Cat installer to prepare the manager")
    root = state_root() / "runtimes"
    root.mkdir(exist_ok=True)
    # Separate worker processes, including retries, cannot mutate one environment together.
    with (root / (release["id"] + ".lock")).open("a") as lock:
        while True:
            job.check_cancelled()
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                job.update("waiting_for_runtime", 0)
                time.sleep(0.3)
        existing = runtime_releases.installed_runtime(release)
        if existing:
            if not Path(existing["python_path"]).is_file():
                raise ValueError(
                    "Registered environment is missing its Python executable; import a repaired environment"
                )
            return {"runtime_id": existing["id"], "already_installed": True}
        folder = root / release["id"]
        for runtime in db.all_records("runtimes"):
            if Path(runtime["python_path"]).is_relative_to(folder):
                raise ValueError(
                    "This directory is already referenced by an environment; it will not be overwritten"
                )
        if shutil.disk_usage(root).free < 20 * 1024**3:
            raise RuntimeError(
                "Runtime installation requires at least 20 GiB of available disk space"
            )
        folder.mkdir(exist_ok=True)
        paths = []
        for index, asset in enumerate(release["assets"]):
            if not runtime_releases.asset_url(asset["url"], asset["name"]) or not re.fullmatch(
                r"[a-f0-9]{64}", asset.get("sha256") or ""
            ):
                raise ValueError("Release asset URL or SHA256 is invalid")
            wheel = state_root() / "downloads" / release["id"] / asset["name"]
            job.update(
                "downloading_runtime",
                0,
                runtime_release_id=release["id"],
                runtime_version=release["version"],
                asset_name=asset["name"],
                asset_index=index + 1,
                asset_count=len(release["assets"]),
                downloaded_bytes=0,
                expected_bytes=asset.get("bytes", 0),
                bytes_per_second=None,
            )
            cached = False
            if wheel.is_file():
                with wheel.open("rb") as stream:
                    cached = hashlib.file_digest(stream, "sha256").hexdigest() == asset["sha256"]
            if not cached:
                download_file(job, asset["url"], wheel, asset["sha256"])
            paths.append(wheel)
        env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(folder / "python")}
        # Installation subprocesses must not inherit another inference checkout or environment.
        for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            env.pop(name, None)
        job.update("installing_python", 0, bytes_per_second=None)
        run_command(
            job, [uv, "python", "install", "--no-bin", release["python"]], env=env, cwd=folder
        )
        found = subprocess.run(
            [uv, "python", "find", "--managed-python", release["python"]],
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout.strip()
        version = subprocess.run(
            [found, "-c", "import platform; print(platform.python_version())"],
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout.strip()
        job.update("checking_runtime_wheels", 0)
        pinned = wheel_dependencies(paths, release["assets"], version, release["cuda"])
        paths = [
            normalize_wheel_path(path, asset)
            for path, asset in zip(paths, release["assets"], strict=True)
        ]
        release = {
            **release,
            "torch_install_version": pinned["torch"],
            "package_version": str(read_wheel_metadata(paths[0], release["assets"][0])["Version"]),
        }
        run_command(
            job,
            [
                uv,
                "venv",
                "--relocatable",
                "--allow-existing",
                "--python",
                found,
                str(folder / "env"),
            ],
            env=env,
            cwd=folder,
        )
        python = folder / "env" / "bin" / "python"
        constraints = folder / "torch-constraints.txt"
        constraints.write_text(
            "\n".join(f"{name}=={version}" for name, version in pinned.items()) + "\n"
        )
        job.update("installing_torch", 0)
        run_command(
            job,
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                *[f"{name}=={version}" for name, version in pinned.items()],
                "--index-url",
                "https://download.pytorch.org/whl/cu" + release["cuda"].replace(".", ""),
            ],
            env=env,
            cwd=folder,
        )
        job.update("installing_dependencies", 0)
        run_command(
            job,
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--constraint",
                str(constraints),
                *map(str, paths),
            ],
            env=env,
            cwd=folder,
        )
        job.update("validating", 0)
        record = inspect_runtime(
            {
                "id": release["id"],
                "name": "1Cat-vLLM " + release["version"],
                "python_path": str(python),
                "environment": {},
                "managed": True,
                "install_directory": str(folder),
                "release": release,
            }
        )
        return {"runtime_id": record["id"]}


def extract_archive(source: Path, destination: Path):
    with tarfile.open(source) as archive:
        for member in archive.getmembers():
            path = (destination / member.name).resolve()
            if not path.is_relative_to(destination.resolve()):
                raise ValueError("Archive contains a path outside the installation directory")
            if member.isdev() or member.isfifo():
                raise ValueError("Archive contains a special device file")
        archive.extractall(destination, filter="data")


def import_offline(job: Job):
    source = Path(job.payload["path"]).expanduser().resolve()
    if not source.is_file():
        raise ValueError("Archive not found")
    folder = state_root() / "runtimes" / db.uid()
    folder.mkdir()
    job.update("extracting", 10)
    extract_archive(source, folder)
    manifest = json.loads((folder / "onecat-runtime.json").read_text())
    if manifest.get("format") != "onecat-runtime-v1":
        raise ValueError("Not a 1Cat runtime archive")
    original = manifest["original_prefix"]
    job.check_cancelled()
    # Managed uv distributions carry their base Python inside the runtime archive.
    python_home = folder / manifest["python_home"]
    if not python_home.resolve().is_relative_to(folder):
        raise ValueError("Invalid Python home in runtime manifest")
    cfg = folder / "env" / "pyvenv.cfg"
    cfg.write_text(cfg.read_text().replace(original, str(folder)))
    python = folder / "env" / "bin" / "python"
    if python.is_symlink():
        python.unlink()
    python.symlink_to(os.path.relpath(python_home / "bin" / "python3.12", python.parent))
    for script in (folder / "env" / "bin").iterdir():
        if script.is_file() and not script.is_symlink() and script.stat().st_size < 1024 * 1024:
            content = script.read_bytes()
            if content.startswith(b"#!"):
                script.write_bytes(content.replace(original.encode(), str(folder).encode()))
    job.update("validating", 90)
    record = inspect_runtime(
        {
            "name": manifest.get("name", "Offline runtime"),
            "python_path": str(python),
            "environment": {},
            "managed": True,
            "install_directory": str(folder),
        }
    )
    return {"runtime_id": record["id"]}


def export_runtime(job: Job):
    runtime = db.get("runtimes", job.payload["runtime_id"])
    if not runtime or not runtime.get("managed"):
        raise ValueError("Only Studio-installed runtimes support portable offline export")
    folder = Path(runtime["install_directory"])
    python_home = Path(runtime["capabilities"]["python_base"])
    if not python_home.is_relative_to(folder):
        raise ValueError(
            "Runtime's base Python is external; it is not a self-contained installation"
        )
    manifest = {
        "format": "onecat-runtime-v1",
        "name": runtime["name"],
        "original_prefix": str(folder),
        "python_home": str(python_home.relative_to(folder)),
    }
    (folder / "onecat-runtime.json").write_text(json.dumps(manifest, indent=2))
    target = state_root() / "backups" / f"runtime-{runtime['id']}.onecat.tar.gz"
    job.update("packing_runtime", 20)

    def portable(member):
        job.check_cancelled()
        if member.issym() and os.path.isabs(member.linkname):
            target = Path(member.linkname).resolve()
            if not target.is_relative_to(folder.resolve()):
                raise ValueError("Runtime contains an external symlink: " + member.name)
            member.linkname = os.path.relpath(target, (folder / member.name).parent)
        return member

    with tarfile.open(target, "w:gz", dereference=False, compresslevel=1) as archive:
        for child in folder.iterdir():
            job.check_cancelled()
            archive.add(child, arcname=child.name, filter=portable)
    return {"download_path": str(target), "bytes": target.stat().st_size}
