"""Checks for the account forms, before anything is sent: the same rules the server applies, so a form never
promises what the server then refuses. The server stays the authority; these only spare a round trip.

* E-mail: the server's pattern (``server/akeno_server/auth/service.py``).
* Password: the server's policy (``server/akeno_server/auth/passwords.py``): at least 10 characters, at most 256,
  not the e-mail address, at least 4 different characters. The strength meter only advises on top of that.
* Masked addresses for confirmation screens, and the cooldown of "send again" buttons.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
MIN_PASSWORD = 10
MAX_PASSWORD = 256
MAX_EMAIL = 320
MAX_NAME = 80
RESEND_SECONDS = 60          # between two e-mails the user asks for (the server allows three per hour)

# the reasons a password is refused, in the order they are shown (English source texts, translated by the UI)
TOO_SHORT = "At least 10 characters"
IS_EMAIL = "Not your e-mail address"
TOO_SIMPLE = "Not too simple (at least 4 different characters)"
TOO_LONG = "At most 256 characters"
RULES = (TOO_SHORT, IS_EMAIL, TOO_SIMPLE)

STRENGTH = ("Too weak", "Weak", "Fair", "Good", "Strong")
COMMON = ("password", "passwort", "123456", "qwertz", "qwerty", "akeno", "letmein", "welcome", "abc123", "iloveyou",
          "dragon", "monkey", "football", "admin")


def valid_email(email: str) -> bool:
    email = (email or "").strip()
    return len(email) <= MAX_EMAIL and bool(EMAIL.match(email))


def password_problems(password: str, email: str = "") -> list[str]:
    """The server's rules this password breaks (empty: the server accepts it)."""
    problems = []
    if len(password) < MIN_PASSWORD:
        problems.append(TOO_SHORT)
    if len(password) > MAX_PASSWORD:
        problems.append(TOO_LONG)
    if email.strip() and password.strip().lower() == email.strip().lower():
        problems.append(IS_EMAIL)
    if len(set(password)) < 4:
        problems.append(TOO_SIMPLE)
    return problems


def password_strength(password: str, email: str = "") -> int:
    """0 (refused by the server) to 4 (strong): length and variety, minus common words and repeats."""
    if not password or password_problems(password, email):
        return 0
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    score = 1
    score += len(password) >= 12
    score += len(password) >= 16
    score += classes >= 3
    lower = password.lower()
    local = email.split("@")[0].lower() if "@" in email else ""
    if any(word in lower for word in COMMON) or (len(local) >= 4 and local in lower):
        score -= 2
    if len(set(password)) <= len(password) // 3 or re.search(r"(.)\1\1", password):
        score -= 1
    return max(1, min(4, score))


def mask_email(email: str) -> str:
    """``player@example.test`` → ``p••••r@example.test``: enough to recognise one's own address on a screen that
    others may see, without showing it."""
    email = (email or "").strip()
    local, at, domain = email.partition("@")
    if not at:
        return "•••"
    if len(local) <= 2:
        shown = local[:1] + "•••"
    else:
        shown = local[0] + "•" * min(6, max(3, len(local) - 2)) + local[-1]
    return f"{shown}@{domain}"


@dataclass
class Cooldown:
    """When a "send again" button may be used again: after its own pause, or later when the server says so."""

    seconds: float = RESEND_SECONDS
    until: float = 0.0
    clock: object = time.monotonic

    def start(self, seconds: float | None = None) -> None:
        self.until = max(self.until, self.clock() + (self.seconds if seconds is None else max(0.0, seconds)))

    def start_from(self, sent_at: float | None, wall=time.time) -> None:
        """Continue a pause that began at the wall-clock time ``sent_at`` (for example before a restart)."""
        if sent_at:
            left = self.seconds - (wall() - sent_at)
            if 0 < left <= self.seconds:
                self.start(left)

    def remaining(self) -> int:
        return max(0, int(self.until - self.clock() + 0.999))

    @property
    def ready(self) -> bool:
        return self.remaining() == 0


def clock_text(seconds: float) -> str:
    """``0:42`` / ``12:05`` / ``1:02:00``"""
    s = max(0, int(seconds + 0.999))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
