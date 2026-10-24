"""Time-based one-time passwords (RFC 6238) for privileged accounts, plus single-use recovery codes."""

from __future__ import annotations

import hashlib
import hmac
import secrets

import pyotp

from akeno_server.auth.secretbox import seal, unseal
from akeno_server.clock import utcnow
from akeno_server.models import UserCredential

ISSUER = "AKENO"
RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_secret() -> str:
    return pyotp.random_base32(32)


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def _matching_step(secret: str, code: str) -> int | None:
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    now = int(utcnow().timestamp())
    for drift in (0, -1, 1):
        step = now // 30 + drift
        if hmac.compare_digest(totp.at(step * 30), code):
            return step
    return None


def check_new(secret: str, code: str) -> bool:
    """Enrolment: the code shown by the authenticator app for a secret not saved yet."""
    return _matching_step(secret, code) is not None


def enable(cred: UserCredential, secret: str) -> list[str]:
    cred.totp_secret_enc = seal(secret)
    cred.totp_enabled_at = utcnow()
    cred.totp_last_step = None
    codes = new_recovery_codes()
    cred.recovery_codes = [_hash(c) for c in codes]
    return codes


def disable(cred: UserCredential) -> None:
    cred.totp_secret_enc = None
    cred.totp_enabled_at = None
    cred.totp_last_step = None
    cred.recovery_codes = []


def verify(cred: UserCredential, code: str) -> bool:
    """A TOTP code (each accepted once) or one of the recovery codes (used up)."""
    if not cred.totp_enabled_at or not cred.totp_secret_enc:
        return False
    code = code.strip()
    if "-" in code or any(ch.isalpha() for ch in code):
        return _use_recovery(cred, code)
    step = _matching_step(unseal(cred.totp_secret_enc), code)
    if step is None or (cred.totp_last_step is not None and step <= cred.totp_last_step):
        return False
    cred.totp_last_step = step
    return True


def new_recovery_codes(n: int = 10) -> list[str]:
    def one() -> str:
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10))
        return f"{raw[:5]}-{raw[5:]}"
    return [one() for _ in range(n)]


def _hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().upper().encode()).hexdigest()


def _use_recovery(cred: UserCredential, code: str) -> bool:
    h = _hash(code)
    remaining = list(cred.recovery_codes or [])
    if h not in remaining:
        return False
    remaining.remove(h)
    cred.recovery_codes = remaining
    return True
