# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import httpx
import pytest

from onecat import catalog, db, model_cards

REPO = "QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4"


def metadata():
    return {
        "Path": "QUASAR-QAT", "Name": REPO.split("/")[1],
        "Description": "", "ReadMeContent": "# Model\n\nA quantized checkpoint published by the maintainer, with its own model card.",
        "BaseModel": ["Qwen/Qwen3.8-27B"], "License": "apache-2.0",
        "Downloads": 1200, "Stars": 12, "Tags": ["nvfp4"], "LastUpdatedTime": 1789000000,
    }


def test_card_preserves_publisher_and_base_model_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(model_cards, "fetch_metadata", lambda endpoint, repo: calls.append(repo) or metadata())
    result = model_cards.read(REPO)
    assert result["publisher"] == "QUASAR-QAT"
    assert result["base_models"] == ["Qwen/Qwen3.8-27B"]
    assert result["source_url"] == f"https://modelscope.cn/models/{REPO}"
    assert result["description"].startswith("A quantized checkpoint")
    assert result["downloads"] == 1200 and result["status"] == "ready"
    assert model_cards.read(REPO) == result
    assert calls == [REPO]
    assert not db.all_records("model_validation")
    assert not db.all_records("models")


def test_failed_refresh_preserves_cached_card_and_cold_failure_is_retryable(monkeypatch):
    monkeypatch.setattr(model_cards, "fetch_metadata", lambda *args: metadata())
    saved = model_cards.read(REPO)

    def offline(*args):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(model_cards, "fetch_metadata", offline)
    stale = model_cards.read(REPO, refresh=True)
    assert stale["status"] == "stale" and stale["readme"] == saved["readme"]
    assert model_cards.read(REPO) == stale
    missing = model_cards.read("Qwen/Qwen3.6-27B-FP8")
    assert missing["status"] == "unavailable" and missing["publisher"] == "Qwen"
    monkeypatch.setattr(model_cards, "fetch_metadata", lambda *args: metadata())
    assert model_cards.read(REPO, refresh=True)["status"] == "ready"


def test_model_card_endpoint_is_admin_only_and_rejects_unknown_repositories(client, monkeypatch):
    monkeypatch.setattr(model_cards, "fetch_metadata", lambda *args: metadata())
    assert client.get("/api/models/card", params={"catalog_id": REPO}).json()["publisher"] == "QUASAR-QAT"
    before = catalog.directory()
    for value in ["owner/unlisted-model", "../../etc/passwd", "https://other.test/model"]:
        assert client.get("/api/models/card", params={"catalog_id": value}).status_code == 400
    assert catalog.directory() == before
    client.cookies.clear()
    assert client.get("/api/models/card", params={"catalog_id": REPO}).status_code == 401


def test_response_limits_and_repository_identity(monkeypatch):
    original_client = httpx.Client
    reply = metadata()
    response = httpx.Response(200, json={"Code": 200, "Data": reply})
    monkeypatch.setattr(model_cards.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(lambda request: response), **kwargs))
    assert model_cards.fetch_metadata("https://modelscope.cn", REPO)["BaseModel"] == ["Qwen/Qwen3.8-27B"]
    response = httpx.Response(200, json={"Code": 200, "Data": {**reply, "Path": "other-publisher"}})
    with pytest.raises(ValueError, match="another repository"):
        model_cards.fetch_metadata("https://modelscope.cn", REPO)
    response = httpx.Response(200, content=b"x" * (model_cards.MAX_BYTES + 1))
    with pytest.raises(ValueError, match="size limit"):
        model_cards.fetch_metadata("https://modelscope.cn", REPO)
    response = httpx.Response(200, json=[])
    with pytest.raises(ValueError, match="Invalid ModelScope"):
        model_cards.fetch_metadata("https://modelscope.cn", REPO)


def test_excerpt_skips_frontmatter_badges_and_code():
    assert model_cards.excerpt("简短模型介绍", minimum_length=1) == "简短模型介绍"
    readme = "---\nlicense: apache-2.0\n---\n# Model\n\n[![badge](badge.svg)](https://example.test)\n\n```python\nprint('example')\n```\n\nA **useful model** published by [its author](https://example.test/model), with &amp; without quantization."
    assert model_cards.excerpt(readme) == "A useful model published by its author, with & without quantization."
