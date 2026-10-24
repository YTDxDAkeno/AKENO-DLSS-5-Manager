"""Shared steps for the server tests."""

from __future__ import annotations

import hashlib
import re

import pyotp

from akeno_server import db as dbmod
from akeno_server import mail
from akeno_server.admin import service as admin
from akeno_server.auth import service as auth
from akeno_server.models import Role

PASSWORD = "correct horse battery"


def fingerprint(name: str = "pc-1") -> str:
    return hashlib.sha256(f"akeno-test-device:{name}".encode()).hexdigest()


def register(client, email: str = "player@example.test", password: str = PASSWORD, **extra) -> dict:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def staff(email: str = "admin@example.test", role: Role = Role.SUPER_ADMIN, with_totp: bool = True) -> tuple:
    """A staff account created directly (like the bootstrap CLI), with two-factor set up."""
    with dbmod.transaction() as db:
        user = auth.create_user(db, email, PASSWORD, "Staff", role=role, verified=True)
        secret = None
        if with_totp:
            from akeno_server.auth import totp
            secret = totp.new_secret()
            totp.enable(user.credential, secret)
        uid = user.id
    return uid, secret


def totp_now(secret: str, offset_steps: int = 0) -> str:
    from akeno_server.clock import utcnow
    return pyotp.TOTP(secret).at(int(utcnow().timestamp()) + 30 * offset_steps)


def staff_login(client, email: str, secret: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD, "totp_code": totp_now(secret)})
    assert r.status_code == 200, r.text
    return r.json()


def make_codes(code_type: str = "monthly", count: int = 1, **kw) -> list[str]:
    with dbmod.transaction() as db:
        actor = auth.user_by_email(db, "codegen@example.test")
        if actor is None:
            actor = auth.create_user(db, "codegen@example.test", PASSWORD, role=Role.ADMIN, verified=True)
        _, items = admin.generate_codes(db, actor, code_type, count, **kw)
        return [plain for plain, _ in items]


def register_device(client, tokens: dict, name: str = "pc-1", label: str = "Gaming PC") -> dict:
    r = client.post("/api/v1/devices/register", headers=bearer(tokens),
                    json={"fingerprint": fingerprint(name), "label": label, "os": "Windows 11", "app_version": "3.0.0"})
    assert r.status_code == 200, r.text
    return r.json()


def mail_token(kind: str = "reset-password") -> str:
    for msg in reversed(mail.outbox):
        m = re.search(rf"/{kind}\?token=([\w\-]+)", msg.get_content())
        if m:
            return m.group(1)
    raise AssertionError(f"no {kind} e-mail was sent")
