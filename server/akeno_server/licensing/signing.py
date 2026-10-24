"""Ed25519 license signing.

Signed documents are JWS compact serialisations with ``alg: EdDSA`` (RFC 8037): ``header.payload.signature``,
each base64url. The Windows app ships the public keys and verifies leases and offline certificates without a
network connection. Private keys are PEM files in the key directory (a Docker volume mounted only into the API
container); the database holds only the public halves.

Rotation: a new key is endorsed by the key it replaces. The app accepts a new key from the server only with a
valid endorsement chain back to a key it already trusts, so a build that embeds the first key keeps working
after any number of rotations.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import select
from sqlalchemy.orm import Session

from akeno_server.auth.tokens import b64d, b64e
from akeno_server.clock import utcnow
from akeno_server.config import get_config
from akeno_server.models import SigningKey

ACTIVE_FILE = "active-kid"


class SigningUnavailable(RuntimeError):
    """No usable private key: run ``akeno-server keys init`` (deploy/install.sh does)."""


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def raw_public(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def kid_for(public: bytes) -> str:
    return hashlib.sha256(public).hexdigest()[:16]


def sign(private: Ed25519PrivateKey, kid: str, typ: str, payload: dict) -> str:
    header = b64e(canonical({"alg": "EdDSA", "crv": "Ed25519", "kid": kid, "typ": typ}))
    body = b64e(canonical(payload))
    return f"{header}.{body}.{b64e(private.sign(f'{header}.{body}'.encode('ascii')))}"


class BadSignature(ValueError):
    pass


def verify(token: str, keys: dict[str, Ed25519PublicKey], typ: str) -> tuple[str, dict]:
    """Returns (kid, payload); raises BadSignature. The app has its own copy (akeno/licensing/crypto.py)."""
    try:
        h, b, s = token.split(".")
        header = json.loads(b64d(h))
        payload = json.loads(b64d(b))
        signature = b64d(s)
    except (ValueError, TypeError) as exc:
        raise BadSignature("malformed token") from exc
    if header.get("alg") != "EdDSA" or header.get("typ") != typ:
        raise BadSignature("wrong token type")
    key = keys.get(header.get("kid", ""))
    if key is None:
        raise BadSignature("unknown signing key")
    try:
        key.verify(signature, f"{h}.{b}".encode("ascii"))
    except InvalidSignature as exc:
        raise BadSignature("signature does not match") from exc
    return header["kid"], payload


class KeyStore:
    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory or get_config().keys_dir)
        self._cache: tuple[str, Ed25519PrivateKey] | None = None

    def _path(self, kid: str) -> Path:
        return self.dir / f"{kid}.ed25519.pem"

    def _write_private(self, kid: str, key: Ed25519PrivateKey) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        tmp = self._path(kid).with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem)
        os.replace(tmp, self._path(kid))

    def _set_active(self, kid: str) -> None:
        tmp = self.dir / (ACTIVE_FILE + ".tmp")
        tmp.write_text(kid + "\n", encoding="ascii")
        os.replace(tmp, self.dir / ACTIVE_FILE)
        self._cache = None

    def active_kid(self) -> str | None:
        try:
            return (self.dir / ACTIVE_FILE).read_text(encoding="ascii").strip() or None
        except OSError:
            return None

    def load(self, kid: str) -> Ed25519PrivateKey:
        try:
            key = serialization.load_pem_private_key(self._path(kid).read_bytes(), password=None)
        except (OSError, ValueError) as exc:
            raise SigningUnavailable(f"private key {kid} is missing in {self.dir}") from exc
        if not isinstance(key, Ed25519PrivateKey) or kid_for(raw_public(key.public_key())) != kid:
            raise SigningUnavailable(f"{self._path(kid)} is not the Ed25519 key {kid}")
        return key

    def active(self) -> tuple[str, Ed25519PrivateKey]:
        kid = self.active_kid()
        if not kid:
            raise SigningUnavailable("no license signing key yet (run: akeno-server keys init)")
        if self._cache is None or self._cache[0] != kid:
            self._cache = (kid, self.load(kid))
        return self._cache

    def create(self, db: Session) -> SigningKey:
        """The first key, or a rotation: the new key is endorsed by the current one, which stops signing."""
        previous = None
        if self.active_kid():
            previous = self.active()
        key = Ed25519PrivateKey.generate()
        public = raw_public(key.public_key())
        kid = kid_for(public)
        now = utcnow()
        row = SigningKey(kid=kid, public_key=b64e(public), created_at=now)
        if previous:
            old_kid, old_key = previous
            row.endorsed_by = old_kid
            row.endorsement = sign(old_key, old_kid, "akeno-key-endorsement",
                                   {"kid": kid, "public_key": row.public_key, "iat": int(now.timestamp())})
            old = db.get(SigningKey, old_kid)
            if old is not None:
                old.retired_at = now
        self._write_private(kid, key)
        db.add(row)
        db.flush()
        self._set_active(kid)
        return row


def public_keys(db: Session) -> list[SigningKey]:
    return list(db.scalars(select(SigningKey).order_by(SigningKey.created_at)))


def public_key_objects(db: Session) -> dict[str, Ed25519PublicKey]:
    return {k.kid: Ed25519PublicKey.from_public_bytes(b64d(k.public_key)) for k in public_keys(db)}


def key_document(rows: list[SigningKey]) -> dict:
    return {"keys": [{"kid": k.kid, "alg": "EdDSA", "crv": "Ed25519", "public_key": k.public_key,
                      "created_at": _iso(k.created_at), "retired_at": _iso(k.retired_at),
                      "endorsed_by": k.endorsed_by, "endorsement": k.endorsement} for k in rows]}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


_store: KeyStore | None = None


def store() -> KeyStore:
    global _store
    if _store is None or _store.dir != Path(get_config().keys_dir):
        _store = KeyStore()
    return _store
