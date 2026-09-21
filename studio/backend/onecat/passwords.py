# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Account credentials using the existing PBKDF2-SHA256 storage format.

The database stores a textual salt and a 32-byte hexadecimal digest. Keeping
100,000 iterations and encoding the salt as UTF-8 lets existing users sign in.
"""
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from secrets import token_bytes


def _digest(secret: str, salt_text: str) -> bytes:
    return pbkdf2_hmac(
        hash_name="sha256", password=secret.encode(), salt=salt_text.encode(),
        iterations=100_000, dklen=32,
    )


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt_text = token_bytes(16).hex() if salt is None else salt
    return salt_text, _digest(password, salt_text).hex()


def verify_password(password: str, salt: str, hashed: str) -> bool:
    try:
        expected = bytes.fromhex(hashed)
    except ValueError:
        return False
    return compare_digest(_digest(password, salt), expected)
