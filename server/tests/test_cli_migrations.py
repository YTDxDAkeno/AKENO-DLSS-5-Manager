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
