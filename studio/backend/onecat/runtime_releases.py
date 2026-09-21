# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Discover immutable runtime recipes from the project's public GitHub releases."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import unquote, urlsplit

import requests
from packaging.tags import platform_tags
from packaging.utils import InvalidWheelFilename, parse_wheel_filename
from packaging.version import Version

from . import db

REPOSITORY = "1CatAI/1Cat-vLLM"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases"
CACHE_SECONDS = 900
RETRY_SECONDS = 60
_refresh_lock = threading.Lock()


def asset_url(url: str, name: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and not parsed.query
        and not parsed.fragment
        and "/" not in name
        and "\\" not in name
        and parsed.path.startswith(f"/{REPOSITORY}/releases/download/")
        and unquote(parsed.path.rsplit("/", 1)[-1]) == name
    )


def wheel_info(name: str) -> dict | None:
    try:
        package, version, _, tags = parse_wheel_filename(name)
    except (InvalidWheelFilename, ValueError):
        return None
    candidates = set()
    for tag in tags:
        match = re.fullmatch(r"cp(3)(\d+)", tag.interpreter)
        if match and (tag.abi == tag.interpreter or tag.abi == "abi3"):
            minor = int(match[2])
            if tag.abi == "abi3" and minor <= 12:
                minor = 12
            if 10 <= minor <= 14:
                candidates.add(f"3.{minor}")
    python = "3.12" if "3.12" in candidates else next(iter(sorted(candidates)), None)
    return {
        "package": str(package),
        "package_version": str(version),
        "python": python,
        "platforms": sorted({tag.platform for tag in tags}),
    }


def _sha(asset: dict) -> str | None:
    digest = asset.get("digest") or ""
    return digest[7:].lower() if re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest) else None


def _checksums(release: dict) -> dict:
    sums = {}
    wheels = [a for a in release.get("assets", []) if a.get("name", "").endswith(".whl")]
    if all(_sha(a) for a in wheels):
        return sums
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.lower() not in {
            "sha256sums",
            "sha256sums.txt",
            "checksums.txt",
        } and not name.endswith(".whl.sha256"):
            continue
        if asset.get("size", 0) > 256 * 1024 or not asset_url(
            asset.get("browser_download_url", ""), name
        ):
            continue
        with requests.get(asset["browser_download_url"], timeout=(5, 15), stream=True) as response:
            response.raise_for_status()
            payload = bytearray()
            for chunk in response.iter_content(8192):
                payload.extend(chunk)
                if len(payload) > 256 * 1024:
                    raise ValueError("Release checksum file exceeds 256 KiB")
        for line in payload.decode("utf-8").splitlines():
            match = re.fullmatch(r"([a-fA-F0-9]{64})[ \t]+\*?(.+)", line.strip())
            if match:
                filename, digest = match[2].removeprefix("./"), match[1].lower()
            elif name.endswith(".whl.sha256") and re.fullmatch(r"[a-fA-F0-9]{64}", line.strip()):
                filename, digest = name[:-7], line.strip().lower()
            else:
                continue
            if filename in sums and sums[filename] != digest:
                raise ValueError("Release contains conflicting checksums")
            sums[filename] = digest
    return sums


