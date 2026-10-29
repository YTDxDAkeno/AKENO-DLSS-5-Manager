"""The command line (bootstrap, keys, checks) and the Alembic migrations on a fresh database."""

from __future__ import annotations

import io
import json
import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from akeno_server import cli
from akeno_server import db as dbmod
from akeno_server.models import Base, Role, User
from tests.conftest import POSTGRES, ROOT


def run(argv, stdin=None, monkeypatch=None):
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return cli.main(argv)


def test_create_super_admin_once(monkeypatch, capsys):
    assert run(["create-super-admin", "--email", "owner@example.test", "--password-stdin"],
               "a long enough password\n", monkeypatch) == 0
    with dbmod.session() as db:
        user = db.query(User).one()
        assert user.role == Role.SUPER_ADMIN.value and user.email_verified_at is not None
        assert "a long enough password" not in user.credential.password_hash
    assert run(["create-super-admin", "--email", "second@example.test", "--password-stdin"],
               "another good password\n", monkeypatch) == 2
    assert "exists already" in capsys.readouterr().err


def test_create_super_admin_refuses_weak_password(monkeypatch):
    assert run(["create-super-admin", "--email", "owner@example.test", "--password-stdin"], "short\n",
               monkeypatch) == 1


def test_password_is_never_a_command_line_argument():
    with pytest.raises(SystemExit):
        cli.main(["create-super-admin", "--email", "x@example.test", "--password", "secret"])


