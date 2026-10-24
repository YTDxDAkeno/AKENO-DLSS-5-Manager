"""Accounts, codes and licenses through the public API, the way the Windows app uses it."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from akeno_server import clock
from akeno_server import db as dbmod
from akeno_server import settings
from akeno_server.licensing import codes
from tests.helpers import bearer, fingerprint, make_codes, register, register_device

HOUR = 3600
DAY = 86400


def iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def redeem(client, tokens, code):
    return client.post("/api/v1/licenses/redeem", headers=bearer(tokens), json={"code": code})


def test_register_login_me_and_no_license(client):
    tokens = register(client, display_name="Akeno", language="de")
    assert tokens["user"]["language"] == "de" and not tokens["user"]["email_verified"]
    r = client.post("/api/v1/auth/login", json={"email": "PLAYER@example.test", "password": "correct horse battery"})
    assert r.status_code == 200
    me = client.get("/api/v1/auth/me", headers=bearer(r.json()))
    assert me.json()["display_name"] == "Akeno"
    lic = client.get("/api/v1/licenses/current", headers=bearer(tokens)).json()
    assert lic == {**lic, "active": False, "status": "none", "entitlement": None}


def test_wrong_password_and_unknown_email_look_the_same(client):
    register(client)
    a = client.post("/api/v1/auth/login", json={"email": "player@example.test", "password": "wrong password!!"})
    b = client.post("/api/v1/auth/login", json={"email": "nobody@example.test", "password": "wrong password!!"})
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["code"] == b.json()["error"]["code"] == "invalid_credentials"


def test_account_locks_after_repeated_failures(client):
    register(client)
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "player@example.test", "password": "wrong password!!"})
    r = client.post("/api/v1/auth/login", json={"email": "player@example.test", "password": "correct horse battery"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    clock.advance(16 * 60)
    r = client.post("/api/v1/auth/login", json={"email": "player@example.test", "password": "correct horse battery"})
    assert r.status_code == 200


def test_weak_password_and_duplicate_email_refused(client):
    r = client.post("/api/v1/auth/register", json={"email": "a@example.test", "password": "short"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "weak_password"
    register(client, "a@example.test")
    r = client.post("/api/v1/auth/register", json={"email": "A@Example.test", "password": "another good pass"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_taken"


def test_registration_can_be_closed(client):
    with dbmod.transaction() as db:
        settings.put(db, "registration_enabled", False)
    r = client.post("/api/v1/auth/register", json={"email": "a@example.test", "password": "a good password"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "registration_closed"


@pytest.mark.parametrize("kind,seconds", [("trial_24h", 24 * HOUR), ("trial_72h", 72 * HOUR), ("monthly", 30 * DAY)])
def test_time_codes_start_at_redemption(client, kind, seconds):
    code = make_codes(kind)[0]
    clock.advance(5 * DAY)                    # the clock starts when the code is redeemed, not when it was made
    tokens = register(client)
    r = redeem(client, tokens, code)
    assert r.status_code == 200, r.text
    lic = r.json()["license"]
    assert lic["active"] and lic["license_type"] == kind and lic["entitlement"] == "akeno-suite"
    assert iso(lic["expires_at"]) - iso(lic["activated_at"]) == timedelta(seconds=seconds)
    assert abs((iso(lic["activated_at"]) - clock.utcnow()).total_seconds()) < 5


def test_lifetime_code(client):
    tokens = register(client)
    lic = redeem(client, tokens, make_codes("lifetime")[0]).json()["license"]
    assert lic["lifetime"] and lic["expires_at"] is None and lic["offline_eligible"] and lic["remaining_seconds"] is None


def test_codes_stack_deterministically(client):
    tokens = register(client)
    t72, monthly, t24 = make_codes("trial_72h")[0], make_codes("monthly")[0], make_codes("trial_24h")[0]
    first = redeem(client, tokens, t72).json()["license"]
    clock.advance(10 * HOUR)
    tokens = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()
    second = redeem(client, tokens, monthly).json()["license"]
    assert second["license_type"] == "monthly"
    assert iso(second["expires_at"]) == iso(first["expires_at"]) + timedelta(days=30)
    third = redeem(client, tokens, t24).json()["license"]
    assert third["license_type"] == "monthly"            # a shorter code extends but does not downgrade
    assert iso(third["expires_at"]) == iso(second["expires_at"]) + timedelta(hours=24)
    life = redeem(client, tokens, make_codes("lifetime")[0]).json()["license"]
    assert life["license_type"] == "lifetime" and life["expires_at"] is None


def test_expired_license_restarts_from_now(client):
    tokens = register(client)
    redeem(client, tokens, make_codes("trial_24h")[0])
    clock.advance(3 * DAY)
    tokens = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()
    assert client.get("/api/v1/licenses/current", headers=bearer(tokens)).json()["status"] == "expired"
    lic = redeem(client, tokens, make_codes("monthly")[0]).json()["license"]
    assert abs((iso(lic["expires_at"]) - (clock.utcnow() + timedelta(days=30))).total_seconds()) < 5


def test_time_code_on_lifetime_is_not_used(client):
    tokens = register(client)
    redeem(client, tokens, make_codes("lifetime")[0])
    code = make_codes("monthly")[0]
    r = redeem(client, tokens, code)
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_lifetime"
    other = register(client, "other@example.test")
    assert redeem(client, other, code).status_code == 200      # still unused


def test_code_is_single_use_and_retry_is_idempotent(client):
    code = make_codes("monthly")[0]
    a, b = register(client, "a@example.test"), register(client, "b@example.test")
    first = redeem(client, a, code)
    again = redeem(client, a, code.lower().replace("-", " "))
    assert again.status_code == 200 and again.json()["already_redeemed"]
    assert again.json()["license"]["expires_at"] == first.json()["license"]["expires_at"]
    r = redeem(client, b, code)
    assert r.status_code == 409 and r.json()["error"]["code"] == "code_used"


@pytest.mark.parametrize("text,error", [("AKENO-1234", "invalid_code_format"),
                                        ("AKENO-OOOO-IIII-0000-1111", "invalid_code_format"),
                                        ("AKENO-AAAA-BBBB-CCCC-DDDD", "invalid_code")])
def test_bad_codes(client, text, error):
    tokens = register(client)
    r = redeem(client, tokens, text)
    assert r.status_code == 400 and r.json()["error"]["code"] == error


def test_unused_code_expiry_and_assignment(client):
    expiring = make_codes("monthly", expires_in_days=1)[0]
    clock.advance(2 * DAY)
    tokens = register(client)
    assert redeem(client, tokens, expiring).json()["error"]["code"] == "code_expired"
    register(client, "owner@example.test")
    assigned = make_codes("monthly", assigned_email="owner@example.test")[0]
    assert redeem(client, tokens, assigned).json()["error"]["code"] == "code_not_for_account"


def test_trial_once_per_account(client):
    tokens = register(client)
    a, b = make_codes("trial_24h", count=2)
    assert redeem(client, tokens, a).status_code == 200
    assert redeem(client, tokens, b).json()["error"]["code"] == "trial_already_used"
    with dbmod.transaction() as db:
        settings.put(db, "trial_once_per_account", False)
    assert redeem(client, tokens, b).status_code == 200


def test_device_allowance_from_code(client):
    tokens = register(client)
    redeem(client, tokens, make_codes("monthly", device_allowance=4)[0])
    assert client.get("/api/v1/licenses/current", headers=bearer(tokens)).json()["device_limit"] == 4


def test_code_storage_holds_no_plaintext(client):
    code = make_codes("monthly")[0]
    body = codes.normalize(code)
    with dbmod.session() as db:
        from akeno_server.models import LicenseCode
        row = db.query(LicenseCode).one()
        stored = " ".join(str(v) for v in vars(row).values())
    assert body not in stored and code not in stored and row.hint == body[-4:]
    assert row.verifier == codes.verifier(body) and len(row.verifier) == 64


def test_generated_codes_are_random_and_well_formed():
    seen = {codes.generate() for _ in range(2000)}
    assert len(seen) == 2000
    for c in list(seen)[:50]:
        assert len(c) == 25 and c.startswith("AKENO-") and not set(c[6:].replace("-", "")) & set("01OI")


def test_refresh_rotates_and_detects_reuse(client):
    tokens = register(client)
    r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r1.status_code == 200
    new = r1.json()
    assert new["refresh_token"] != tokens["refresh_token"]
    clock.advance(120)                       # past the grace period, an old token is a stolen token
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r2.status_code == 401
    r3 = client.post("/api/v1/auth/refresh", json={"refresh_token": new["refresh_token"]})
    assert r3.status_code == 401             # the whole session ended
    assert client.get("/api/v1/auth/me", headers=bearer(new)).status_code == 401


def test_refresh_retry_within_grace_keeps_session(client):
    tokens = register(client)
    lost = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()
    retry = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert retry.status_code == 200          # the first answer never arrived: one more rotation
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": lost["refresh_token"]}).status_code == 401
    assert client.post("/api/v1/auth/refresh",
                       json={"refresh_token": retry.json()["refresh_token"]}).status_code == 200


def test_access_token_expires_and_logout_revokes(client):
    tokens = register(client)
    clock.advance(16 * 60)
    r = client.get("/api/v1/auth/me", headers=bearer(tokens))
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_access_token"
    fresh = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()
    assert client.post("/api/v1/auth/logout", headers=bearer(fresh), json={}).status_code == 204
    assert client.get("/api/v1/auth/me", headers=bearer(fresh)).json()["error"]["code"] == "session_revoked"


def test_password_reset_flow(client):
    register(client)
    r = client.post("/api/v1/auth/password-reset/request", json={"email": "player@example.test"})
    assert r.status_code == 202
    assert client.post("/api/v1/auth/password-reset/request", json={"email": "ghost@example.test"}).status_code == 202
    from tests.helpers import mail_token
    token = mail_token("reset-password")
    r = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "a brand new pass"})
    assert r.status_code == 200
    again = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "x another pw"})
    assert again.json()["error"]["code"] == "invalid_token"
    assert client.post("/api/v1/auth/login", json={"email": "player@example.test",
                                                   "password": "a brand new pass"}).status_code == 200


def test_email_verification(client):
    register(client)
    from tests.helpers import mail_token
    token = mail_token("verify-email")
    assert client.post("/api/v1/auth/verify-email", json={"token": token}).json()["status"] == "verified"
    login = client.post("/api/v1/auth/login", json={"email": "player@example.test", "password": "correct horse battery"})
    assert login.json()["user"]["email_verified"]


def test_devices_register_limit_rename_remove(client):
    tokens = register(client)
    redeem(client, tokens, make_codes("monthly")[0])
    a = register_device(client, tokens, "pc-a")
    assert register_device(client, tokens, "pc-a")["id"] == a["id"]           # the same PC again
    register_device(client, tokens, "pc-b")
    r = client.post("/api/v1/devices/register", headers=bearer(tokens),
                    json={"fingerprint": fingerprint("pc-c"), "label": "Laptop"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "device_limit_reached"
    assert len(r.json()["error"]["details"]["devices"]) == 2
    assert client.patch(f"/api/v1/devices/{a['id']}", headers=bearer(tokens),
                        json={"label": "Living room"}).json()["label"] == "Living room"
    assert client.delete(f"/api/v1/devices/{a['id']}", headers=bearer(tokens)).status_code == 204
    register_device(client, tokens, "pc-c")
    assert {d["label"] for d in client.get("/api/v1/devices", headers=bearer(tokens)).json()} == {"Gaming PC"}


def test_device_removals_are_limited(client):
    tokens = register(client)
    with dbmod.transaction() as db:
        settings.put(db, "device_removals_per_30_days", 1)
        settings.put(db, "default_device_limit", 5)
    a, b = register_device(client, tokens, "a"), register_device(client, tokens, "b")
    assert client.delete(f"/api/v1/devices/{a['id']}", headers=bearer(tokens)).status_code == 204
    r = client.delete(f"/api/v1/devices/{b['id']}", headers=bearer(tokens))
    assert r.status_code == 409 and r.json()["error"]["code"] == "device_removals_exhausted"


def test_invalid_fingerprint_refused(client):
    tokens = register(client)
    r = client.post("/api/v1/devices/register", headers=bearer(tokens), json={"fingerprint": "z" * 64})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_fingerprint"


def test_validation_errors_are_structured(client):
    r = client.post("/api/v1/auth/login", json={"email": "x"})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error" and body["details"]["fields"][0]["field"] == "password"


def test_maintenance_mode_answers_503(client):
    tokens = register(client)
    with dbmod.transaction() as db:
        settings.put(db, "maintenance_mode", True)
    r = client.get("/api/v1/licenses/current", headers=bearer(tokens))
    assert r.status_code == 503 and r.json()["error"]["code"] == "maintenance"
    assert client.get("/api/v1/system/version").json()["maintenance"] is True


def test_system_endpoints(client, keys):
    assert client.get("/api/v1/system/health").json()["status"] == "ok"
    assert client.get("/api/v1/system/ready").json()["checks"]["signing_key"] == "ok"
    doc = client.get("/api/v1/system/keys").json()
    assert doc["keys"][0]["kid"] == keys and doc["keys"][0]["endorsement"] is None
    assert client.get("/api/v1/openapi.json").json()["info"]["title"] == "AKENO Licensing API"


def test_rate_limit_on_redeem(client):
    tokens = register(client)
    statuses = [redeem(client, tokens, "AKENO-AAAA-BBBB-CCCC-DDDD").status_code for _ in range(12)]
    assert statuses[:10] == [400] * 10 and statuses[10] == 429


def test_naive_times_never_reach_the_database(db):
    from akeno_server.models import License
    with pytest.raises(Exception):
        db.add(License(user_id=__import__("uuid").uuid4(), license_type="monthly",
                       expires_at=datetime(2030, 1, 1)))
        db.flush()
    db.rollback()
    assert datetime.now(timezone.utc).tzinfo is not None
