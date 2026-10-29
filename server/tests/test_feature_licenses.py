"""Module licenses (AKENO VPN, AKENO LAN Play) next to AKENO Full: codes, stacking, signed access documents,
older apps, devices, revocation and staff permissions."""

from __future__ import annotations

import base64
import json
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from akeno_server import clock
from akeno_server import db as dbmod
from akeno_server import settings
from akeno_server.admin import service as admin
from akeno_server.auth import service as auth
from akeno_server.auth.tokens import b64d
from akeno_server.errors import ApiError
from akeno_server.licensing import entitlements, signing
from akeno_server.licensing import service as lic
from akeno_server.models import FeatureGrant, LicenseCode, LicenseRedemption, Role
from tests.helpers import PASSWORD, bearer, make_codes, register, register_device

DAY = 86400
VPN, LAN, SUITE = entitlements.VPN, entitlements.LAN_PLAY, entitlements.SUITE


@pytest.fixture(autouse=True)
def vpn_licenses_on():
    with dbmod.transaction() as db:
        settings.put(db, "vpn_module_licenses", True)


def trusted(client) -> dict:
    return {k["kid"]: Ed25519PublicKey.from_public_bytes(b64d(k["public_key"]))
            for k in client.get("/api/v1/system/keys").json()["keys"]}


def module_code(ent: str, length: str = "lifetime", **kw) -> str:
    code_type, days = ("lifetime", None) if length == "lifetime" else ("monthly", int(length))
    return make_codes(code_type, entitlement=ent, duration_days=days if days != 30 else None, **kw)[0]


def redeem(client, tokens, code, access=True):
    body = {"code": code, **({"access": 1} if access else {})}
    return client.post("/api/v1/licenses/redeem", headers=bearer(tokens), json=body)


