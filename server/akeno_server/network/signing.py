"""Purpose-separated network credentials, delegated by the existing account trust anchor.

The license key signs only the short-lived typed network-key delegation. Peer/tunnel credentials use a different
Ed25519 private key on disk. Release signing keys and release verification purposes are never involved.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from akeno_server.auth.tokens import b64e
from akeno_server.clock import utcnow
from akeno_server.config import get_config
from akeno_server.licensing import signing as account_signing

PURPOSES = ('akeno-lan-peer', 'akeno-vpn-tunnel')
DELEGATION_TYPE = 'akeno-network-key-delegation'
DELEGATION_SECONDS = 3600
SigningUnavailable = account_signing.SigningUnavailable


def _private(path: Path) -> Ed25519PrivateKey:
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except FileExistsError:
            return _private(path)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return key
    except OSError as exc:
        raise SigningUnavailable('The network signing key is unavailable.') from exc
    with os.fdopen(fd, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or (os.name != 'nt' and metadata.st_mode & 0o077):
            raise SigningUnavailable('The network signing key must be an owner-only regular file.')
        raw = stream.read(33)
    if len(raw) != 32:
        raise SigningUnavailable('The network signing key is damaged.')
    return Ed25519PrivateKey.from_private_bytes(raw)


def active():
    key = _private(get_config().network_signing_key_path)
    public = account_signing.raw_public(key.public_key())
    return account_signing.kid_for(public), key


def public_keys() -> dict[str, str]:
    kid, key = active()
    return {kid: b64e(account_signing.raw_public(key.public_key()))}


def sign_token(typ: str, payload: dict) -> str:
    if typ not in PURPOSES:
        raise ValueError('Unsupported network credential purpose.')
    kid, key = active()
    return account_signing.sign(key, kid, typ, payload)


def delegation() -> dict:
    keys = public_keys()
    kid, key = account_signing.store().active()
    # Never delegate the account key itself, even after an operator configuration mistake.
    if kid in keys:
        raise SigningUnavailable('Account and network signing keys must differ.')
    now = int(utcnow().timestamp())
    payload = {'iss': 'akeno-network', 'aud': 'akeno-network-key', 'iat': now,
               'exp': now + DELEGATION_SECONDS, 'purposes': list(PURPOSES), 'keys': keys}
    return {'credential': account_signing.sign(key, kid, DELEGATION_TYPE, payload), 'keys': keys}
