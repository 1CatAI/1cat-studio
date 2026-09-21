# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Existing credentials must remain usable after the implementation switch."""
import pytest
from fastapi import HTTPException

from onecat import auth, db
from onecat.passwords import hash_password, verify_password

# OpenSSL PBKDF2 known-answer fixture: SHA256, 100000 iterations, 32 bytes.
SALT = "00112233445566778899aabbccddeeff"
DIGEST = "e3123c16b2b092fd0e3e4138f30f8662e6f2c33eb4a8e666a89d433773deec23"


def test_existing_administrator_can_login_without_password_reset():
    db.put("auth", "admin", {"salt": SALT, "hash": DIGEST})
    token = auth.login("legacy-test-password", "fixture-address")
    with db.connect() as conn:
        row = conn.execute("SELECT scope FROM credentials WHERE digest=?", (auth.digest(token),)).fetchone()
    assert row["scope"] == "admin"
    assert db.get("auth", "admin")["hash"] == DIGEST
    with pytest.raises(HTTPException) as rejected:
        auth.login("incorrect-password", "fixture-address")
    assert rejected.value.status_code == 401


def test_digest_contract_and_new_passwords():
    assert hash_password("legacy-test-password", SALT) == (SALT, DIGEST)
    first, second = hash_password("中文🙂"), hash_password("中文🙂")
    assert first != second
    assert len(first[0]) == 32 and len(first[1]) == 64
    assert verify_password("中文🙂", *first)
    assert not verify_password("中文", *first)
    assert not verify_password("中文🙂", first[0], "invalid-hex")
