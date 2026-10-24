"""Encryption of small secrets kept in the database (TOTP seeds), with a key derived from the server pepper."""

from __future__ import annotations

import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from akeno_server.auth.tokens import b64d, b64e
from akeno_server.config import get_config


def _key(purpose: bytes) -> bytes:
    pepper = get_config().code_pepper.get_secret_value().encode()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"akeno secretbox v1", info=purpose).derive(pepper)


def seal(plain: str, purpose: bytes = b"totp") -> str:
    nonce = os.urandom(12)
    return "v1." + b64e(nonce + AESGCM(_key(purpose)).encrypt(nonce, plain.encode(), purpose))


def unseal(sealed: str, purpose: bytes = b"totp") -> str:
    if not sealed.startswith("v1."):
        raise ValueError("unknown secret format")
    raw = b64d(sealed[3:])
    return AESGCM(_key(purpose)).decrypt(raw[:12], raw[12:], purpose).decode()
