"""The welcome screen and My Account against a scripted server: what the user sees in each state, that nothing is sent
twice or before the form is valid, and that the screens never claim more than the server said."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit

from akeno.licensing.api import ApiError, ServerUnavailable
from test_licensing_client import FakeApi, env  # noqa: F401  (the fixture)

EMAIL = "player@example.test"
PASSWORD = "correct horse battery"


def tokens(verified=False, delivery="sent"):
    return {"access_token": "a", "refresh_token": "r", "expires_in": 900, "verification_email": delivery,
            "user": {"id": "u1", "email": EMAIL, "display_name": "P", "email_verified": verified}}


@pytest.fixture
def gate(env, qtbot):
    from akeno.ui.license_gate import LicenseGate
    make, validate, _ = env
    FakeApi.answers["/licenses/validate"] = validate(result="no_license") | {"license": {"status": "none"}}
    g = LicenseGate(make())
    qtbot.addWidget(g)
    g.show()
    qtbot.waitUntil(lambda: not g.busy, timeout=3000)
    return g


def calls(path):
    return [c for c in FakeApi.calls if c[1] == path]


def idle(qtbot, g):
    qtbot.waitUntil(lambda: not g.busy, timeout=5000)


# -- sign in ---------------------------------------------------------------------------------------------------------
def test_sign_in_checks_the_form_before_sending(gate, qtbot):
    gate._sign_in()
    assert not calls("/auth/login")
    assert gate.signin.email_field.error.text() and gate.signin.password_field.error.text()
    gate.si_email.setText("not-an-address")
    gate.si_password.setText("pw")
    gate._sign_in()
    assert not calls("/auth/login") and "valid e-mail" in gate.signin.email_field.error.text()


def test_sign_in_cannot_be_sent_twice_and_shows_progress(gate, qtbot):
    gate.si_email.setText(EMAIL)
    gate.si_password.setText(PASSWORD)
    gate._sign_in()
    assert gate.busy and not gate.btn_signin.isEnabled() and gate.btn_signin.text() != "Sign in"
    assert not gate.pages.currentWidget().isEnabled() and not gate.lang.isEnabled()
    gate._sign_in()
    gate.signin.submitted.emit()
    idle(qtbot, gate)
    assert len(calls("/auth/login")) == 1
    assert gate.btn_signin.text() == "Sign in" and gate.btn_signin.isEnabled()
    assert gate.si_password.text() == "", "the password does not stay in the field"


@pytest.mark.parametrize("error,text", [
    (ApiError(401, "invalid_credentials", "x"), "E-mail address or password is wrong."),
    (ServerUnavailable("x", kind="offline"), "Unable to connect to AKENO. Please check your internet connection."),
    (ServerUnavailable("x", kind="timeout"), "AKENO is taking too long to respond"),
    (ServerUnavailable("x", maintenance=True), "AKENO is being maintained right now"),
    (ServerUnavailable("x", kind="certificate"), "secure connection to AKENO could not be verified"),
    (ApiError(429, "rate_limited", "x", {"retry_after": 600}), "Please wait 10 minutes"),
    (ApiError(403, "account_disabled", "x"), "This account is disabled"),
    (RuntimeError("Traceback: KeyError 'x'"), "Something went wrong. Please try again."),
])
def test_sign_in_errors_are_friendly(gate, qtbot, error, text):
    FakeApi.answers["/auth/login"] = error
    gate.si_email.setText(EMAIL)
    gate.si_password.setText("wrong password")
    gate._sign_in()
    idle(qtbot, gate)
    assert text in gate.notice.text() and gate.notice.kind == "error"
    assert "Traceback" not in gate.notice.text() and "KeyError" not in gate.notice.text()
    assert gate.current() == "signin"


def test_offline_is_shown_in_the_status_line(gate, qtbot):
    FakeApi.answers["/auth/login"] = ServerUnavailable("x", kind="offline")
    gate.si_email.setText(EMAIL)
    gate.si_password.setText(PASSWORD)
    gate._sign_in()
    idle(qtbot, gate)
    assert "No connection" in gate.net.accessibleName()


def test_password_can_be_shown_and_hidden(gate):
    pw = gate.si_password
    assert pw.echoMode() == QLineEdit.EchoMode.Password
    pw.toggle.trigger()
    assert pw.echoMode() == QLineEdit.EchoMode.Normal and pw.toggle.text() == "Hide password"
    pw.toggle.trigger()
    assert pw.echoMode() == QLineEdit.EchoMode.Password


def test_enter_submits(gate, qtbot):
    gate.si_email.setText(EMAIL)
    gate.si_password.setText(PASSWORD)
    qtbot.keyClick(gate.si_password, Qt.Key.Key_Return)
    idle(qtbot, gate)
    assert len(calls("/auth/login")) == 1


def test_session_expired_message(env, qtbot):
    from akeno.ui.license_gate import LicenseGate
    make, validate, _ = env
    m = make()
    m.sign_in(EMAIL, PASSWORD)
    m.state.update(needs_sign_in=True, session_end="invalid_refresh_token")
    m.state.pop("lease", None)
    m.state.pop("license", None)
    m._save()
    g = LicenseGate(make())
    qtbot.addWidget(g)
    assert g.current() == "signin" and g.si_email.text() == EMAIL and g.heading.text() == "Welcome back"
    assert g.notice.text() == "Your session has expired. Please sign in again."


# -- registration and verification ----------------------------------------------------------------------------------
def fill_register(g, email=EMAIL, pw=PASSWORD, repeat=None):
    g.show_page("register")
    g.rg_email.setText(email)
    g.rg_password.setText(pw)
    g.rg_repeat.setText(pw if repeat is None else repeat)


def test_registration_form_validation(gate, qtbot):
    fill_register(gate, "player@example", "short", "other")
    gate._register()
    assert not calls("/auth/register")
    assert gate.f_email.error.text() and gate.f_password.error.text() and gate.f_repeat.error.text()
    assert gate.strength.word.text() == "Too weak"
    assert gate.rules.rows["At least 10 characters"].text().find("•") >= 0
    fill_register(gate, EMAIL, PASSWORD, PASSWORD + "x")
    gate._register()
    assert not calls("/auth/register") and "do not match" in gate.f_repeat.error.text()
    fill_register(gate, EMAIL, EMAIL)                       # the e-mail address as password: the server refuses it
    gate._register()
    assert not calls("/auth/register")


def test_registration_then_check_your_inbox(gate, qtbot):
    FakeApi.answers["/auth/register"] = tokens(delivery="sent")
    fill_register(gate)
    gate.rg_name.setText("Akeno")
    gate._register()
    idle(qtbot, gate)
    assert calls("/auth/register")[0][2] == {"email": EMAIL, "password": PASSWORD, "display_name": "Akeno",
                                             "language": "en"}
    assert gate.current() == "verify" and gate.heading.text() == "Check your inbox"
    assert "We've sent an email verification link to" in gate.vf_text.text()
    assert EMAIL not in gate.vf_text.text() and "p••••r@example.test" in gate.vf_text.text()
    assert not gate.btn_resend.isEnabled() and gate.btn_resend.cooldown.remaining() > 45
    assert "(0:" in gate.btn_resend.text() or "(1:00)" in gate.btn_resend.text()
    assert gate.rg_password.text() == "" and gate.rg_repeat.text() == ""


@pytest.mark.parametrize("delivery,banner", [
    ("failed", "the verification e-mail could not be sent"),
    ("unavailable", "AKENO cannot send e-mails at the moment"),
])
def test_registration_never_claims_an_email_that_was_not_sent(gate, qtbot, delivery, banner):
    FakeApi.answers["/auth/register"] = tokens(delivery=delivery)
    fill_register(gate)
    gate._register()
    idle(qtbot, gate)
    assert gate.current() == "verify"
    assert "We've sent" not in gate.vf_text.text() and banner in gate.notice.text() and gate.notice.kind == "warn"
    assert gate.btn_resend.isEnabled(), "sending it again is possible at once"


def test_taken_address_is_shown_at_the_field(gate, qtbot):
    FakeApi.answers["/auth/register"] = ApiError(409, "email_taken", "x")
    fill_register(gate)
    gate._register()
    idle(qtbot, gate)
    assert gate.current() == "register" and "already exists" in gate.f_email.error.text()
    assert gate.rg_password.text() == PASSWORD, "nothing to type again"


def test_verification_resend_cooldown_and_rate_limit(gate, qtbot):
    FakeApi.answers["/auth/register"] = tokens(delivery="failed")
    fill_register(gate)
    gate._register()
    idle(qtbot, gate)
    FakeApi.answers["/auth/verify-email/resend"] = {"status": "sent", "email": EMAIL}
    gate.btn_resend.click()
    idle(qtbot, gate)
    assert "We've sent a new verification link" in gate.notice.text()
    assert not gate.btn_resend.isEnabled() and gate.btn_resend.cooldown.remaining() > 50
    gate.btn_resend.cooldown.until = 0
    gate.btn_resend.refresh()
    FakeApi.answers["/auth/verify-email/resend"] = ApiError(429, "rate_limited", "x", {"retry_after": 1200})
    gate.btn_resend.click()
    idle(qtbot, gate)
    assert "Please wait 20 minutes" in gate.notice.text()
    assert gate.btn_resend.cooldown.remaining() > 1100, "the server's wait is respected"
    gate.btn_resend.cooldown.until = 0
    gate.btn_resend.refresh()
    FakeApi.answers["/auth/verify-email/resend"] = ServerUnavailable("x", kind="server", code="email_delivery_failed")
    gate.btn_resend.click()
    idle(qtbot, gate)
    assert "could not be sent" in gate.notice.text() and "Connected" in gate.net.accessibleName()


def test_verified_on_the_website_then_continue(gate, qtbot):
    FakeApi.answers["/auth/register"] = tokens(delivery="sent")
    fill_register(gate)
    gate._register()
    idle(qtbot, gate)
    FakeApi.answers["/auth/me"] = tokens()["user"]
    gate._check_verified()
    idle(qtbot, gate)
    assert gate.current() == "verify" and "isn't verified yet" in gate.notice.text()
    FakeApi.answers["/auth/me"] = tokens(verified=True)["user"]
    gate._poll_verified()                                  # the regular check while the page is open
    qtbot.waitUntil(lambda: gate.notice.text() == "Your e-mail address is verified.", timeout=5000)
    qtbot.waitUntil(lambda: gate.current() == "redeem", timeout=5000)


def test_change_a_mistyped_address(gate, qtbot):
    FakeApi.answers["/auth/register"] = tokens(delivery="sent")
    fill_register(gate, "player@exmaple.test")
    gate._register()
    idle(qtbot, gate)
    gate._toggle_change()
    gate.f_new_email.edit.setText("broken")
    gate._change_email()
    assert not calls("/auth/change-email") and gate.f_new_email.error.text() and gate.f_change_pw.error.text()
    FakeApi.answers["/auth/change-email"] = ApiError(400, "wrong_password", "x")
    gate.f_new_email.edit.setText(EMAIL)
    gate.f_change_pw.edit.setText("wrong")
    gate._change_email()
    idle(qtbot, gate)
    assert "current password is wrong" in gate.f_change_pw.error.text()
    FakeApi.answers["/auth/change-email"] = {"status": "sent", "email": EMAIL}
    gate.f_change_pw.edit.setText(PASSWORD)
    gate._change_email()
    idle(qtbot, gate)
    assert "p••••r@example.test" in gate.vf_text.text() and not gate.vf_change.isVisible()


def test_skip_verification_only_when_the_server_allows_it(env, qtbot):
    from akeno.ui.license_gate import LicenseGate
    make, validate, _ = env
    FakeApi.answers["/licenses/validate"] = validate(result="no_license") | {"license": {"status": "none"}}
    FakeApi.answers["/system/version"] = {"require_email_verification": True}
    g = LicenseGate(make())
    qtbot.addWidget(g)
    g.show()
    idle(qtbot, g)
    qtbot.waitUntil(lambda: g.m.verification_required(), timeout=3000)
    FakeApi.answers["/auth/register"] = tokens()
    fill_register(g)
    g._register()
    idle(qtbot, g)
    assert g.current() == "verify" and g.vf_skip.isHidden()
    FakeApi.answers["/licenses/redeem"] = ApiError(403, "email_not_verified", "x")
    g._skip_verify = True
    g.refresh(g.m.view())
    assert g.current() == "redeem"
    g.code.setText("AKENO-AAAA-BBBB-CCCC-DDDD")
    g._redeem()
    idle(qtbot, g)
    assert g.current() == "verify" and "verify your e-mail address" in g.notice.text()


# -- password reset ---------------------------------------------------------------------------------------------------
def test_reset_says_the_same_for_every_address(gate, qtbot):
    FakeApi.answers["/auth/password-reset/request"] = {"status": "sent_if_account_exists"}
    texts = []
    for email in (EMAIL, "nobody@example.test"):
        gate._to_forgot(email)
        gate._forgot()
        idle(qtbot, gate)
        assert gate.current() == "forgot_sent"
        texts.append(gate.fs_text.text().replace("p••••r", "X").replace("n••••y", "X"))
    assert texts[0] == texts[1] and "If an account exists for" in texts[0]
    assert len(calls("/auth/password-reset/request")) == 2
    assert not gate.btn_again.isEnabled(), "send again waits a minute"
    gate._back_to_sign_in()
    assert gate.current() == "signin" and gate.si_email.text() == "nobody@example.test"


def test_reset_checks_the_address_first(gate, qtbot):
    gate._to_forgot("")
    gate._forgot()
    gate._to_forgot("nope")
    gate._forgot()
    assert not calls("/auth/password-reset/request") and gate.f_reset.error.text()


@pytest.mark.parametrize("error,text", [
    (ServerUnavailable("x", kind="server", code="email_unavailable"), "AKENO cannot send e-mails at the moment"),
    (ServerUnavailable("x", kind="offline"), "Unable to connect to AKENO"),
    (ApiError(429, "rate_limited", "x", {"retry_after": 30}), "Please wait 30 seconds"),
])
def test_reset_failures_are_honest(gate, qtbot, error, text):
    FakeApi.answers["/auth/password-reset/request"] = error
    gate._to_forgot(EMAIL)
    gate._forgot()
    idle(qtbot, gate)
    assert gate.current() == "forgot", "no confirmation when nothing was sent"
    assert text in gate.notice.text() and gate.notice.kind == "error"


# -- German --------------------------------------------------------------------------------------------------------
def test_welcome_screen_in_german(env, qtbot):
    from akeno import i18n
    from akeno.ui.license_gate import LicenseGate
    make, validate, _ = env
    i18n.set_language("de")
    try:
        g = LicenseGate(make(), probe=False)
        qtbot.addWidget(g)
        assert g.heading.text() == "Willkommen bei AKENO" and g.btn_signin.text() == "Anmelden"
        g.show_page("forgot_sent")
        assert g.heading.text() == "Sieh in dein Postfach"
    finally:
        i18n.set_language("en")


# -- My Account -------------------------------------------------------------------------------------------------------
@pytest.fixture
def account(env, qtbot, tmp_path):
    from akeno.core.db import Database
    from akeno.services import AppServices
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    make, validate, _ = env
    FakeApi.answers["/auth/login"] = tokens(verified=False)
    FakeApi.answers["/auth/me"] = dict(tokens()["user"], created_at="2026-10-01T12:00:00Z", two_factor=False)
    FakeApi.answers["/devices"] = [{"id": "dev-1", "label": "Gaming PC", "os": "Windows 11",
                                    "last_seen_at": "2026-10-20T10:00:00Z"},
                                   {"id": "dev-2", "label": "Laptop", "os": "Windows 10",
                                    "last_seen_at": "2026-10-01T10:00:00Z"}]
    m = make()
    m.sign_in(EMAIL, PASSWORD)
    svc = AppServices(db=Database(tmp_path / "db.sqlite3"))
    win = MainWindow(Controller(svc), m)
    qtbot.addWidget(win)
    win.go("account")
    page = win.pages["account"]
    qtbot.waitUntil(lambda: page.devices.count() == 2, timeout=5000)
    yield page
    from akeno.ui import workers
    workers.drain(5000)
    svc.db.close()


def test_account_page_shows_the_server_state(account):
    page = account
    assert page.email.text() == EMAIL and page.name.text() == "P"
    assert page.verified_pill.accessibleName() == "E-mail not verified" and page.btn_verify_mail.isVisibleTo(page)
    assert "2026" in page.facts.grid.itemAtPosition(0, 1).widget().text()
    assert "2 PC(s)" in page.device_usage.text() or "of" in page.device_usage.text()


def test_change_password_from_the_account_page(account, qtbot):
    page = account
    page._toggle(page.pw_box)
    page.f_current.edit.setText(PASSWORD)
    page.f_new.edit.setText("short")
    page.f_confirm.edit.setText("short")
    page._change_password()
    assert not calls("/auth/change-password") and page.f_new.error.text()
    FakeApi.answers["/auth/change-password"] = ApiError(400, "wrong_password", "x")
    page.f_new.edit.setText("a brand new pass")
    page.f_confirm.edit.setText("a brand new pass")
    page._change_password()
    qtbot.waitUntil(lambda: bool(page.f_current.error.text()), timeout=5000)
    FakeApi.answers["/auth/change-password"] = {"status": "password_changed"}
    page._change_password()
    qtbot.waitUntil(lambda: "Password changed" in page.banner.text(), timeout=5000)
    assert not page.pw_box.isVisibleTo(page) and page.f_new.edit.text() == ""


def test_sign_out_other_devices(account, qtbot, monkeypatch):
    from akeno.ui.pages import account as account_page
    page = account
    monkeypatch.setattr(account_page, "confirm", lambda *a, **k: True)
    FakeApi.answers["/auth/sessions/revoke-others"] = {"revoked": 3}
    page._sign_out_others()
    qtbot.waitUntil(lambda: "Signed out of 3" in page.banner.text(), timeout=5000)


def test_expired_session_on_the_account_page(account, qtbot, monkeypatch):
    page = account
    page.m._access = None
    FakeApi.answers["/auth/refresh"] = ApiError(401, "invalid_refresh_token", "x")
    page.on_show()
    qtbot.waitUntil(lambda: page.banner.action.isVisibleTo(page), timeout=5000)
    assert "session has expired" in page.banner.text() and not page.btn_pw_open.isEnabled()
    assert page.m.view().unlocked, "the license keeps running"
    from akeno.ui import account_ui
    shown = []
    monkeypatch.setattr(account_ui.SignInDialog, "exec", lambda self: shown.append(self) or 0)
    page.banner.action.click()
    assert shown and shown[0].form.email.text() == EMAIL
