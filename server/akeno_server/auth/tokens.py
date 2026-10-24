"""Access tokens (short-lived, HMAC-signed) and opaque secrets (refresh tokens, cookies, e-mail links).

Opaque secrets are 256-bit random strings; the database keeps only their SHA-256. Access tokens carry the
session id, so revoking a session (sign-out, password change, an administrator) ends them at once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

from akeno_server.clock import utcnow
from akeno_server.config import get_config

PREFIX = "ak1"


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _key() -> bytes:
    return hashlib.sha256(b"akeno access token v1|" + get_config().token_secret.get_secret_value().encode()).digest()


@dataclass(frozen=True)
class AccessClaims:
    user_id: str
    session_id: str
    role: str
    issued_at: int
    expires_at: int


def issue_access(user_id: str, session_id: str, role: str, now: float | None = None) -> tuple[str, int]:
    now = int(now if now is not None else utcnow().timestamp())
    ttl = get_config().access_token_minutes * 60
    body = b64e(json.dumps({"sub": user_id, "sid": session_id, "role": role, "iat": now, "exp": now + ttl},
                           separators=(",", ":"), sort_keys=True).encode())
    sig = b64e(hmac.new(_key(), f"{PREFIX}.{body}".encode(), hashlib.sha256).digest())
    return f"{PREFIX}.{body}.{sig}", ttl


def read_access(token: str, now: float | None = None) -> AccessClaims | None:
    try:
        prefix, body, sig = token.split(".")
    except ValueError:
        return None
    if prefix != PREFIX:
        return None
    expected = b64e(hmac.new(_key(), f"{PREFIX}.{body}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        data = json.loads(b64d(body))
        claims = AccessClaims(str(data["sub"]), str(data["sid"]), str(data["role"]), int(data["iat"]),
                              int(data["exp"]))
    except (ValueError, KeyError, TypeError):
        return None
    if claims.expires_at <= int(now if now is not None else utcnow().timestamp()):
        return None
    return claims