def validate(client, tokens, device, access=True, serial=None):
    nonce = secrets.token_urlsafe(24)
    body = {"device_id": device["id"], "app_version": "3.2.0", "certificate_serial": serial, "nonce": nonce}
    if access:
        body["access"] = 1
    r = client.post("/api/v1/licenses/validate", headers=bearer(tokens), json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    typ = "akeno-access-validation" if access else "akeno-validation"
    _, st = signing.verify(out["validation"], trusted(client), typ)
    assert st["nonce"] == nonce and st["res"] == out["result"]
    out["statement"] = st
    if out["lease"]:
        out["lease_payload"] = signing.verify(out["lease"], trusted(client),
                                              "akeno-access-lease" if access else "akeno-lease")[1]
    return out


def later(client, tokens, seconds):
    """Let time pass; the app then refreshes its short-lived access token, as it does after a pause."""
    clock.advance(seconds)
    r = client.post("/api/v1/auth/login", json={"email": tokens["user"]["email"], "password": PASSWORD})
    assert r.status_code == 200, r.text
    tokens.update(r.json())


def granted(out) -> dict:
    return {g["ent"]: g for g in out.get("lease_payload", {}).get("grants", [])}


def account(client, email="player@example.test"):
    tokens = register(client, email)
    return tokens, register_device(client, tokens, email)


def uid(tokens):
    import uuid
    return uuid.UUID(tokens["user"]["id"])


def has(tokens, ent) -> bool:
    with dbmod.session() as db:
        return entitlements.has(db, uid(tokens), ent)


# -- 1-6: what each license unlocks ----------------------------------------------------------------------------------
def test_account_without_license_signs_in_and_redeems_a_vpn_lifetime_code(client):
    tokens, device = account(client)
    assert validate(client, tokens, device)["result"] == "no_license"
    r = redeem(client, tokens, module_code(VPN))
    assert r.status_code == 200, r.text
    assert r.json()["entitlement"] == VPN and r.json()["access"]["running"] == [VPN]
    assert r.json()["license"]["status"] == "none"            # the suite license was not created
    out = validate(client, tokens, device)
    assert out["result"] == "ok" and set(granted(out)) == {VPN}
    assert granted(out)[VPN]["lt"] == "lifetime" and granted(out)[VPN]["exp"] is None
    assert out["lease_payload"]["ent"] == "akeno-access" and out["access"]["modules"] == ["vpn"]


def test_vpn_lifetime_unlocks_nothing_else(client):
    tokens, device = account(client)
    redeem(client, tokens, module_code(VPN))
    assert has(tokens, VPN) and not has(tokens, LAN) and not has(tokens, SUITE)
    out = validate(client, tokens, device)
    assert SUITE not in granted(out) and LAN not in granted(out)


def test_lan_lifetime_unlocks_only_lan_play(client):
    tokens, device = account(client)
    redeem(client, tokens, module_code(LAN))
    assert has(tokens, LAN) and not has(tokens, VPN) and not has(tokens, SUITE)
    assert set(granted(validate(client, tokens, device))) == {LAN}


def test_full_lifetime_unlocks_everything(client):
    tokens, device = account(client)
    assert redeem(client, tokens, make_codes("lifetime")[0]).status_code == 200
    assert has(tokens, SUITE) and has(tokens, VPN) and has(tokens, LAN)
    out = validate(client, tokens, device)
    assert set(granted(out)) == {SUITE} and out["access"]["modules"] == ["*"]


def test_vpn_and_lan_combine(client):
    tokens, device = account(client)
    redeem(client, tokens, module_code(VPN))
    redeem(client, tokens, module_code(LAN, "90"))
    out = validate(client, tokens, device)
    assert set(granted(out)) == {VPN, LAN} and not has(tokens, SUITE)
    assert granted(out)[LAN]["exp"] - out["statement"]["iat"] == pytest.approx(90 * DAY, abs=5)


# -- 7: AKENO Full for 30 days on top of VPN lifetime ----------------------------------------------------------------
def test_full_30_days_then_back_to_vpn_only(client):
    tokens, device = account(client)
    redeem(client, tokens, module_code(VPN))
    assert redeem(client, tokens, make_codes("monthly")[0]).status_code == 200
    out = validate(client, tokens, device)
    assert set(granted(out)) == {SUITE, VPN}
    assert granted(out)[SUITE]["exp"] is not None and granted(out)[VPN]["exp"] is None
    later(client, tokens, 31 * DAY)
    out = validate(client, tokens, device)
    assert out["result"] == "ok" and set(granted(out)) == {VPN}
    assert has(tokens, VPN) and not has(tokens, SUITE) and not has(tokens, LAN)
    entries = {e["entitlement"]: e for e in out["access"]["entitlements"]}
    assert entries[SUITE]["status"] == "expired" and entries[VPN]["active"]


def test_a_code_never_shortens_or_replaces_a_stronger_entitlement(client):
    tokens, device = account(client)
    redeem(client, tokens, module_code(VPN))
    r = redeem(client, tokens, module_code(VPN, "30"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_lifetime"
    redeem(client, tokens, module_code(LAN, "30"))
    redeem(client, tokens, module_code(LAN, "90"))           # time adds up, from the later expiry
    with dbmod.session() as db:
        grant = entitlements.grant_of(db, uid(tokens), LAN)
        assert (grant.expires_at - clock.utcnow()).total_seconds() == pytest.approx(120 * DAY, abs=5)
    assert redeem(client, tokens, make_codes("lifetime")[0]).status_code == 200
    r = redeem(client, tokens, module_code(LAN, "lifetime"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_included"
    with dbmod.session() as db:                              # the refused codes stay unused
        assert db.query(LicenseCode).filter(LicenseCode.status == "available").count() == 2
        assert entitlements.grant_of(db, uid(tokens), VPN).lifetime


# -- 8-12: codes and grants ------------------------------------------------------------------------------------------
def test_a_module_code_cannot_be_used_twice(client):
    code = module_code(VPN)
    a, _ = account(client, "a@example.test")
    b, _ = account(client, "b@example.test")
    assert redeem(client, a, code).status_code == 200
    again = redeem(client, a, code)
    assert again.status_code == 200 and again.json()["already_redeemed"] is True
    r = redeem(client, b, code)
    assert r.status_code == 409 and r.json()["error"]["code"] == "code_used"
    assert not has(b, VPN)


def _redeem_as(user_id, code, barrier):
    with dbmod.session() as db:
        from akeno_server.models import User
        user = db.get(User, user_id)
        db.rollback()                 # hold no lock while waiting (SQLite takes the write lock at BEGIN)
        barrier.wait(timeout=60)
        try:
            done = lic.redeem(db, user, code, "", "app", access=True)
            return "already" if done.already else "ok"
        except ApiError as exc:
            return exc.code


def test_two_accounts_redeeming_one_module_code_at_once(client):
    code = module_code(VPN)
    users = []
    for i in range(8):
        with dbmod.transaction() as db:
            users.append(auth.create_user(db, f"race{i}@example.test", PASSWORD, verified=True).id)
    barrier = threading.Barrier(len(users))
    with ThreadPoolExecutor(len(users)) as pool:
        results = list(pool.map(lambda u: _redeem_as(u, code, barrier), users))
    assert results.count("ok") == 1 and set(results) == {"ok", "code_used"}, results
    with dbmod.session() as db:
        assert db.query(LicenseRedemption).count() == 1 and db.query(FeatureGrant).count() == 1


def test_withdrawn_and_expired_codes_and_an_expired_grant(client):
    tokens, device = account(client)
    with dbmod.transaction() as db:
        actor = auth.create_user(db, "boss@example.test", PASSWORD, role=Role.ADMIN, verified=True)
        _, items = admin.generate_codes(db, actor, "lifetime", 1, entitlement=VPN)
        admin.revoke_code(db, actor, str(items[0][1].id))
        withdrawn = items[0][0]
    r = redeem(client, tokens, withdrawn)
    assert r.status_code == 400 and r.json()["error"]["code"] == "code_revoked"
    unused_expiry = make_codes("monthly", entitlement=LAN, expires_in_days=1)[0]
    later(client, tokens, 2 * DAY)
    r = redeem(client, tokens, unused_expiry)                # the unused code ran out: code_expired
    assert r.status_code == 400 and r.json()["error"]["code"] == "code_expired"
    assert redeem(client, tokens, module_code(LAN, "30")).status_code == 200
    later(client, tokens, 31 * DAY)                          # the grant it gave ran out: expired
    out = validate(client, tokens, device)
    assert out["result"] == "expired" and out["lease"] is None and not has(tokens, LAN)


def test_revoking_one_module_keeps_the_others_and_ends_its_certificate(client):
    tokens, device = account(client)
    redeem(client, tokens, module_code(VPN))
    redeem(client, tokens, module_code(LAN))
    first = validate(client, tokens, device)
    _, cert = signing.verify(first["offline_certificate"], trusted(client), "akeno-access-offline")
    assert cert["ents"] == sorted([VPN, LAN]) and cert["ent"] == "akeno-access"
    with dbmod.transaction() as db:
        actor = auth.create_user(db, "boss@example.test", PASSWORD, role=Role.ADMIN, verified=True)
        from akeno_server.models import User
        admin.grant_status(db, actor, db.get(User, uid(tokens)), VPN, "revoked", "refund")
    out = validate(client, tokens, device, serial=cert["serial"])
    assert set(granted(out)) == {LAN} and not has(tokens, VPN) and has(tokens, LAN)
    _, new_cert = signing.verify(out["offline_certificate"], trusted(client), "akeno-access-offline")
    assert new_cert["ents"] == [LAN] and new_cert["serial"] != cert["serial"]
    with dbmod.transaction() as db:
        from akeno_server.models import User
        admin.grant_status(db, actor, db.get(User, uid(tokens)), LAN, "disabled")
    out = validate(client, tokens, device, serial=new_cert["serial"])
    assert out["result"] == "license_disabled" and out["certificate_revoked"] and out["lease"] is None


# -- 13-16: signed documents and older apps --------------------------------------------------------------------------
def test_certificates_list_only_lifetime_grants_and_tampering_breaks_them(client):
    tokens, device = account(client)
    redeem(client, tokens, module_code(VPN))
    redeem(client, tokens, make_codes("monthly")[0])          # AKENO Full for 30 days: not in a certificate
    out = validate(client, tokens, device)
    token = out["offline_certificate"]
    _, cert = signing.verify(token, trusted(client), "akeno-access-offline")
    assert cert["ents"] == [VPN] and [g["ent"] for g in cert["grants"]] == [VPN]
    head, body, sig = token.split(".")
    forged = json.loads(b64d(body)) | {"ents": [SUITE, VPN]}
    forged_body = base64.urlsafe_b64encode(json.dumps(forged).encode()).decode().rstrip("=")
    with pytest.raises(signing.BadSignature):
        signing.verify(f"{head}.{forged_body}.{sig}", trusted(client), "akeno-access-offline")
    with pytest.raises(signing.BadSignature):                 # an access document is never a suite document
        signing.verify(token, trusted(client), "akeno-offline-license")
    with pytest.raises(signing.BadSignature):
        signing.verify(out["lease"], trusted(client), "akeno-lease")


def test_existing_full_licenses_answer_exactly_as_before(client):
    tokens, device = account(client)
    redeem(client, tokens, make_codes("lifetime")[0], access=False)
    old = validate(client, tokens, device, access=False)
    assert old["result"] == "ok" and old["statement"]["ent"] == SUITE and old["lease_payload"]["ent"] == SUITE
    _, cert = signing.verify(old["offline_certificate"], trusted(client), "akeno-offline-license")
    assert cert["ent"] == SUITE and cert["lt"] == "lifetime"
    again = validate(client, tokens, device, access=False, serial=cert["serial"])
    assert again["offline_certificate"] == "unchanged"
    new = validate(client, tokens, device, serial=cert["serial"])        # the same PC after updating AKENO
    assert set(granted(new)) == {SUITE} and new["offline_certificate"] not in (None, "unchanged")


def test_an_older_app_never_sees_a_module_grant_as_the_suite(client):
    tokens, device = account(client)
    code = module_code(VPN)
    r = redeem(client, tokens, code, access=False)            # AKENO 3.1.1 redeems a module code
    assert r.status_code == 409 and r.json()["error"]["code"] == "update_required"
    with dbmod.session() as db:
        assert db.query(LicenseCode).one().status == "available"
    assert redeem(client, tokens, code).status_code == 200    # after updating
    old = validate(client, tokens, device, access=False)
    assert old["result"] == "no_license" and old["lease"] is None and old["offline_certificate"] is None
    assert old["statement"]["ent"] == SUITE and old["statement"]["res"] == "no_license"


def test_the_website_redeems_module_codes_for_any_app_version(client):
    tokens, _ = account(client)
    code = module_code(LAN)
    with dbmod.session() as db:
        from akeno_server.models import User
        done = lic.redeem(db, db.get(User, uid(tokens)), code, "", "web")
    assert done.entitlement == LAN and has(tokens, LAN)


# -- 17: devices -----------------------------------------------------------------------------------------------------
def test_device_limits_hold_for_module_licenses(client):
    tokens, _ = account(client)
    redeem(client, tokens, module_code(VPN))
    register_device(client, tokens, "pc-2")
    r = client.post("/api/v1/devices/register", headers=bearer(tokens),
                    json={"fingerprint": __import__("tests.helpers").helpers.fingerprint("pc-3"), "label": "Third"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "device_limit_reached"
    redeem(client, tokens, make_codes("monthly", entitlement=LAN, device_allowance=3)[0])
    assert register_device(client, tokens, "pc-3")["label"] == "Gaming PC"


def test_a_lease_is_bound_to_its_pc(client):
    tokens, device = account(client)
    redeem(client, tokens, module_code(VPN))
    out = validate(client, tokens, device)
    assert out["lease_payload"]["dev"] == device["id"] and len(out["lease_payload"]["dfp"]) == 64
    other = register_device(client, tokens, "pc-2")
    assert validate(client, tokens, other)["lease_payload"]["dev"] == other["id"]


# -- 18: staff -------------------------------------------------------------------------------------------------------
def _staff(role):
    with dbmod.transaction() as db:
        return auth.create_user(db, f"{role.value}@example.test", PASSWORD, role=role, verified=True).id


def test_only_admins_create_module_codes_and_grants(client):
    tokens, _ = account(client)
    support, adm, sa = _staff(Role.SUPPORT), _staff(Role.ADMIN), _staff(Role.SUPER_ADMIN)
    from akeno_server.models import User
    with dbmod.session() as db:
        target = db.get(User, uid(tokens))
        for call in (lambda a: admin.generate_codes(db, a, "lifetime", 1, entitlement=VPN),
                     lambda a: admin.set_grant(db, a, target, VPN, None),
                     lambda a: admin.grant_status(db, a, target, VPN, "revoked")):
            with pytest.raises(ApiError) as exc:
                call(db.get(User, support))
            assert exc.value.code == "insufficient_role"
        admin.set_grant(db, db.get(User, adm), target, LAN, 30)
        with pytest.raises(ApiError) as exc:                  # only a super admin switches VPN licenses
            admin.update_settings(db, db.get(User, adm), {"vpn_module_licenses": False})
        assert exc.value.code == "insufficient_role"
        admin.update_settings(db, db.get(User, sa), {"vpn_module_licenses": False})
        with pytest.raises(ApiError) as exc:
            admin.generate_codes(db, db.get(User, adm), "lifetime", 1, entitlement=VPN)
        assert exc.value.code == "vpn_licenses_locked"
        with pytest.raises(ApiError) as exc:
            admin.generate_codes(db, db.get(User, adm), "trial_24h", 1, entitlement=LAN)
        assert exc.value.code == "invalid_code_type"
        with pytest.raises(ApiError) as exc:
            admin.generate_codes(db, db.get(User, adm), "lifetime", 1, entitlement="akeno-dlss")
        assert exc.value.code == "invalid_entitlement"
        db.commit()
    assert has(tokens, LAN) and not has(tokens, VPN)


def test_admin_api_generates_vpn_lifetime_codes_and_manages_grants(client):
    from tests.helpers import staff, staff_login
    _, secret = staff()
    st = staff_login(client, "admin@example.test", secret)
    r = client.post("/api/v1/admin/codes/generate", headers=bearer(st),
                    json={"code_type": "lifetime", "entitlement": VPN, "count": 5, "note": "friends"})
    assert r.status_code == 200, r.text
    codes = r.json()["codes"]
    assert len(set(codes)) == 5 and all(c.startswith("AKENO-") for c in codes)
    assert {i["product"] for i in r.json()["items"]} == {"AKENO VPN"}
    assert {i["length"] for i in r.json()["items"]} == {"lifetime"}
    listed = client.get("/api/v1/admin/codes", headers=bearer(st), params={"entitlement": VPN}).json()
    assert listed["total"] == 5 and all(c not in json.dumps(listed) for c in codes)   # never shown again
    tokens, _ = account(client)
    assert redeem(client, tokens, codes[0]).status_code == 200
    user_id = tokens["user"]["id"]
    r = client.post(f"/api/v1/admin/users/{user_id}/grants", headers=bearer(st),
                    json={"entitlement": LAN, "days": 30, "note": "test"})
    assert r.status_code == 200 and set(r.json()["access"]["running"]) == {VPN, LAN}
    r = client.post(f"/api/v1/admin/users/{user_id}/grants/extend", headers=bearer(st),
                    json={"entitlement": LAN, "days": 10})
    lan = next(e for e in r.json()["access"]["entitlements"] if e["entitlement"] == LAN)
    assert lan["remaining_seconds"] == pytest.approx(40 * DAY, abs=5)
    r = client.post(f"/api/v1/admin/users/{user_id}/grants/status", headers=bearer(st),
                    json={"entitlement": VPN, "status": "disabled"})
    assert r.json()["access"]["running"] == [LAN]
    detail = client.get(f"/api/v1/admin/users/{user_id}", headers=bearer(st)).json()
    kinds = [(h["entitlement"], h["kind"]) for h in detail["history"]]
    assert (VPN, "redeem") in kinds and (VPN, "status_disabled") in kinds and (LAN, "extend") in kinds
    history = client.get("/api/v1/licenses/history", headers=bearer(tokens)).json()
    assert {h["entitlement"] for h in history} == {VPN, LAN}


def test_admin_website_generates_vpn_lifetime_codes(web):
    from tests.helpers import staff
    from tests.test_web import staff_sign_in
    _, secret = staff()
    staff_sign_in(web, "admin@example.test", secret)
    page = web.get("/admin/codes")
    assert "AKENO VPN" in page.text and "Product / module" in page.text
    csrf = page.text.split('name="csrf" value="')[1].split('"')[0]
    r = web.post("/admin/codes/generate", data={"csrf": csrf, "entitlement": VPN, "length": "lifetime",
                                                 "count": "3", "batch": "vpn-friends"})
    assert r.status_code == 200 and r.text.count("AKENO-") >= 3 and "AKENO VPN" in r.text
    r = web.post("/admin/codes/generate", data={"csrf": csrf, "entitlement": LAN, "length": "custom",
                                                 "custom_days": "45", "count": "1"})
    assert r.status_code == 200 and "45" in r.text
    with dbmod.session() as db:
        rows = db.query(LicenseCode).all()
        assert {(c.entitlement, c.code_type, c.duration_days) for c in rows} == {(VPN, "lifetime", None),
                                                                                (LAN, "monthly", 45)}


# -- network APIs check their own entitlement ------------------------------------------------------------------------
def test_lan_play_api_needs_lan_play_not_vpn(tmp_path, monkeypatch, keys):
    from fastapi.testclient import TestClient
    from akeno_server.config import reset_config
    monkeypatch.setenv("AKENO_NETWORK_ENABLED", "true")
    monkeypatch.setenv("AKENO_NETWORK_DATABASE_PATH", str(tmp_path / "rooms.sqlite3"))
    reset_config()
    from akeno_server.main import create_app
    with TestClient(create_app()) as c:
        tokens = register(c, "lan@example.test")
        with dbmod.transaction() as db:
            settings.put(db, "vpn_module_licenses", True)
            auth.user_by_email(db, "lan@example.test").email_verified_at = clock.utcnow()
        device = register_device(c, tokens, "lan")
        body = {"device_id": device["id"], "public_key": base64.b64encode(secrets.token_bytes(32)).decode(),
                "name": "Room"}
        assert redeem(c, tokens, module_code(VPN)).status_code == 200
        r = c.post("/api/v1/network/lan/rooms", headers=bearer(tokens), json=body)
        assert r.status_code == 403 and r.json()["error"]["code"] == "active_license_required"
        assert redeem(c, tokens, module_code(LAN)).status_code == 200
        assert c.post("/api/v1/network/lan/rooms", headers=bearer(tokens), json=body).status_code == 201
