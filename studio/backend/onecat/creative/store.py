# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import json
import time

from fastapi import HTTPException

from .. import db
from .schema import Document


def require(bucket: str, identity: str) -> dict:
    value = db.get(bucket, identity)
    if not value:
        raise HTTPException(404, "Record not found")
    return value


def project(identity: str) -> dict:
    return require("canvases", identity)


def validate_assets(document: Document):
    for node in document.nodes:
        if node.kind in {"image", "video", "audio"}:
            if not node.asset_id:
                raise ValueError("Media nodes require an uploaded asset")
            asset = require("creative_assets", node.asset_id)
            if asset["kind"] != node.kind:
                raise ValueError("Asset type does not match node")
        elif node.asset_id:
            raise ValueError("Only media nodes can contain assets")


def create(document: Document) -> dict:
    validate_assets(document)
    value = {**document.model_dump(), "id": db.uid(), "revision": 1, "updated_at": time.time()}
    return db.put("canvases", value["id"], value)


def save(identity: str, document: Document) -> dict:
    validate_assets(document)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM records WHERE bucket='canvases' AND id=?", (identity,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Canvas not found")
        previous = json.loads(row[0])
        if previous["revision"] != document.revision:
            raise HTTPException(
                409, "Canvas changed in another tab. Reload or save your draft as a copy."
            )
        value = {
            **document.model_dump(),
            "id": identity,
            "revision": previous["revision"] + 1,
            "updated_at": time.time(),
        }
        conn.execute(
            "UPDATE records SET data=?,updated=? WHERE bucket='canvases' AND id=?",
            (json.dumps(value, ensure_ascii=False), time.time(), identity),
        )
    return value


def finish_result(run: dict, assets: list[dict]):
    """Results are immutable assets. Canvas placement stays a client edit.

    This prevents a late worker from overwriting unsaved user changes or recreating
    a deleted node. Clients merge unseen run outputs into their current draft and
    save with optimistic concurrency; runs remain recoverable from the tray.
    """
    cancelled_after_start = db.get("creative_runs", run["id"], {}).get("cancel_note")
    db.patch(
        "creative_runs",
        run["id"],
        {
            "state": "completed",
            "stage": "completed",
            "assets": [a["id"] for a in assets],
            "finished_at": time.time(),
            "updated_at": time.time(),
            "error": None,
            "cancel_note": "生成已完成，作品已保留 / Generation finished; artwork was retained"
            if cancelled_after_start else None,
        },
    )
