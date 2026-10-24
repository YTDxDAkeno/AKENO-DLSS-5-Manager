"""Activation codes: ``AKENO-XXXX-XXXX-XXXX-XXXX``.

16 characters from a 32-letter alphabet without look-alikes (no I, O, 0, 1) give 80 random bits from the
operating system's CSPRNG. The database stores ``HMAC-SHA256(pepper, code)``: a code can be looked up when it
is redeemed, but a copy of the database does not reveal any unused code. The pepper lives in the server's
``.env`` (or a secret file), never in the database or in Git.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from akeno_server.config import get_config

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
GROUPS = 4
GROUP_LEN = 4
PREFIX = "AKENO"
_SHAPE = re.compile(rf"^{PREFIX}([{ALPHABET}]{{{GROUPS * GROUP_LEN}}})$")


class CodeFormatError(ValueError):
    pass


def generate() -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN))
    return format_code(body)


def format_code(body: str) -> str:
    return PREFIX + "-" + "-".join(body[i:i + GROUP_LEN] for i in range(0, len(body), GROUP_LEN))


def normalize(text: str) -> str:
    """The 16 code characters of what a user typed or pasted (spaces, dashes and case do not matter)."""
    compact = re.sub(r"[\s\-_.]", "", (text or "").upper())
    if not compact.startswith(PREFIX):
        compact = PREFIX + compact
    m = _SHAPE.match(compact)
    if not m:
        # codes never contain 0, 1, O or I; saying so helps someone typing a code from a screenshot
        raise CodeFormatError("lookalike" if any(c in compact[len(PREFIX):] for c in "01OI") else "format")
    return m.group(1)


def verifier(body: str) -> str:
    pepper = get_config().code_pepper.get_secret_value().encode()
    return hmac.new(pepper, f"akeno-code-v1:{body}".encode("ascii"), hashlib.sha256).hexdigest()


def hint(body: str) -> str:
    return body[-GROUP_LEN:]
