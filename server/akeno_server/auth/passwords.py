"""Argon2id password hashing (argon2-cffi), with parameters from the server configuration."""

from __future__ import annotations

from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from akeno_server.config import get_config

MIN_LENGTH = 10
MAX_LENGTH = 256


class PasswordPolicyError(ValueError):
    pass


@lru_cache
def _hasher() -> PasswordHasher:
    cfg = get_config()
    return PasswordHasher(time_cost=cfg.argon2_time_cost, memory_cost=cfg.argon2_memory_kib,
                          parallelism=cfg.argon2_parallelism)


def check_policy(password: str, email: str = "") -> None:
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError(f"The password needs at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError("The password is too long.")
    if email and password.strip().lower() == email.strip().lower():
        raise PasswordPolicyError("The password must not be your e-mail address.")
    if len(set(password)) < 4:
        raise PasswordPolicyError("The password is too simple.")


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return _hasher().verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    try:
        return _hasher().check_needs_rehash(stored)
    except InvalidHashError:
        return True


_DUMMY: list[str] = []


def burn_time(password: str) -> None:
    """Verify against a dummy hash so unknown e-mail addresses take as long as wrong passwords."""
    if not _DUMMY:
        _DUMMY.append(hash_password("akeno-dummy-password"))
    verify_password(_DUMMY[0], password)


def reset_cache() -> None:
    _hasher.cache_clear()
    _DUMMY.clear()
