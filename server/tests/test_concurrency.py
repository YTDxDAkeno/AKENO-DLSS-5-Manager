"""Redemption races: many requests at the same instant, against the real database (PostgreSQL in CI)."""

from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import httpx
import pytest
import uvicorn

from akeno_server import db as dbmod
from akeno_server.auth import service as auth
from akeno_server.errors import ApiError
from akeno_server.licensing import service as lic
from akeno_server.models import LicenseCode, LicenseRedemption, User
from tests.helpers import PASSWORD, make_codes


def _users(n: int) -> list:
    ids = []
    with dbmod.transaction() as db:
        for i in range(n):
            ids.append(auth.create_user(db, f"racer{i}@example.test", PASSWORD).id)
    return ids


def _redeem(user_id, code, barrier):
    db = dbmod.session()
    try:
        user = db.get(User, user_id)
        db.rollback()                 # hold no lock while waiting (SQLite takes the write lock at BEGIN)
        barrier.wait(timeout=60)
        return "ok" if not lic.redeem(db, user, code).already else "already"
    except ApiError as exc:
        return exc.code
    finally:
        db.close()


def test_one_code_many_accounts_at_once():
    code = make_codes("monthly")[0]
    users = _users(12)
    barrier = threading.Barrier(len(users))
    with ThreadPoolExecutor(len(users)) as pool:
        results = list(pool.map(lambda u: _redeem(u, code, barrier), users))
    assert results.count("ok") == 1, results
    assert set(results) == {"ok", "code_used"}
    with dbmod.session() as db:
        assert db.query(LicenseRedemption).count() == 1
        assert db.query(LicenseCode).one().status == "redeemed"


def test_one_code_same_account_twice_at_once():
    code = make_codes("monthly")[0]
    user = _users(1)[0]
    barrier = threading.Barrier(6)
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda _: _redeem(user, code, barrier), range(6)))
    assert results.count("ok") == 1, results
    assert set(results) <= {"ok", "already", "code_used"}
    with dbmod.session() as db:
        assert db.query(LicenseRedemption).count() == 1


def test_many_codes_same_account_at_once_lose_no_time():
    codes = make_codes("trial_72h", count=1) + make_codes("monthly", count=8)
    from akeno_server import settings
    with dbmod.transaction() as db:
        settings.put(db, "trial_once_per_account", False)
    user = _users(1)[0]
    barrier = threading.Barrier(len(codes))
    with ThreadPoolExecutor(len(codes)) as pool:
        results = list(pool.map(lambda c: _redeem(user, c, barrier), codes))
    assert results == ["ok"] * len(codes), results
    with dbmod.session() as db:
        license_ = lic.license_of(db, user)
        reds = db.query(LicenseRedemption).order_by(LicenseRedemption.redeemed_at).all()
        first = reds[0].redeemed_at
        total = timedelta(hours=72) + timedelta(days=30 * 8)
        # every code's time is there: the expiry is the first redemption plus all durations (within the race window)
        assert abs((license_.expires_at - (first + total)).total_seconds()) < 30
        assert license_.license_type == "monthly"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(app):
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def test_http_redemption_race(live_server):
    from akeno_server.auth.ratelimit import LIMITER
    LIMITER.enabled = False
    try:
        code = make_codes("lifetime")[0]
        tokens = []
        with httpx.Client(base_url=live_server) as c:
            for i in range(10):
                r = c.post("/api/v1/auth/register", json={"email": f"h{i}@example.test", "password": PASSWORD})
                tokens.append(r.json()["access_token"])
        barrier = threading.Barrier(len(tokens))

        def go(tok):
            with httpx.Client(base_url=live_server, timeout=30) as c:
                barrier.wait(timeout=60)
                return c.post("/api/v1/licenses/redeem", json={"code": code},
                              headers={"Authorization": f"Bearer {tok}"}).status_code
        with ThreadPoolExecutor(len(tokens)) as pool:
            statuses = list(pool.map(go, tokens))
        assert statuses.count(200) == 1 and statuses.count(409) == len(tokens) - 1, statuses
    finally:
        LIMITER.enabled = True
