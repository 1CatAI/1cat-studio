# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import copy
import hashlib
import json
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from onecat import app, db, gpu, runtimes
from onecat import runtime_releases as releases
from onecat.jobs import Job


def published(version="1.6.0", *, companion=False, name=None):
    def asset(filename, id):
        return {
            "id": id,
            "name": filename,
            "browser_download_url": f"https://github.com/1CatAI/1Cat-vLLM/releases/download/v{version}/{filename}",
            "digest": "sha256:" + str(id) * 64,
            "size": 4096,
        }

    assets = [asset(f"1cat_vllm-{version}-cp312-cp312-linux_x86_64.whl", 1)]
    if companion:
        assets.append(asset(f"flash_attn_v100-{version}-cp312-cp312-linux_x86_64.whl", 2))
    return {
        "id": 10,
        "tag_name": "v" + version,
        "name": name or version,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-09-08T00:00:00Z",
        "body": "V100 / SM70. CUDA 12.8. Python 3.12. Torch 2.10.",
        "assets": assets,
    }


def recipe(**kwargs):
    return releases.parse_release(published(**kwargs))[0]


@pytest.fixture(autouse=True)
def no_network_or_gpu(monkeypatch):
    monkeypatch.setattr(gpu, "snapshot", lambda: {"gpus": [{"compute_capability": [7, 0]}]})
    monkeypatch.setattr(releases, "fetch_releases", lambda: [recipe()])
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("Unexpected network request"))


def test_wheel_groups_and_unreliable_prerelease_flag():
    row = recipe(version="1.1.0", companion=True, name="1.1.0 Beta / Experimental")
    assert len(row["assets"]) == 2
    assert row["prerelease"] is True
    assert row["python"] == "3.12"
    assert not releases.availability(row, gpu.snapshot()["gpus"])
    entry = published()
    entry["draft"] = True
    assert releases.parse_release(entry) == []


def test_asset_replacement_changes_identity_including_companion():
    entry = published(companion=True)
    before = releases.parse_release(entry)[0]
    entry["assets"][1]["digest"] = "sha256:" + "a" * 64
    after = releases.parse_release(entry)[0]
    assert before["id"] != after["id"]


@pytest.mark.parametrize("change", ["hash", "url", "python", "cuda", "gpu", "platform"])
def test_incompatible_assets_are_visible_but_not_installable(change):
    entry = published()
    main = entry["assets"][0]
    if change == "hash":
        main["digest"] = None
    elif change == "url":
        main["browser_download_url"] = "https://example.com/untrusted.whl"
    elif change == "python":
        main["name"] = main["name"].replace("cp312-cp312", "cp39-cp39")
    elif change == "cuda":
        entry["body"] += " Also CUDA 13.0."
    elif change == "platform":
        main["name"] = main["name"].replace("linux_x86_64", "win_amd64")
    row = releases.parse_release(entry)[0]
    devices = [] if change == "gpu" else gpu.snapshot()["gpus"]
    assert releases.availability(row, devices)


def test_cache_refresh_failure_preserves_last_good_list(monkeypatch):
    now = time.time()
    db.put(
        "runtime_release_catalog",
        "github",
        {
            "items": [recipe()],
            "checked_at": now - 1000,
            "attempted_at": now - 1000,
            "source": "github",
        },
    )
    calls = []

    def fail():
        calls.append(1)
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(releases, "fetch_releases", fail)
    result = releases.catalog(force=True)
    assert result["items"][0]["version"] == "1.6.0"
    assert result["stale"] and result["error"] and result["checked_at"] == now - 1000
    releases.catalog(force=True)
    assert len(calls) == 1


def test_github_rate_limit_reuses_existing_cli_login_only_for_fixed_api(monkeypatch):
    response = requests.Response()
    response.status_code = 403
    monkeypatch.setattr(requests, "get", lambda *a, **k: response)
    monkeypatch.setattr(releases.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setenv("GH_DEBUG", "api")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["timeout"] == 20
        assert kwargs["env"]["GH_PROMPT_DISABLED"] == "1"
        assert "GH_DEBUG" not in kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=json.dumps([published()]))

    monkeypatch.setattr(releases.subprocess, "run", run)
    assert releases.github_page(2)[0]["tag_name"] == "v1.6.0"
    assert calls == [
        [
            "/usr/bin/gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            "GET",
            "repos/1CatAI/1Cat-vLLM/releases?per_page=100&page=2",
        ]
    ]


def test_failed_cli_login_preserves_http_error_without_private_stderr(monkeypatch):
    response = requests.Response()
    response.status_code = 429
    monkeypatch.setattr(requests, "get", lambda *a, **k: response)
    monkeypatch.setattr(releases.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        releases.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="PRIVATE_CREDENTIAL_DIAGNOSTIC",
        ),
    )
    with pytest.raises(requests.HTTPError) as error:
        releases.github_page(1)
    assert "429" in str(error.value)
    assert "PRIVATE" not in str(error.value)


