"""Password hashing and session tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta

import jwt

SESSION_HOURS = 8
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = encoded.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    expected = base64.b64decode(digest)
    actual = hashlib.scrypt(
        password.encode(),
        salt=base64.b64decode(salt),
        n=int(n),
        r=int(r),
        p=int(p),
        dklen=len(expected),
    )
    return hmac.compare_digest(actual, expected)


# Used to keep login timing the same whether or not the user exists.
DUMMY_HASH = hash_password(secrets.token_hex(16))


_secret_cache: str | None = None


def signing_secret() -> str:
    """JWT signing key from SSM Parameter Store (or JWT_SECRET for local runs)."""
    global _secret_cache
    if _secret_cache is None:
        if os.environ.get("JWT_SECRET"):
            _secret_cache = os.environ["JWT_SECRET"]
        else:
            import boto3

            response = boto3.client("ssm").get_parameter(
                Name=os.environ["JWT_SECRET_PARAM"], WithDecryption=True
            )
            _secret_cache = response["Parameter"]["Value"]
    return _secret_cache


def issue_token(username: str, role: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=SESSION_HOURS),
    }
    return jwt.encode(payload, signing_secret(), algorithm="HS256")


def verify_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, signing_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
