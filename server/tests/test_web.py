"""The websites through a browser-like client: forms with CSRF tokens, cookies, redirects, both languages."""

from __future__ import annotations

import re

from akeno_server import db as dbmod
from akeno_server.auth import service as auth
from akeno_server.models import LicenseCode, Role
from tests.helpers import PASSWORD, make_codes, staff, totp_now


def csrf(page) -> str:
    m = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert m, page.text[:500]
    return m.group(1)


def post(web, url, data, form_page=None, **kw):
    token = csrf(web.get(form_page or url))
    return web.post(url, data={"csrf": token, **data}, follow_redirects=kw.pop("follow", True), **kw)


def sign_up(web, email="player@example.test"):
    r = post(web, "/register", {"email": email, "password": PASSWORD, "password2": PASSWORD, "language": "en",
                                "display_name": "Player"})
    assert r.status_code == 200 and "My account" in r.text, r.text[:800]
    return r


def staff_sign_in(web, email, secret):
    r = post(web, "/login", {"email": email, "password": PASSWORD})
    assert "/login/verify" in str(r.url)
    r = post(web, "/login/verify", {"code": totp_now(secret)})
    return r


def test_landing_page_languages_and_headers(web):
    r = web.get("/")
    assert r.status_code == 200 and "AKENO" in r.text and 'lang="en"' in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    de = web.get("/?lang=de")
    assert 'lang="de"' in de.text and "Konto erstellen" in de.text
    assert web.get("/").text.count('lang="de"') >= 1                    # the choice is remembered
    assert web.get("/static/akeno.css").status_code == 200


def test_register_redeem_and_account(web):
    sign_up(web)
    page = web.get("/account")
    assert "Not activated" in page.text
    code = make_codes("monthly")[0]
    r = post(web, "/account/redeem", {"code": code.lower()}, form_page="/account")
    assert "Code redeemed" in r.text and "Monthly (30 days)" in r.text and "data-expires" in r.text
    r = post(web, "/account/redeem", {"code": "AKENO-AAAA-BBBB-CCCC-DDDD"}, form_page="/account")
    assert r.status_code == 400 and "does not exist" in r.text


def test_forms_need_csrf(web):
    sign_up(web)
    r = web.post("/account/redeem", data={"code": "x"}, follow_redirects=False)
    assert r.status_code == 403
    r = web.post("/login", data={"email": "a@example.test", "password": "x", "csrf": "forged"})
    assert r.status_code == 403