def test_configured_github_token_is_used_only_in_fixed_api_headers(monkeypatch):
    monkeypatch.setenv("ONECAT_GITHUB_TOKEN", "test-token")

    def get(url, **kwargs):
        assert url == releases.API_URL
        assert kwargs["headers"]["Authorization"] == "Bearer test-token"
        response = requests.Response()
        response.status_code = 200
        response._content = b"[]"
        return response

    monkeypatch.setattr(requests, "get", get)
    assert releases.github_page(1) == []


def test_first_run_offline_uses_explicit_bundled_fallback(monkeypatch):
    monkeypatch.setattr(
        releases,
        "fetch_releases",
        lambda: (_ for _ in ()).throw(requests.ConnectionError("offline")),
    )
    result = releases.catalog()
    assert result["source"] == "bundled" and result["stale"]
    assert {r["version"] for r in result["items"]} == {"1.3.0", "1.5.0"}


def test_checksum_sidecar_and_untrusted_checksum_urls(monkeypatch):
    entry = published()
    wheel = entry["assets"][0]
    wheel["digest"] = None
    content = ("a" * 64 + "  " + wheel["name"] + "\n").encode()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, _):
            yield content

    entry["assets"].append(
        {
            "name": "SHA256SUMS",
            "size": len(content),
            "browser_download_url": "https://github.com/1CatAI/1Cat-vLLM/releases/download/v1.6.0/SHA256SUMS",
        }
    )
    monkeypatch.setattr(requests, "get", lambda *a, **k: Response())
    assert releases.parse_release(entry, releases._checksums(entry))[0]["sha256"] == "a" * 64
    entry["assets"][-1]["browser_download_url"] = "http://127.0.0.1/private"
    assert releases._checksums(entry) == {}


def test_install_api_pins_manifest_and_deduplicates(client, monkeypatch):
    created = []

    def create(kind, payload):
        record = {
            "id": db.uid(),
            "kind": kind,
            "state": "queued",
            "stage": "queued",
            "payload": payload,
            "created_at": time.time(),
        }
        db.put("jobs", record["id"], record)
        created.append(record)
        return record

    monkeypatch.setattr(app, "create_job", create)
    row = client.get("/api/runtimes/releases").json()["items"][0]
    assert row["available"]
    payload = {"release_id": row["id"], "release": {"url": "http://127.0.0.1/malicious.whl"}}
    first = client.post("/api/runtimes/install", json=payload)
    second = client.post("/api/runtimes/install", json=payload)
    assert first.status_code == 200 and first.json()["id"] == second.json()["id"]
    assert len(created) == 1
    pinned = created[0]["payload"]["release"]
    assert pinned["url"].startswith("https://github.com/1CatAI/1Cat-vLLM/")
    assert pinned["assets"][0]["sha256"] == "1" * 64
    assert client.post("/api/runtimes/install", json={"release_id": "arbitrary"}).status_code == 400
    assert client.get("/api/runtimes").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/runtimes/releases").status_code == 401


def test_existing_runtime_requires_all_companion_hashes():
    row = recipe(companion=True)
    db.put("runtimes", "old", {"id": "old", "validated": True, "release": row})
    assert releases.installed_runtime(row)["id"] == "old"
    changed = copy.deepcopy(row)
    changed["assets"][1]["sha256"] = "b" * 64
    assert releases.installed_runtime(changed) is None


def write_wheel(path, asset, *, torch="2.9.1", requires_python=">=3.12"):
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = f"Metadata-Version: 2.1\nName: {asset['package']}\nVersion: {asset['package_version']}\nRequires-Python: {requires_python}\n"
    if asset["package"] == "1cat-vllm":
        metadata += f"Requires-Dist: torch=={torch}\nRequires-Dist: torchvision==0.24.1\nRequires-Dist: torch==1.0; sys_platform == 'win32'\n"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("package.dist-info/METADATA", metadata)
    return path


def test_metadata_drives_torch_abi_and_rejects_mismatches(tmp_path):
    row = recipe(companion=True)
    paths = [write_wheel(tmp_path / a["name"], a) for a in row["assets"]]
    assert runtimes.wheel_dependencies(paths, row["assets"], "3.12.14", "12.8") == {
        "torch": "2.9.1+cu128",
        "torchvision": "0.24.1+cu128",
    }
    write_wheel(paths[0], row["assets"][0], torch="2.9.1+cu130")
    with pytest.raises(ValueError, match="CUDA build disagree"):
        runtimes.wheel_dependencies(paths, row["assets"], "3.12.14", "12.8")
    write_wheel(paths[0], row["assets"][0], requires_python=">=3.13")
    with pytest.raises(ValueError, match="Python version"):
        runtimes.wheel_dependencies(paths, row["assets"], "3.12.14", "12.8")


