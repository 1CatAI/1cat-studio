# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from . import db
from .passwords import hash_password, verify_password

COOKIE = "onecat_session"
_attempts: dict[str, deque] = defaultdict(deque)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def initialize(password: str):
    salt, hashed = hash_password(password)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM records WHERE bucket='auth' AND id='admin'").fetchone():
            raise HTTPException(409, "Administrator already configured")
        import json

        conn.execute(
            "INSERT INTO records VALUES('auth','admin',?,?)",
            (json.dumps({"salt": salt, "hash": hashed}), time.time()),
        )


def login(password: str, address: str) -> str:
    now = time.time()
    attempts = _attempts[address]
    while attempts and attempts[0] < now - 60:
        attempts.popleft()
    if len(attempts) >= 10:
        raise HTTPException(429, "Too many login attempts; retry in one minute")
    attempts.append(now)
    admin = db.get("auth", "admin")
    if not admin or not verify_password(password, admin["salt"], admin["hash"]):
        raise HTTPException(401, "Incorrect password")
    attempts.clear()
    return issue("Browser session", "admin", expires=now + 30 * 86400)


def issue(name: str, scope: str = "inference", expires: float | None = None) -> str:
    token = "oc_" + secrets.token_urlsafe(32)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO credentials VALUES(?,?,?,?,?,?)",
            (digest(token), name, scope, expires, time.time(), token[:10]),
        )
    return token


def resolve(request: Request, admin: bool = False) -> dict:
    header = request.headers.get("authorization", "")
    bearer = header[7:] if header.lower().startswith("bearer ") else ""
    token = bearer or request.cookies.get(COOKIE, "")
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM credentials WHERE digest=?", (digest(token),)).fetchone()
    if not row or (row["expires"] is not None and row["expires"] < time.time()):
        raise HTTPException(401, "Sign in required")
    if admin and row["scope"] != "admin":
        raise HTTPException(403, "A browser administrator session is required")
    # Cookies authenticate management from the same origin only. Inference keys use Bearer.
    if not bearer and request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin management request rejected")
    return dict(row)


def require_admin(request: Request) -> dict:
    return resolve(request, admin=True)


def require_inference(request: Request) -> dict:
    return resolve(request)