def test_pages_need_sign_in(web):
    r = web.get("/account", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert web.get("/admin", follow_redirects=False).headers["location"].startswith("/login")


def test_wrong_password_and_logout(web):
    sign_up(web)
    post(web, "/logout", {}, form_page="/account")
    assert web.get("/account", follow_redirects=False).status_code == 303
    r = post(web, "/login", {"email": "player@example.test", "password": "wrong password!"})
    assert r.status_code == 401 and "wrong" in r.text
    r = post(web, "/login?next=/account/security", {"email": "player@example.test", "password": PASSWORD},
             form_page="/login")
    assert str(r.url).endswith("/account/security")


def test_open_redirect_is_refused(web):
    sign_up(web)
    post(web, "/logout", {}, form_page="/account")
    token = csrf(web.get("/login"))
    r = web.post("/login?next=//evil.example/x", data={"csrf": token, "email": "player@example.test",
                                                        "password": PASSWORD}, follow_redirects=False)
    assert r.headers["location"] == "/account"


def test_staff_two_factor_flow_and_admin_pages(web):
    _, secret = staff()
    r = staff_sign_in(web, "admin@example.test", secret)
    assert "Overview" in r.text and str(r.url).endswith("/admin")
    for page in ("/admin/codes", "/admin/users", "/admin/devices", "/admin/audit", "/admin/settings",
                 "/admin/system"):
        assert web.get(page).status_code == 200, page
    assert "Signing key" in web.get("/admin/system").text


def test_staff_without_2fa_must_enrol_first(web):
    staff("new@example.test", Role.ADMIN, with_totp=False)
    r = post(web, "/login", {"email": "new@example.test", "password": PASSWORD})
    assert str(r.url).endswith("/admin/security") and "<svg" in r.text      # QR code shown
    secret = re.search(r'<p class="mono[^"]*"[^>]*>([A-Z2-7]{32})</p>', r.text).group(1)
    r = web.post("/admin/security", data={"csrf": csrf(r), "code": totp_now(secret)})
    assert "Recovery codes" in r.text and len(re.findall(r"[A-Z0-9]{5}-[A-Z0-9]{5}", r.text)) == 10
    assert web.get("/admin").status_code == 200


def test_generate_codes_shows_them_once(web):
    _, secret = staff()
    staff_sign_in(web, "admin@example.test", secret)
    r = post(web, "/admin/codes/generate", {"code_type": "trial_72h", "count": "3", "note": "=1+1", "batch": "giveaway",
                                            "device_allowance": "", "expires_in_days": "14", "assigned_email": ""},
             form_page="/admin/codes")
    found = re.findall(r"AKENO-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", r.text)
    assert len(set(found)) == 3 and "&#39;=1+1" in r.text            # CSV cell protected against formulas
    listing = web.get("/admin/codes?batch=giveaway")
    assert all(code not in listing.text for code in found) and listing.text.count("giveaway") >= 3
    with dbmod.session() as db:
        assert db.query(LicenseCode).count() == 3


def test_admin_user_actions(web):
    _, secret = staff()
    staff_sign_in(web, "admin@example.test", secret)
    with dbmod.transaction() as db:
        uid = auth.create_user(db, "customer@example.test", PASSWORD).id
    page = f"/admin/users/{uid}"
    r = post(web, f"{page}/action", {"action": "extend", "amount": "3", "unit": "days", "note": "goodwill"},
             form_page=page)
    assert "Monthly (30 days)" in r.text and "goodwill" in r.text
    r = post(web, f"{page}/action", {"action": "grant_lifetime"}, form_page=page)
    assert "Lifetime granted" in r.text
    r = post(web, f"{page}/action", {"action": "disable", "note": "abuse"}, form_page=page)
    assert "Enable account" in r.text and "abuse" in r.text
    r = post(web, f"{page}/action", {"action": "notes", "notes": "called on Monday"}, form_page=page)
    assert "called on Monday" in r.text
    audit = web.get("/admin/audit").text
    assert "license.extend" in audit and "users.update" in audit


def test_support_cannot_open_settings(web):
    _, secret = staff("support@example.test", Role.SUPPORT)
    staff_sign_in(web, "support@example.test", secret)
    r = web.get("/admin/settings")
    assert str(r.url).endswith("/admin") and "does not allow" in r.text
    assert "Generate activation codes" not in web.get("/admin/codes").text


def test_settings_save(web):
    _, secret = staff()
    staff_sign_in(web, "admin@example.test", secret)
    token = csrf(web.get("/admin/settings"))
    r = web.post("/admin/settings", data={"csrf": token, "registration_enabled": "on", "default_device_limit": "3",
                                          "device_removals_per_30_days": "3", "validation_interval_hours": "6",
                                          "offline_grace_hours": "48", "retention_days": "180",
                                          "maintenance_message": "", "min_client_version": "",
                                          "latest_client_version": "3.0.0", "trial_once_per_account": "on"})
    assert "Saved" in r.text and 'value="48"' in r.text
    bad = web.post("/admin/settings", data={"csrf": csrf(r), "offline_grace_hours": "0", "default_device_limit": "2",
                                            "device_removals_per_30_days": "3", "validation_interval_hours": "6",
                                            "retention_days": "180"})
    assert "between" in bad.text


def test_password_reset_and_email_verification_pages(web):
    sign_up(web)
    post(web, "/logout", {}, form_page="/account")
    r = post(web, "/forgot-password", {"email": "player@example.test"})
    assert "on its way" in r.text
    from tests.helpers import mail_token
    token = mail_token("reset-password")
    r = post(web, "/reset-password", {"token": token, "password": "a fresh password", "password2": "a fresh password"},
             form_page=f"/reset-password?token={token}")
    assert "password was changed" in r.text
    verify = mail_token("verify-email")
    assert "confirmed" in web.get(f"/verify-email?token={verify}").text
    assert "invalid" in web.get("/verify-email?token=nope").text


def test_security_page_password_sessions_and_user_2fa(web):
    sign_up(web)
    r = post(web, "/account/password", {"current_password": PASSWORD, "new_password": "brand new secret",
                                        "new_password2": "brand new secret"}, form_page="/account/security")
    assert "Password changed" in r.text
    r = post(web, "/account/sessions/revoke-others", {}, form_page="/account/security")
    assert r.status_code == 200
    setup = web.get("/account/2fa/setup")
    secret = re.search(r'<p class="mono[^"]*"[^>]*>([A-Z2-7]{32})</p>', setup.text).group(1)
    r = web.post("/account/2fa/setup", data={"csrf": csrf(setup), "code": totp_now(secret)})
    assert "Recovery codes" in r.text
    assert "On" in web.get("/account/security").text


def test_device_rename_and_remove_on_website(web):
    from tests.helpers import fingerprint
    sign_up(web)
    with dbmod.transaction() as db:
        from akeno_server.licensing import service as lic
        user = auth.user_by_email(db, "player@example.test")
        dev = lic.register_device(db, user, fingerprint("web-pc"), "Old name", "Windows 11", "3.0.0")
        dev_id = dev.id
    r = post(web, f"/account/devices/{dev_id}/rename", {"label": "Living room"}, form_page="/account")
    assert "Living room" in r.text
    r = post(web, f"/account/devices/{dev_id}/remove", {}, form_page="/account")
    assert "was removed" in r.text and "Living room" not in r.text


def test_german_admin_pages(web):
    _, secret = staff()
    staff_sign_in(web, "admin@example.test", secret)
    r = web.get("/admin/codes?lang=de")
    assert "Aktivierungscodes erzeugen" in r.text