def test_keys_commands(capsys):
    assert cli.main(["keys", "init"]) == 0
    first = capsys.readouterr().out
    kid = json.loads(first.strip().splitlines()[-1])["kid"]
    assert cli.main(["keys", "init"]) == 0 and "exists already" in capsys.readouterr().out
    assert cli.main(["keys", "rotate"]) == 0
    capsys.readouterr()
    assert cli.main(["keys", "export", "--server-url", "https://licenses.invalid"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["server_url"] == "https://licenses.invalid" and [k["kid"] for k in doc["keys"]][0] == kid
    assert len(doc["keys"]) == 2 and "PRIVATE" not in json.dumps(doc)
    assert cli.main(["keys", "list"]) == 0
    listing = capsys.readouterr().out
    assert "retired" in listing and "active" in listing


def test_check_and_cleanup(capsys, keys):
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "database        ok" in out and keys in out
    assert cli.main(["cleanup"]) == 0


def test_reset_2fa(capsys):
    from tests.helpers import staff
    staff()
    assert cli.main(["reset-2fa", "--email", "admin@example.test"]) == 0
    with dbmod.session() as db:
        assert db.query(User).one().credential.totp_enabled_at is None


def _alembic(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    os.environ["AKENO_DATABASE_URL"] = url
    from akeno_server.config import reset_config
    reset_config()
    return cfg


def test_migrations_match_models_and_downgrade(tmp_path, monkeypatch):
    url = os.environ["AKENO_DATABASE_URL"]
    if not POSTGRES:
        url = f"sqlite:///{tmp_path / 'fresh.sqlite3'}"
    monkeypatch.setenv("AKENO_DATABASE_URL", url)
    cfg = _alembic(url)
    engine = dbmod.make_engine(url)
    try:
        if POSTGRES:
            command.downgrade(cfg, "base")
        else:
            command.upgrade(cfg, "head")
        if POSTGRES:
            command.upgrade(cfg, "head")
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diff == [], diff
        command.downgrade(cfg, "base")
        with engine.connect() as conn:
            assert not [t for t in Base.metadata.tables if engine.dialect.has_table(conn, t)]
        command.upgrade(cfg, "head")
    finally:
        engine.dispose()


def test_migration_0002_keeps_existing_licenses_and_its_downgrade_withdraws_module_codes(tmp_path, monkeypatch):
    """A 3.1 database (suite license, code, redemption, certificate) survives 0002 unchanged; going back to 0001
    removes module grants and withdraws unused module codes so the older server never reads them as suite codes."""
    import uuid
    from sqlalchemy import text
    url = f"sqlite:///{tmp_path / 'upgrade.sqlite3'}"
    monkeypatch.setenv("AKENO_DATABASE_URL", url)
    cfg = _alembic(url)
    engine = dbmod.make_engine(url)
    ids = {k: uuid.uuid4().hex for k in ("user", "lic", "code", "red", "dev", "cert", "vpncode", "grant", "used")}
    now = "2026-10-01 10:00:00.000000"
    try:
        command.upgrade(cfg, "0001")
        with engine.begin() as c:
            c.execute(text("INSERT INTO users (id,email,email_normalized,display_name,role,status,disabled_reason,"
                           "language,notes,created_at,updated_at,failed_logins) VALUES (:user,'a@x.test','a@x.test',"
                           "'','user','active','','en','',:now,:now,0)"), {**ids, "now": now})
            c.execute(text("INSERT INTO licenses (id,user_id,license_type,status,activated_at,expires_at,device_limit,"
                           "created_at,updated_at) VALUES (:lic,:user,'lifetime','active',:now,NULL,2,:now,:now)"),
                      {**ids, "now": now})
            for code, status in ((ids["code"], "available"), (ids["used"], "redeemed")):
                c.execute(text("INSERT INTO license_codes (id,verifier,hint,code_type,status,note,batch,created_at,"
                               "reserved_for) VALUES (:id,:v,'ABCD','monthly',:st,'','b',:now,'')"),
                          {"id": code, "v": code * 2, "st": status, "now": now})
            c.execute(text("INSERT INTO license_redemptions (id,code_id,user_id,license_id,redeemed_at,new_type,"
                           "channel,ip) VALUES (:red,:used,:user,:lic,:now,'lifetime','app','')"), {**ids, "now": now})
            c.execute(text("INSERT INTO devices (id,user_id,fingerprint,label,os,app_version,created_at,last_seen_at,"
                           "revoked_by) VALUES (:dev,:user,:fp,'PC','','',:now,:now,'')"),
                      {**ids, "fp": "f" * 64, "now": now})
            c.execute(text("INSERT INTO offline_certificates (id,license_id,device_id,kid,issued_at,revoke_reason,"
                           "payload_sha256) VALUES (:cert,:lic,:dev,'k',:now,'','x')"), {**ids, "now": now})
        command.upgrade(cfg, "head")
        with engine.begin() as c:
            assert c.execute(text("SELECT license_type, expires_at FROM licenses")).one() == ("lifetime", None)
            assert set(c.execute(text("SELECT entitlement FROM license_codes")).scalars()) == {"akeno-suite"}
            assert c.execute(text("SELECT user_id FROM offline_certificates")).scalar() == ids["user"]
            assert c.execute(text("SELECT count(*) FROM license_redemptions WHERE license_id=:lic"), ids).scalar() == 1
            c.execute(text("INSERT INTO license_codes (id,verifier,hint,code_type,entitlement,status,note,batch,"
                           "created_at,reserved_for) VALUES (:vpncode,'vv','WXYZ','lifetime','akeno-vpn','available',"
                           "'','b',:now,'')"), {**ids, "now": now})
            c.execute(text("INSERT INTO feature_grants (id,user_id,entitlement,grant_type,status,activated_at,"
                           "device_limit,created_at,updated_at) VALUES (:grant,:user,'akeno-vpn','lifetime','active',"
                           ":now,2,:now,:now)"), {**ids, "now": now})
        assert cli.main(["migrate-down", "0001"]) == 0
        with engine.begin() as c:
            rows = dict(c.execute(text("SELECT id, status FROM license_codes")).all())
            assert rows == {ids["code"]: "available", ids["used"]: "redeemed", ids["vpncode"]: "revoked"}
            assert c.execute(text("SELECT license_type FROM licenses")).scalar() == "lifetime"
            assert c.execute(text("SELECT count(*) FROM offline_certificates")).scalar() == 1
            assert not engine.dialect.has_table(c, "feature_grants")
    finally:
        engine.dispose()
