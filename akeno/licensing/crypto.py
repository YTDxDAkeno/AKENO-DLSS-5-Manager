"""Verification of documents signed by the license server (JWS compact, EdDSA/Ed25519, RFC 8037).

The app only ever verifies; it holds no private key. The same format is produced by
``server/akeno_server/licensing/signing.py``.
"""

from __future__ import annotations

import base64
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class BadSignature(ValueError):
    pass


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_key(b64: str) -> Ed25519PublicKey:
    raw = b64d(b64)
    if len(raw) != 32:
        raise ValueError("an Ed25519 public key has 32 bytes")
    return Ed25519PublicKey.from_public_bytes(raw)


def kid_for(b64: str) -> str:
    import hashlib
    return hashlib.sha256(b64d(b64)).hexdigest()[:16]


def header(token: str) -> dict:
    try:
        return json.loads(b64d(token.split(".")[0]))
    except (ValueError, IndexError) as exc:
        raise BadSignature("malformed token") from exc


def verify(token: str, keys: dict[str, Ed25519PublicKey], typ: str) -> tuple[str, dict]:
    """(kid, payload) of a valid token of type ``typ``; raises BadSignature otherwise."""
    try:
        h, b, s = token.split(".")
        head = json.loads(b64d(h))
        payload = json.loads(b64d(b))
        signature = b64d(s)
    except (ValueError, TypeError, AttributeError) as exc:
        raise BadSignature("malformed token") from exc
    if head.get("alg") != "EdDSA" or head.get("typ") != typ or not isinstance(payload, dict):
        raise BadSignature("wrong token type")
    key = keys.get(head.get("kid", ""))
    if key is None:
        raise BadSignature("unknown signing key")
    try:
        key.verify(signature, f"{h}.{b}".encode("ascii"))
    except InvalidSignature as exc:
        raise BadSignature("signature does not match") from exc
    return head["kid"], payload
