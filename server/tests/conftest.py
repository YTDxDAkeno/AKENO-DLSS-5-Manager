"""Test setup: a migrated database (SQLite by default, PostgreSQL with AKENO_TEST_DATABASE_URL), a throwaway key
directory, fast Argon2 parameters and a clean state for every test."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = Path(tempfile.mkdtemp(prefix="akeno-server-tests-"))

os.environ.update({
    "AKENO_ENVIRONMENT": "test",
    "AKENO_ROLE": "all",
    "AKENO_DATABASE_URL": os.environ.get("AKENO_TEST_DATABASE_URL") or f"sqlite:///{_TMP / 'test.sqlite3'}",
    "AKENO_KEYS_DIR": str(_TMP / "keys"),
    "AKENO_MAIL_BACKEND": "console",
    "AKENO_TOKEN_SECRET": "test-token-secret-0123456789abcdef0123456789",
    "AKENO_CODE_PEPPER": "test-code-pepper-0123456789abcdef0123456789ab",
    "AKENO_ARGON2_TIME_COST": "1",
    "AKENO_ARGON2_MEMORY_KIB": "8192",
    "AKENO_ARGON2_PARALLELISM": "1",
    "AKENO_COOKIE_SECURE": "false",
    "AKENO_PUBLIC_URL": "http://localhost:8000",
})

from akeno_server import clock, db as dbmod, mail  # noqa: E402
from akeno_server.auth import passwords  # noqa: E402
from akeno_server.auth.ratelimit import LIMITER  # noqa: E402
from akeno_server.config import reset_config  # noqa: E402
from akeno_server.models import Base  # noqa: E402

POSTGRES = os.environ["AKENO_DATABASE_URL"].startswith("postgresql")


def migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    os.environ["AKENO_DATABASE_URL"] = url
    reset_config()
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def _database():
    url = os.environ["AKENO_DATABASE_URL"]
    if POSTGRES:
        engine = dbmod.make_engine(url)
        with engine.begin() as conn:
            Base.metadata.drop_all(conn)
            conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
        engine.dispose()
    migrate(url)
    dbmod.use_engine(dbmod.make_engine(url))
    yield


def _wipe() -> None:
    engine = dbmod.engine()
    with engine.begin() as conn:
        if POSTGRES:
            names = ", ".join(t.name for t in Base.metadata.sorted_tables)
            conn.exec_driver_sql(f"TRUNCATE {names} CASCADE")
        else:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(table.delete())


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setenv("AKENO_KEYS_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("AKENO_ROLE", "api")
    reset_config()
    passwords.reset_cache()
    _wipe()
    LIMITER.reset()
    clock.reset()
    mail.outbox.clear()
    yield
    clock.reset()


@pytest.fixture
def keys():
    from akeno_server.licensing import signing
    with dbmod.transaction() as db:
        row = signing.store().create(db)
    return row.kid


@pytest.fixture
def app(keys):
    from akeno_server.main import create_app
    return create_app()


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture
def web(keys, monkeypatch):
    """The websites and the API in one process (AKENO_ROLE=all), like a development server."""
    monkeypatch.setenv("AKENO_ROLE", "all")
    reset_config()
    from fastapi.testclient import TestClient
    from akeno_server.main import create_app
    with TestClient(create_app(), base_url="http://localhost:8000") as c:
        yield c


@pytest.fixture
def db():
    s = dbmod.session()
    yield s
    s.close()