def parse_release(release: dict, sums: dict | None = None) -> list[dict]:
    if release.get("draft"):
        return []
    sums = sums or {}
    assets = []
    for asset in release.get("assets", []):
        info = wheel_info(asset.get("name", ""))
        if not info:
            continue
        assets.append(
            {
                **info,
                "asset_id": asset["id"],
                "name": asset["name"],
                "url": asset["browser_download_url"],
                "bytes": asset.get("size", 0),
                "sha256": _sha(asset) or sums.get(asset["name"]),
            }
        )
    notes = release.get("body") or ""
    title = release.get("name") or release["tag_name"]
    cuda_targets = set(re.findall(r"\bCUDA\s*[:：]?\s*`?(\d+\.\d+)\b", notes, re.IGNORECASE))
    torch_targets = set(
        re.findall(r"\b(?:PyTorch|Torch)\s*[:：]?\s*`?(\d+\.\d+(?:\.\d+)?)", notes, re.IGNORECASE)
    )
    experimental = bool(
        release.get("prerelease")
        or re.search(r"\b(beta|alpha|experimental|rc\d*)\b", title, re.IGNORECASE)
    )
    result = []
    for main in assets:
        if main["package"] not in {"vllm", "1cat-vllm"}:
            continue
        bundled = [
            a
            for a in assets
            if a["package"] == "flash-attn-v100"
            and a["python"] == main["python"]
            and set(a["platforms"]) & set(main["platforms"])
        ]
        problems = []
        if len(bundled) > 1:
            problems.append("配套 FlashAttention wheel 不唯一 / Multiple companion wheels")
        selected = [main, *bundled]
        if any(
            a["sha256"] and sums.get(a["name"]) and a["sha256"] != sums[a["name"]] for a in selected
        ):
            problems.append("官方 SHA256 校验信息冲突 / Conflicting official SHA256 checksums")
        if any(not a["sha256"] for a in selected):
            problems.append("缺少官方 SHA256 校验信息 / Official SHA256 is missing")
        if any(not asset_url(a["url"], a["name"]) for a in selected):
            problems.append("发行附件地址无效 / Invalid release asset URL")
        if not main["python"]:
            problems.append("Python wheel 标签暂不支持 / Unsupported Python wheel tags")
        cuda = next(iter(cuda_targets)) if len(cuda_targets) == 1 else None
        if not cuda:
            embedded = re.search(r"\.cu(\d{2})(\d)\b", main["package_version"])
            if embedded and not cuda_targets:
                cuda = f"{int(embedded[1])}.{embedded[2]}"
        if not cuda:
            problems.append("发布说明未明确单一 CUDA 构建目标 / No unambiguous CUDA build target")
        if not re.search(r"\bV100\b|\bSM[_ -]?70\b", notes + " " + title, re.IGNORECASE):
            problems.append("发布说明未声明 V100/SM70 支持 / V100/SM70 support is not declared")
        if any(a["package"] == "flash-attn-v100" for a in assets) and not bundled:
            problems.append(
                "缺少匹配此 Python/平台的配套 wheel / Matching companion wheel is missing"
            )
        identity = hashlib.sha256(
            json.dumps(
                [(a["asset_id"], a["sha256"]) for a in selected], separators=(",", ":")
            ).encode()
        ).hexdigest()[:12]
        result.append(
            {
                "id": f"github-{main['asset_id']}-{identity}",
                "version": release["tag_name"].removeprefix("v"),
                "tag": release["tag_name"],
                "title": title,
                "release_id": release["id"],
                "evidence": f"{RELEASES_URL}/tag/{release['tag_name']}",
                "published_at": release.get("published_at"),
                "prerelease": experimental,
                "python": main["python"],
                "platform": main["platforms"][0],
                "platforms": main["platforms"],
                "cuda": cuda,
                "torch": next(iter(torch_targets)) if len(torch_targets) == 1 else None,
                "compute_capabilities": [[7, 0]],
                "assets": selected,
                "url": main["url"],
                "sha256": main["sha256"],
                "bytes": sum(a["bytes"] for a in selected),
                "problems": problems,
                "source": "github",
            }
        )
    return result


