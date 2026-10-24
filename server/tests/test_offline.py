"""Signed leases, lifetime offline certificates, revocation and key rotation."""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from akeno_server import clock
from akeno_server import db as dbmod
from akeno_server import settings
from akeno_server.auth.tokens import b64d
from akeno_server.licensing import signing
from tests.helpers import bearer, fingerprint, make_codes, register, register_device

HOUR = 3600


def trusted(client) -> dict:
    return {k["kid"]: Ed25519PublicKey.from_public_bytes(b64d(k["public_key"]))
            for k in client.get("/api/v1/system/keys").json()["keys"]}


def validate(client, tokens, device, serial=None):
    r = client.post("/api/v1/licenses/validate", headers=bearer(tokens),
                    json={"device_id": device["id"], "app_version": "3.0.0", "certificate_serial": serial})
    assert r.status_code == 200, r.text
    return r.json()


def setup(client, kind="lifetime"):
    tokens = register(client)
    client.post("/api/v1/licenses/redeem", headers=bearer(tokens), json={"code": make_codes(kind)[0]})
    return tokens, register_device(client, tokens)


def test_lifetime_validation_returns_certificate_and_lease(client):
    tokens, device = setup(client)
    out = validate(client, tokens, device)
    assert out["result"] == "ok" and out["license"]["lifetime"]
    _, cert = signing.verify(out["offline_certificate"], trusted(client), "akeno-offline-license")
    assert cert["ent"] == "akeno-suite" and cert["lt"] == "lifetime" and "exp" not in cert
    assert cert["dev"] == device["id"] and cert["dfp"] == fingerprint()
    _, lease = signing.verify(out["lease"], trusted(client), "akeno-lease")
    assert lease["exp"] - lease["iat"] == 72 * HOUR
    again = validate(client, tokens, device, cert["serial"])
    assert again["offline_certificate"] == "unchanged" and not again["certificate_revoked"]


def test_lease_never_outlives_the_license(client):
    tokens, device = setup(client, "trial_24h")
    out = validate(client, tokens, device)
    _, lease = signing.verify(out["lease"], trusted(client), "akeno-lease")
    assert lease["exp"] == lease["lexp"] and lease["exp"] - lease["iat"] <= 24 * HOUR
    assert out["offline_certificate"] is None


def test_grace_period_is_configurable(client):
    with dbmod.transaction() as db:
        settings.put(db, "offline_grace_hours", 6)
    tokens, device = setup(client, "monthly")
    _, lease = signing.verify(validate(client, tokens, device)["lease"], trusted(client), "akeno-lease")
    assert lease["exp"] - lease["iat"] == 6 * HOUR and lease["grace"] == 6 * HOUR


def test_expired_license_gets_no_lease(client):
    tokens, device = setup(client, "trial_24h")
    clock.advance(25 * HOUR)
    tokens = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()
    out = validate(client, tokens, device)
    assert out["result"] == "expired" and out["lease"] is None and not out["license"]["active"]


