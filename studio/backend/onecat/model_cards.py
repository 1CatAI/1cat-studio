# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Display-only ModelScope metadata, cached independently of launch validation."""
from __future__ import annotations

import hashlib
from html import unescape
import json
import re
import time

import httpx

from . import catalog, db

TTL = 6 * 3600
RETRY_AFTER = 60
MAX_BYTES = 2 * 1024 * 1024
REPO = re.compile(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+")


def excerpt(markdown: str, minimum_length: int = 40) -> str:
    """Use a short publisher-authored paragraph; skip headings, badges and code."""
    markdown = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", markdown, flags=re.S)
    markdown = re.sub(r"```.*?```|~~~.*?~~~|<!--.*?-->", "", markdown, flags=re.S)
    for paragraph in re.split(r"\n\s*\n", markdown):
        if re.match(r"\s*(?:#|\||>|[-*] |\d+\. )", paragraph):
            continue
        text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", lambda m: "" if m[0].startswith("!") else m[1], paragraph)
        text = re.sub(r"<[^>]*>", "", text)
        text = " ".join(unescape(text).replace("**", "").replace("`", "").split())
        if len(text) >= minimum_length:
            return text[:237].rstrip() + "…" if len(text) > 240 else text
    return ""


def fetch_metadata(endpoint: str, repo: str) -> dict:
    # Public model cards need no account token. Bound both wait time and body size.
    with httpx.Client(timeout=httpx.Timeout(12, connect=5), trust_env=False) as client:
        with client.stream("GET", f"{endpoint}/api/v1/models/{repo}") as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("Model card response exceeds size limit")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("Invalid ModelScope metadata response")
    data = payload.get("Data")
    if payload.get("Code") != 200 or not isinstance(data, dict):
        raise ValueError("ModelScope metadata is unavailable")
    if f"{data.get('Path')}/{data.get('Name')}" != repo:
        raise ValueError("ModelScope returned another repository")
    return data


def read(repo: str, refresh: bool = False) -> dict:
    if not REPO.fullmatch(repo) or not catalog.entry(repo):
        raise ValueError("Model is not in the supported directory")
    endpoint = db.settings()["modelscope_endpoint"].rstrip("/")
    key = hashlib.sha256(f"{endpoint}:{repo}".encode()).hexdigest()
    saved = db.get("model_cards", key, {})
    now = time.time()
    if not refresh and now < saved.get("retry_at", 0):
        return saved["card"]
    if not refresh and now - saved.get("fetched_at", 0) < TTL:
        return saved["card"]
    fallback = {
        "repo_id": repo, "publisher": repo.split("/")[0],
        "source_url": f"{endpoint}/models/{repo}", "endpoint": endpoint,
        "description": "", "readme": "", "base_models": [],
        "tags": [], "license": None, "downloads": None, "likes": None,
        "updated_at": None, "fetched_at": None, "status": "unavailable",
    }
    try:
        data = fetch_metadata(endpoint, repo)
        readme = data.get("ReadMeContent") or ""
        if not isinstance(readme, str):
            raise ValueError("Invalid README")
        base = data.get("BaseModel") or []
        if isinstance(base, str):
            base = [base]
        description = data.get("Description") or ""
        card = {
            **fallback,
            "description": excerpt(description, minimum_length=1) or excerpt(readme),
            "readme": readme,
            "base_models": [item for item in base if isinstance(item, str) and REPO.fullmatch(item)],
            "tags": [tag for tag in (data.get("Tags") or []) if isinstance(tag, str)][:24],
            "license": data.get("License") or None,
            "downloads": data.get("Downloads"), "likes": data.get("Stars"),
            "updated_at": data.get("LastUpdatedTime"),
            "fetched_at": now, "status": "ready",
        }
        db.put("model_cards", key, {"fetched_at": now, "card": card})
        return card
    except (httpx.HTTPError, ValueError, TypeError):
        card = {**saved.get("card", fallback), "status": "stale" if saved.get("fetched_at") else "unavailable"}
        db.put("model_cards", key, {**saved, "card": card, "retry_at": now + RETRY_AFTER})
        return card
