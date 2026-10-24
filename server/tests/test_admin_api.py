"""Staff accounts: two-factor sign-in, roles, code management, user and device administration, audit."""

from __future__ import annotations

from akeno_server import clock
from akeno_server.models import Role
from tests.helpers import PASSWORD, bearer, make_codes, register, register_device, staff, staff_login, totp_now


def test_staff_need_their_second_factor(client):
    _, secret = staff()
    base = {"email": "admin@example.test", "password": PASSWORD}
    assert client.post("/api/v1/auth/login", json=base).json()["error"]["code"] == "totp_required"
    assert client.post("/api/v1/auth/login", json=base | {"totp_code": "000000"}).json()["error"]["code"] \
        == "invalid_totp"
    code = totp_now(secret)
    assert client.post("/api/v1/auth/login", json=base | {"totp_code": code}).status_code == 200
    replay = client.post("/api/v1/auth/login", json=base | {"totp_code": code})
    assert replay.json()["error"]["code"] == "invalid_totp"          # each code works once


def test_staff_without_two_factor_cannot_use_the_api(client):
    staff("new-admin@example.test", Role.ADMIN, with_totp=False)
    r = client.post("/api/v1/auth/login", json={"email": "new-admin@example.test", "password": PASSWORD})
    assert r.status_code == 403 and r.json()["error"]["code"] == "totp_enrolment_required"


def test_recovery_code_signs_in_once(client, db):
    from akeno_server import db as dbmod
    from akeno_server.auth import service as auth
    from akeno_server.auth import totp
    staff(with_totp=False)
    with dbmod.transaction() as s:
        user = auth.user_by_email(s, "admin@example.test")
        recovery = totp.enable(user.credential, totp.new_secret())
    body = {"email": "admin@example.test", "password": PASSWORD, "totp_code": recovery[0]}
    assert client.post("/api/v1/auth/login", json=body).status_code == 200
    assert client.post("/api/v1/auth/login", json=body).json()["error"]["code"] == "invalid_totp"


def test_users_cannot_reach_admin(client):
    tokens = register(client)
    r = client.get("/api/v1/admin/statistics", headers=bearer(tokens))
    assert r.status_code == 403 and r.json()["error"]["code"] == "insufficient_role"


def test_generate_list_reserve_revoke_codes(client):
    _, secret = staff()
    adm = staff_login(client, "admin@example.test", secret)
    r = client.post("/api/v1/admin/codes/generate", headers=bearer(adm),
                    json={"code_type": "trial_72h", "count": 3, "note": "Giveaway", "batch": "stream-1",
                          "expires_in_days": 30, "device_allowance": 3})
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["codes"]) == 3 and out["batch"] == "stream-1"
    listed = client.get("/api/v1/admin/codes?batch=stream-1", headers=bearer(adm)).json()
    assert listed["total"] == 3
    assert all(c not in str(listed) for c in out["codes"])            # plaintext is never shown again
    found = client.get(f"/api/v1/admin/codes?q={out['codes'][0]}", headers=bearer(adm)).json()
    assert found["total"] == 1 and found["items"][0]["id"] == out["items"][0]["id"]
    first, second = out["items"][0]["id"], out["items"][1]["id"]
    assert client.post(f"/api/v1/admin/codes/{first}/reserve", headers=bearer(adm),
                       json={"reserved_for": "Max"}).json()["status"] == "reserved"
    assert client.post(f"/api/v1/admin/codes/{second}/revoke", headers=bearer(adm)).json()["status"] == "revoked"
    player = register(client)
    assert client.post("/api/v1/licenses/redeem", headers=bearer(player),
                       json={"code": out["codes"][1]}).json()["error"]["code"] == "code_revoked"
    ok = client.post("/api/v1/licenses/redeem", headers=bearer(player), json={"code": out["codes"][0]})
    assert ok.status_code == 200 and ok.json()["license"]["device_limit"] == 3
    redeemed = client.get("/api/v1/admin/codes?status=redeemed", headers=bearer(adm)).json()["items"][0]
    assert redeemed["redeemed_by_email"] == "player@example.test"
    assert client.post(f"/api/v1/admin/codes/{first}/revoke", headers=bearer(adm)).json()["error"]["code"] \
        == "code_used"


