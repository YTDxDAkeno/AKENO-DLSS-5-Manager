"""E-mail honesty and link hygiene for the app's account flows.

* Registration, resending and changing the address say whether the e-mail was accepted for delivery.
* A password reset request never says whether an account exists; only a server that cannot send e-mail to anybody
  answers differently, and then for every address alike.
* A new link replaces older ones; a password change ends every reset link; the old address hears about a change.
"""

from __future__ import annotations

import logging
import socket

import pytest

from akeno_server import mail
from akeno_server.config import reset_config
from tests.helpers import PASSWORD, bearer, mail_token, register


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def smtp_down(monkeypatch):
    """SMTP configured, but the mail server refuses the connection."""
    monkeypatch.setenv("AKENO_MAIL_BACKEND", "smtp")
    monkeypatch.setenv("AKENO_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("AKENO_SMTP_PORT", str(_closed_port()))
    monkeypatch.setenv("AKENO_SMTP_SECURITY", "none")
    reset_config()


@pytest.fixture
def production_without_smtp(monkeypatch):
    """A production server whose e-mails still go to the file sink (SMTP not set up yet)."""
    monkeypatch.setenv("AKENO_ENVIRONMENT", "production")
    monkeypatch.setenv("AKENO_MAIL_BACKEND", "file")
    reset_config()


def test_registration_reports_a_sent_confirmation(client):
    out = register(client)
    assert out["verification_email"] == "sent"
    assert mail_token("verify-email")


def test_registration_reports_a_failed_confirmation(client, smtp_down):
    out = register(client)
    assert out["verification_email"] == "failed"
    assert out["access_token"] and out["user"]["email_verified"] is False      # the account exists all the same


def test_registration_on_a_server_without_email(client, production_without_smtp):
    assert register(client)["verification_email"] == "unavailable"


def test_login_and_refresh_carry_no_delivery_field(client):
    tokens = register(client)
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200 and r.json()["verification_email"] is None


def test_reset_request_never_reveals_an_account(client):
    register(client)
    a = client.post("/api/v1/auth/password-reset/request", json={"email": "player@example.test"})
    b = client.post("/api/v1/auth/password-reset/request", json={"email": "nobody@example.test"})
    assert a.status_code == b.status_code == 202 and a.json() == b.json()


def test_reset_request_on_a_server_without_email_fails_for_every_address(client, production_without_smtp):
    register(client)
    mail.outbox.clear()
    a = client.post("/api/v1/auth/password-reset/request", json={"email": "player@example.test"})
    b = client.post("/api/v1/auth/password-reset/request", json={"email": "nobody@example.test"})
    assert a.status_code == b.status_code == 503
    assert a.json() == b.json() and a.json()["error"]["code"] == "email_unavailable"
    assert not mail.outbox, "no reset link was created or written anywhere"


def test_a_new_reset_link_replaces_the_previous_one(client):
    register(client)
    client.post("/api/v1/auth/password-reset/request", json={"email": "player@example.test"})
    first = mail_token("reset-password")
    client.post("/api/v1/auth/password-reset/request", json={"email": "player@example.test"})
    second = mail_token("reset-password")
    assert first != second
    r = client.post("/api/v1/auth/password-reset/confirm", json={"token": first, "new_password": "an older link pw"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_token"
    r = client.post("/api/v1/auth/password-reset/confirm", json={"token": second, "new_password": "the newest link"})
    assert r.status_code == 200


def test_a_password_change_ends_every_reset_link(client):
    tokens = register(client)
    client.post("/api/v1/auth/password-reset/request", json={"email": "player@example.test"})
    pending = mail_token("reset-password")
    r = client.post("/api/v1/auth/change-password", headers=bearer(tokens),
                    json={"current_password": PASSWORD, "new_password": "changed in the app"})
    assert r.status_code == 200
    r = client.post("/api/v1/auth/password-reset/confirm", json={"token": pending, "new_password": "hijacked again"})
    assert r.json()["error"]["code"] == "invalid_token"


def test_reset_ends_other_sessions(client):
    tokens = register(client)
    client.post("/api/v1/auth/password-reset/request", json={"email": "player@example.test"})
    client.post("/api/v1/auth/password-reset/confirm",
                json={"token": mail_token("reset-password"), "new_password": "a brand new pass"})
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_refresh_token"


def test_resend_says_sent_only_when_it_was(client, monkeypatch):
    tokens = register(client)
    first = mail_token("verify-email")
    r = client.post("/api/v1/auth/verify-email/resend", headers=bearer(tokens))
    assert r.status_code == 202 and r.json() == {"status": "sent", "email": "player@example.test"}
    second = mail_token("verify-email")
    assert client.post("/api/v1/auth/verify-email", json={"token": first}).status_code == 400, "replaced link"
    monkeypatch.setenv("AKENO_MAIL_BACKEND", "smtp")
    monkeypatch.setenv("AKENO_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("AKENO_SMTP_PORT", str(_closed_port()))
    monkeypatch.setenv("AKENO_SMTP_SECURITY", "none")
    reset_config()
    r = client.post("/api/v1/auth/verify-email/resend", headers=bearer(tokens))
    assert r.status_code == 503 and r.json()["error"]["code"] == "email_delivery_failed"
    r = client.post("/api/v1/auth/verify-email", json={"token": second})
    assert r.status_code == 200, "an e-mail that could not be sent does not end the last link that was"


def test_resend_is_rate_limited(client):
    tokens = register(client)
    for _ in range(3):
        assert client.post("/api/v1/auth/verify-email/resend", headers=bearer(tokens)).status_code == 202
    r = client.post("/api/v1/auth/verify-email/resend", headers=bearer(tokens))
    assert r.status_code == 429 and r.json()["error"]["details"]["retry_after"] > 0
    assert int(r.headers["Retry-After"]) > 0


def test_a_reset_email_that_could_not_be_sent_keeps_the_last_link(client, monkeypatch):
    register(client)
    client.post("/api/v1/auth/password-reset/request", json={"email": "player@example.test"})
    first = mail_token("reset-password")
    monkeypatch.setenv("AKENO_MAIL_BACKEND", "smtp")
    monkeypatch.setenv("AKENO_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("AKENO_SMTP_PORT", str(_closed_port()))
    monkeypatch.setenv("AKENO_SMTP_SECURITY", "none")
    reset_config()
    r = client.post("/api/v1/auth/password-reset/request", json={"email": "player@example.test"})
    assert r.status_code == 202, "the answer stays the same: nothing about the account is revealed"
    r = client.post("/api/v1/auth/password-reset/confirm", json={"token": first, "new_password": "still works pw"})
    assert r.status_code == 200


def test_resend_on_a_server_without_email(client, production_without_smtp):
    tokens = register(client)
    r = client.post("/api/v1/auth/verify-email/resend", headers=bearer(tokens))
    assert r.status_code == 503 and r.json()["error"]["code"] == "email_unavailable"


def test_resend_after_verification(client):
    tokens = register(client)
    client.post("/api/v1/auth/verify-email", json={"token": mail_token("verify-email")})
    r = client.post("/api/v1/auth/verify-email/resend", headers=bearer(tokens))
    assert r.status_code == 202 and r.json()["status"] == "already_verified"


def test_change_email_from_the_app(client):
    tokens = register(client)
    r = client.post("/api/v1/auth/change-email", headers=bearer(tokens),
                    json={"password": "wrong password!", "new_email": "fixed@example.test"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "wrong_password"
    register(client, "taken@example.test")
    r = client.post("/api/v1/auth/change-email", headers=bearer(tokens),
                    json={"password": PASSWORD, "new_email": "taken@example.test"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_taken"
    r = client.post("/api/v1/auth/change-email", headers=bearer(tokens),
                    json={"password": PASSWORD, "new_email": " fixed@example.test "})
    assert r.status_code == 202 and r.json() == {"status": "sent", "email": "fixed@example.test"}
    assert mail.outbox[-1]["To"] == "fixed@example.test"
    me = client.get("/api/v1/auth/me", headers=bearer(tokens)).json()
    assert me["email"] == "player@example.test", "the address changes only when the link is opened"
    assert client.post("/api/v1/auth/verify-email",
                       json={"token": mail_token("verify-email")}).json()["email"] == "fixed@example.test"
    notice = mail.outbox[-1]
    assert notice["To"] == "player@example.test" and "fixed@example.test" in notice.get_content()
    assert client.get("/api/v1/auth/me", headers=bearer(tokens)).json()["email_verified"] is True
    assert client.post("/api/v1/auth/login", json={"email": "fixed@example.test",
                                                   "password": PASSWORD}).status_code == 200


def test_a_newer_address_change_replaces_the_mistyped_one(client):
    tokens = register(client)
    client.post("/api/v1/auth/change-email", headers=bearer(tokens),
                json={"password": PASSWORD, "new_email": "typo@exmaple.test"})
    typo = mail_token("verify-email")
    client.post("/api/v1/auth/change-email", headers=bearer(tokens),
                json={"password": PASSWORD, "new_email": "right@example.test"})
    assert client.post("/api/v1/auth/verify-email", json={"token": typo}).status_code == 400
    assert client.post("/api/v1/auth/verify-email",
                       json={"token": mail_token("verify-email")}).json()["email"] == "right@example.test"


def test_sign_out_of_other_devices(client):
    here = register(client)
    other = client.post("/api/v1/auth/login", json={"email": "player@example.test", "password": PASSWORD}).json()
    r = client.post("/api/v1/auth/sessions/revoke-others", headers=bearer(here))
    assert r.status_code == 200 and r.json() == {"revoked": 1}
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": other["refresh_token"]}).status_code == 401
    assert client.get("/api/v1/auth/me", headers=bearer(here)).status_code == 200, "this sign-in stays"


def test_version_says_whether_verification_is_required(client):
    assert client.get("/api/v1/system/version").json()["require_email_verification"] is False


def test_production_log_never_contains_links(client, monkeypatch, caplog):
    monkeypatch.setenv("AKENO_ENVIRONMENT", "production")
    monkeypatch.setenv("AKENO_MAIL_BACKEND", "console")
    reset_config()
    with caplog.at_level(logging.INFO, logger="akeno_server.mail"):
        register(client)
    token = mail_token("verify-email")
    assert caplog.records and token not in caplog.text and "verify-email" not in caplog.text


def test_mail_ids_use_the_sender_domain(monkeypatch):
    monkeypatch.setenv("AKENO_MAIL_FROM", "AKENO <no-reply@akeno.online>")
    reset_config()
    msg = mail.compose("verify", "player@example.test", "en", "https://akeno.online/verify-email?token=x")
    assert msg["Message-ID"].endswith("@akeno.online>")