def test_install_uses_isolated_prefix_correct_torch_and_all_wheels(state, tmp_path, monkeypatch):
    row = recipe(companion=True)
    for asset in row["assets"]:
        path = write_wheel(tmp_path / asset["name"], asset)
        asset["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    old_python = tmp_path / "existing-env/bin/python"
    old_python.parent.mkdir(parents=True)
    old_python.write_text("existing environment")
    old = {"id": "existing", "python_path": str(old_python), "validated": True}
    db.put("runtimes", "existing", old)
    db.put("profiles", "p", {"id": "p", "runtime_id": "existing"})
    commands = []
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: "/test/uv")
    monkeypatch.setattr(
        runtimes.shutil, "disk_usage", lambda path: SimpleNamespace(free=100 * 1024**3)
    )

    def command(job, argv, **kwargs):
        commands.append(argv)
        assert "PYTHONPATH" not in kwargs["env"]
        if "venv" in argv:
            target = Path(argv[-1]) / "bin/python"
            target.parent.mkdir(parents=True)
            target.write_text("new environment")

    monkeypatch.setattr(runtimes, "run_command", command)
    monkeypatch.setattr(
        runtimes.subprocess,
        "run",
        lambda argv, **kwargs: SimpleNamespace(
            stdout="/test/python" if "find" in argv else "3.12.14"
        ),
    )
    monkeypatch.setattr(
        runtimes,
        "download_file",
        lambda job, url, path, sha: write_wheel(
            path, next(a for a in row["assets"] if a["url"] == url)
        ),
    )
    monkeypatch.setattr(
        runtimes,
        "inspect_runtime",
        lambda record: db.put("runtimes", record["id"], {**record, "validated": True}),
    )
    db.put(
        "jobs",
        "j",
        {
            "id": "j",
            "kind": "install_runtime",
            "state": "queued",
            "payload": {"release": row},
            "cancel_requested": False,
        },
    )
    result = runtimes.install(Job("j"))
    assert result["runtime_id"] == row["id"]
    assert any(
        "torch==2.9.1+cu128" in cmd and "torchvision==0.24.1+cu128" in cmd for cmd in commands
    )
    wheel_command = next(cmd for cmd in commands if "--constraint" in cmd)
    assert sum(str(a).endswith(".whl") for a in wheel_command) == 2
    assert db.get("profiles", "p")["runtime_id"] == "existing"
    assert (
        db.get("runtimes", "existing") == old and old_python.read_text() == "existing environment"
    )
    before = len(commands)
    assert runtimes.install(Job("j"))["already_installed"] is True
    assert len(commands) == before


def test_official_direct_torch_wheel_dependency(tmp_path):
    row = recipe()
    asset = row["assets"][0]
    path = tmp_path / asset["name"]

    def write(url):
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(
                "package.dist-info/METADATA",
                f"Name: 1cat-vllm\nVersion: 1.6.0\nRequires-Dist: torch @ {url}\n",
            )

    write(
        "https://download.pytorch.org/whl/cu128/torch-2.10.0%2Bcu128-cp312-cp312-manylinux_2_28_x86_64.whl"
    )
    assert (
        runtimes.wheel_dependencies([path], [asset], "3.12.14", "12.8")["torch"] == "2.10.0+cu128"
    )
    write("https://example.com/torch-2.10.0-cp312-cp312-linux_x86_64.whl")
    with pytest.raises(ValueError, match="official matching CUDA"):
        runtimes.wheel_dependencies([path], [asset], "3.12.14", "12.8")


def test_historical_filename_correction_preserves_verified_bytes(tmp_path):
    asset = recipe()["assets"][0]
    path = tmp_path / asset["name"]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("package.dist-info/METADATA", "Name: 1cat-vllm\nVersion: 1.6.1\n")
    original = path.read_bytes()
    asset["sha256"] = hashlib.sha256(original).hexdigest()
    normalized = runtimes.normalize_wheel_path(path, asset)
    assert normalized.name == "1cat_vllm-1.6.1-cp312-cp312-linux_x86_64.whl"
    assert normalized.read_bytes() == original == path.read_bytes()
    assert runtimes.normalize_wheel_path(path, asset) == normalized


def test_unrecorded_companions_are_not_claimed_as_installed():
    row = recipe(companion=True)
    old = copy.deepcopy(row)
    old.pop("assets")
    db.put("runtimes", "old", {"id": "old", "validated": True, "release": old})
    assert releases.installed_runtime(row) is None