def test_support_role_limits(client):
    _, secret = staff("support@example.test", Role.SUPPORT)
    sup = staff_login(client, "support@example.test", secret)
    r = client.post("/api/v1/admin/codes/generate", headers=bearer(sup), json={"code_type": "monthly"})
    assert r.status_code == 403
    player = register(client)
    register_device(client, player)
    uid = player["user"]["id"]
    assert client.post(f"/api/v1/admin/users/{uid}/reset-devices", headers=bearer(sup)).json()["removed"] == 1
    assert client.post(f"/api/v1/admin/users/{uid}/grant-lifetime", headers=bearer(sup), json={}).status_code == 403
    assert client.get("/api/v1/admin/audit", headers=bearer(sup)).status_code == 403


def test_license_administration(client):
    _, secret = staff()
    adm = staff_login(client, "admin@example.test", secret)
    player = register(client)
    uid = player["user"]["id"]
    r = client.post(f"/api/v1/admin/users/{uid}/extend", headers=bearer(adm), json={"hours": 48, "note": "sorry"})
    assert r.json()["license"]["license_type"] == "monthly" and r.json()["license"]["remaining_seconds"] > 47 * 3600
    r = client.post(f"/api/v1/admin/users/{uid}/license", headers=bearer(adm),
                    json={"license_type": "trial_72h", "expires_at": "2099-01-01T00:00:00Z"})
    assert r.json()["license"]["license_type"] == "trial_72h"
    r = client.post(f"/api/v1/admin/users/{uid}/grant-lifetime", headers=bearer(adm), json={"note": "VIP"})
    assert r.json()["license"]["lifetime"]
    r = client.post(f"/api/v1/admin/users/{uid}/license-status", headers=bearer(adm), json={"status": "disabled"})
    assert r.json()["license"]["status"] == "disabled"
    detail = client.get(f"/api/v1/admin/users/{uid}", headers=bearer(adm)).json()
    assert [h["kind"] for h in detail["history"]][:4] == ["status_disabled", "grant_lifetime", "change_type", "extend"]
    history = client.get("/api/v1/licenses/history", headers=bearer(player)).json()
    assert len(history) == 4
    actions = [e["action"] for e in client.get("/api/v1/admin/audit", headers=bearer(adm)).json()["items"]]
    assert {"license.extend", "license.set_type", "license.grant_lifetime", "license.disabled"} <= set(actions)


def test_disable_user_ends_sessions(client):
    _, secret = staff()
    adm = staff_login(client, "admin@example.test", secret)
    player = register(client)
    uid = player["user"]["id"]
    r = client.patch(f"/api/v1/admin/users/{uid}", headers=bearer(adm), json={"status": "disabled",
                                                                             "disabled_reason": "chargeback"})
    assert r.json()["status"] == "disabled"
    assert client.get("/api/v1/auth/me", headers=bearer(player)).status_code == 401
    refresh = client.post("/api/v1/auth/refresh", json={"refresh_token": player["refresh_token"]})
    assert refresh.status_code == 403 and refresh.json()["error"]["code"] == "account_disabled"
    login = client.post("/api/v1/auth/login", json={"email": "player@example.test", "password": PASSWORD})
    assert login.json()["error"]["code"] == "account_disabled"
    client.patch(f"/api/v1/admin/users/{uid}", headers=bearer(adm), json={"status": "active"})
    assert client.post("/api/v1/auth/login", json={"email": "player@example.test",
                                                   "password": PASSWORD}).status_code == 200