def test_offline_certificate_only_for_lifetime(client):
    tokens, device = setup(client, "monthly")
    r = client.post("/api/v1/licenses/offline-certificate", headers=bearer(tokens), json={"device_id": device["id"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_lifetime"


def test_new_certificate_replaces_the_old_one(client):
    tokens, device = setup(client)
    first = client.post("/api/v1/licenses/offline-certificate", headers=bearer(tokens),
                        json={"device_id": device["id"]}).json()
    second = client.post("/api/v1/licenses/offline-certificate", headers=bearer(tokens),
                         json={"device_id": device["id"]}).json()
    assert first["serial"] != second["serial"]
    out = validate(client, tokens, device, first["serial"])
    assert out["offline_certificate"] not in (None, "unchanged")      # the app gets the current one back


def test_disabled_license_revokes_certificate(client):
    from akeno_server.admin import service as admin
    from akeno_server.auth import service as auth
    tokens, device = setup(client)
    serial = signing.verify(validate(client, tokens, device)["offline_certificate"], trusted(client),
                            "akeno-offline-license")[1]["serial"]
    from tests.helpers import staff
    staff("boss@example.test")
    with dbmod.transaction() as db:
        boss = auth.user_by_email(db, "boss@example.test")
        admin.license_status(db, boss, auth.user_by_email(db, "player@example.test"), "disabled")
    out = validate(client, tokens, device, serial)
    assert out["result"] == "license_disabled" and out["certificate_revoked"] and out["lease"] is None


def test_removed_device_loses_certificate(client):
    tokens, device = setup(client)
    serial = signing.verify(validate(client, tokens, device)["offline_certificate"], trusted(client),
                            "akeno-offline-license")[1]["serial"]
    client.delete(f"/api/v1/devices/{device['id']}", headers=bearer(tokens))
    out = validate(client, tokens, device, serial)
    assert out["result"] == "device_not_registered" and out["certificate_revoked"]


def test_lifetime_changed_to_monthly_revokes_certificate(client):
    from akeno_server.admin import service as admin
    from akeno_server.auth import service as auth
    from tests.helpers import staff
    tokens, device = setup(client)
    serial = signing.verify(validate(client, tokens, device)["offline_certificate"], trusted(client),
                            "akeno-offline-license")[1]["serial"]
    staff("boss@example.test")
    with dbmod.transaction() as db:
        admin.set_license(db, auth.user_by_email(db, "boss@example.test"),
                          auth.user_by_email(db, "player@example.test"), "monthly")
    out = validate(client, tokens, device, serial)
    assert out["result"] == "ok" and out["certificate_revoked"] and out["offline_certificate"] is None


def test_key_rotation_keeps_old_certificates_valid(client, keys):
    tokens, device = setup(client)
    old_cert = validate(client, tokens, device)["offline_certificate"]
    with dbmod.transaction() as db:
        new = signing.store().create(db)
    doc = client.get("/api/v1/system/keys").json()["keys"]
    assert [k["kid"] for k in doc] == [keys, new.kid]
    assert doc[0]["retired_at"] is not None and doc[1]["endorsed_by"] == keys
    old_key = {keys: Ed25519PublicKey.from_public_bytes(b64d(doc[0]["public_key"]))}
    _, endorsement = signing.verify(doc[1]["endorsement"], old_key, "akeno-key-endorsement")
    assert endorsement["kid"] == new.kid and endorsement["public_key"] == doc[1]["public_key"]
    kid, _ = signing.verify(old_cert, trusted(client), "akeno-offline-license")
    assert kid == keys                                              # still valid after rotation
    serial = signing.verify(old_cert, trusted(client), "akeno-offline-license")[1]["serial"]
    out = validate(client, tokens, device, serial)
    assert signing.verify(out["lease"], trusted(client), "akeno-lease")[0] == new.kid


def test_tampered_or_foreign_tokens_fail():
    import pytest
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    kid = signing.kid_for(signing.raw_public(key.public_key()))
    token = signing.sign(key, kid, "akeno-lease", {"ent": "akeno-suite", "exp": 1})
    keys = {kid: key.public_key()}
    assert signing.verify(token, keys, "akeno-lease")[1]["exp"] == 1
    h, b, s = token.split(".")
    forged = h + "." + b[:-2] + ("AA" if b[-2:] != "AA" else "BB") + "." + s
    for bad, typ in ((forged, "akeno-lease"), (token, "akeno-offline-license"), ("x.y", "akeno-lease")):
        with pytest.raises(signing.BadSignature):
            signing.verify(bad, keys, typ)
    other = Ed25519PrivateKey.generate()
    with pytest.raises(signing.BadSignature):
        signing.verify(signing.sign(other, kid, "akeno-lease", {}), keys, "akeno-lease")


def test_private_keys_stay_in_key_directory(client, keys):
    import os
    store = signing.store()
    path = store.dir / f"{keys}.ed25519.pem"
    assert path.exists() and (os.stat(path).st_mode & 0o077) == 0
    with dbmod.session() as db:
        from akeno_server.models import SigningKey
        row = db.get(SigningKey, keys)
        assert "PRIVATE" not in (row.public_key + (row.endorsement or ""))
    assert "PRIVATE" not in client.get("/api/v1/system/keys").text


def test_validation_without_signing_key_reports_unavailable(app, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("AKENO_KEYS_DIR", str(tmp_path / "empty"))
    from akeno_server.config import reset_config
    reset_config()
    with TestClient(app) as c:
        tokens = register(c)
        c.post("/api/v1/licenses/redeem", headers=bearer(tokens), json={"code": make_codes("monthly")[0]})
        device = register_device(c, tokens)
        r = c.post("/api/v1/licenses/validate", headers=bearer(tokens), json={"device_id": device["id"]})
        assert r.status_code == 503 and r.json()["error"]["code"] == "signing_unavailable"
        assert c.get("/api/v1/system/ready").status_code == 503