def github_page(page: int) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "1Cat-Studio"}
    if token := os.environ.get("ONECAT_GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(
        API_URL,
        params={"per_page": 100, "page": page},
        headers=headers,
        timeout=(5, 15),
    )
    # Reuse the server user's existing GitHub login only for this fixed public API.
    # Never expose credentials to the browser, release assets, or subprocess arguments.
    if response.status_code in {403, 429} and (gh := shutil.which("gh")):
        env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat"}
        env.pop("GH_DEBUG", None)
        try:
            result = subprocess.run(
                [
                    gh,
                    "api",
                    "--hostname",
                    "github.com",
                    "--method",
                    "GET",
                    f"repos/{REPOSITORY}/releases?per_page=100&page={page}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
                env=env,
            )
            if result.returncode == 0:
                entries = json.loads(result.stdout)
                if isinstance(entries, list):
                    return entries
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    response.raise_for_status()
    entries = response.json()
    if not isinstance(entries, list):
        raise TypeError("GitHub did not return a release list")
    return entries


def fetch_releases() -> list[dict]:
    rows = []
    for page in range(1, 11):
        entries = github_page(page)
        for entry in entries:
            if not entry.get("draft"):
                try:
                    sums = _checksums(entry)
                except (requests.RequestException, ValueError) as error:
                    affected = parse_release(entry)
                    for row in affected:
                        row["problems"].append(
                            "此版本校验文件读取失败 / Could not read this release's checksums: "
                            + str(error)[:200]
                        )
                    rows.extend(affected)
                else:
                    rows.extend(parse_release(entry, sums))
        if len(entries) < 100:
            break
    else:
        raise ValueError("Release list exceeds the supported page limit")

    # Release publication time alone is not version order (older tags can be republished).
    def ordering(row):
        try:
            version = Version(row["version"])
        except ValueError:
            version = Version("0")
        return version, row.get("published_at") or "", row["id"]

    return sorted(rows, key=ordering, reverse=True)


def bundled_releases() -> list[dict]:
    from .runtimes import RELEASES

    result = []
    for release in RELEASES:
        row = copy.deepcopy(release)
        name = row["url"].rsplit("/", 1)[-1]
        row.update(
            tag="v" + row["version"],
            title="1Cat-vLLM " + row["version"],
            evidence=f"{RELEASES_URL}/tag/v{row['version']}",
            platforms=[row["platform"]],
            prerelease=False,
            bytes=0,
            problems=[],
            source="bundled",
            assets=[
                {
                    **wheel_info(name),
                    "name": name,
                    "url": row["url"],
                    "sha256": row["sha256"],
                    "bytes": 0,
                }
            ],
        )
        result.append(row)
    return result


def catalog(*, force=False) -> dict:
    now = time.time()
    with _refresh_lock:
        cached = db.get("runtime_release_catalog", "github", {})
        checked = cached.get("checked_at", 0)
        attempted = cached.get("attempted_at", 0)
        minimum_interval = 5 if force else RETRY_SECONDS
        if (force or now - checked >= CACHE_SECONDS) and now - attempted >= minimum_interval:
            try:
                rows = fetch_releases()
                cached = {
                    "items": rows,
                    "source": "github",
                    "checked_at": now,
                    "attempted_at": now,
                    "error": None,
                }
            except (requests.RequestException, ValueError, KeyError, TypeError) as error:
                cached = {
                    **cached,
                    "attempted_at": now,
                    "error": "无法刷新 GitHub 发布列表 / Could not refresh GitHub releases: "
                    + str(error)[:300],
                }
            db.put("runtime_release_catalog", "github", cached)
    return {
        "items": copy.deepcopy(cached.get("items", bundled_releases())),
        "source": cached.get("source", "bundled"),
        "checked_at": cached.get("checked_at"),
        "stale": bool(cached.get("error")) or not cached.get("checked_at"),
        "error": cached.get("error"),
        "repository_url": RELEASES_URL,
    }


def installed_runtime(release: dict) -> dict | None:
    for runtime in db.all_records("runtimes"):
        installed = runtime.get("release") or {}
        if (
            runtime.get("validated")
            and installed.get("url") == release["url"]
            and installed.get("sha256") == release["sha256"]
        ):
            old_assets = installed.get("assets")
            if not old_assets and len(release["assets"]) > 1:
                continue
            if old_assets and [(a["url"], a["sha256"]) for a in old_assets] != [
                (a["url"], a["sha256"]) for a in release["assets"]
            ]:
                continue
            return runtime
    return None


def availability(release: dict, devices: list[dict]) -> list[str]:
    problems = list(release.get("problems", []))
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        problems.append(
            "自动安装目前支持 Linux x86_64 / Automatic installation requires Linux x86_64"
        )
    elif not set(release.get("platforms", [])) & set(platform_tags()):
        problems.append("此 wheel 与本机平台不兼容 / Wheel platform is incompatible")
    if not any(d.get("compute_capability") == [7, 0] for d in devices):
        problems.append("此安装流程适用于 V100/SM70 / This installation targets V100/SM70")
    return problems


def resolve(release_id: str, devices: list[dict]) -> dict:
    rows = catalog()["items"]
    release = next((r for r in rows if r["id"] == release_id), None)
    if not release:
        # Existing clients and queued 0.4.0 jobs still use these pinned identifiers.
        release = next((r for r in bundled_releases() if r["id"] == release_id), None)
    if not release:
        raise ValueError(
            "发行包已变化或不存在，请刷新列表 / Release changed or is unavailable; refresh the list"
        )
    problems = availability(release, devices)
    if problems:
        raise ValueError("; ".join(problems))
    return release