def test_roles_and_last_super_admin(client):
    boss_id, secret = staff()
    boss = staff_login(client, "admin@example.test", secret)
    player = register(client)
    uid = player["user"]["id"]
    assert client.patch(f"/api/v1/admin/users/{uid}", headers=bearer(boss), json={"role": "support"}).json()["role"] \
        == "support"
    r = client.patch(f"/api/v1/admin/users/{boss_id}", headers=bearer(boss), json={"role": "admin"})
    assert r.json()["error"]["code"] == "own_role"
    _, asecret = staff("second@example.test", Role.ADMIN)
    adm = staff_login(client, "second@example.test", asecret)
    assert client.patch(f"/api/v1/admin/users/{uid}", headers=bearer(adm), json={"role": "admin"}).status_code == 403
    r = client.patch(f"/api/v1/admin/users/{boss_id}", headers=bearer(adm), json={"status": "disabled"})
    assert r.status_code == 403                                      # admins cannot touch a super admin


def test_device_admin_transfer_and_revoke(client):
    _, secret = staff()
    adm = staff_login(client, "admin@example.test", secret)
    a = register(client, "a@example.test")
    register(client, "b@example.test")
    dev = register_device(client, a)
    listed = client.get("/api/v1/admin/devices?q=a@example", headers=bearer(adm)).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == dev["id"]
    moved = client.post(f"/api/v1/admin/devices/{dev['id']}/transfer", headers=bearer(adm),
                        json={"email": "b@example.test"}).json()
    assert moved["active"]
    assert client.get("/api/v1/devices", headers=bearer(a)).json() == []
    r = client.post(f"/api/v1/admin/devices/{moved['id']}/revoke", headers=bearer(adm))
    assert r.json()["active"] is False


def test_admin_revoked_device_cannot_reregister(client):
    _, secret = staff()
    adm = staff_login(client, "admin@example.test", secret)
    player = register(client)
    dev = register_device(client, player)
    client.post(f"/api/v1/admin/devices/{dev['id']}/revoke", headers=bearer(adm))
    from tests.helpers import fingerprint
    r = client.post("/api/v1/devices/register", headers=bearer(player), json={"fingerprint": fingerprint()})
    assert r.json()["error"]["code"] == "device_blocked"
    uid = player["user"]["id"]
    client.post(f"/api/v1/admin/users/{uid}/reset-devices", headers=bearer(adm))
    assert client.post("/api/v1/devices/register", headers=bearer(player),
                       json={"fingerprint": fingerprint()}).json()["error"]["code"] == "device_blocked"


def test_statistics_export_settings(client):
    _, secret = staff()
    adm = staff_login(client, "admin@example.test", secret)
    p = register(client, "=cmd@example.test", display_name="=HYPERLINK(1)")
    client.post("/api/v1/licenses/redeem", headers=bearer(p), json={"code": make_codes("monthly")[0]})
    stats = client.get("/api/v1/admin/statistics", headers=bearer(adm)).json()
    assert stats["users"] >= 2 and stats["licenses_by_type"]["monthly"] == 1 and stats["codes"]["redeemed"] == 1
    assert stats["daily"][-1]["redemptions"] == 1
    csv_text = client.get("/api/v1/admin/users/export", headers=bearer(adm)).text
    assert "'=HYPERLINK(1)" in csv_text and "'=cmd@example.test" in csv_text
    r = client.put("/api/v1/admin/settings", headers=bearer(adm), json={"offline_grace_hours": 0})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_setting"
    r = client.put("/api/v1/admin/settings", headers=bearer(adm), json={"offline_grace_hours": 96,
                                                                       "min_client_version": "3.0.0"})
    assert r.json()["offline_grace_hours"] == 96
    assert client.get("/api/v1/system/version").json()["min_client_version"] == "3.0.0"


def test_staff_session_needs_mfa_flag(client):
    """A staff API session always comes from a two-factor sign-in; a token without it is refused."""
    from akeno_server import db as dbmod
    from akeno_server.auth import service as auth
    uid, _ = staff()
    with dbmod.transaction() as db:
        user = auth.user_by_email(db, "admin@example.test")
        issued = auth.start_session(db, user, "app", mfa=False)
    r = client.get("/api/v1/admin/statistics", headers={"Authorization": f"Bearer {issued.access_token}"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "mfa_required"
