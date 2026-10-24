"""The app side of licensing without a server: storage, the clock guard, states, the welcome screen, the shell.

The full flow against a real server is in server/tests/test_client_e2e.py (it needs the server's packages).
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from akeno.licensing import LicenseManager, Status, device
from akeno.licensing import config as cfgmod
from akeno.licensing.api import ApiError, ServerUnavailable, check_url
from akeno.licensing.clock import ClockGuard
from akeno.licensing.storage import SecureStore


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Signer:
    """Signs like the server (JWS, EdDSA) so the app's verification is exercised for real."""

    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public = b64e(raw)
        import hashlib
        self.kid = hashlib.sha256(raw).hexdigest()[:16]

    def sign(self, typ: str, payload: dict) -> str:
        h = b64e(json.dumps({"alg": "EdDSA", "kid": self.kid, "typ": typ}, separators=(",", ":")).encode())
        b = b64e(json.dumps(payload, separators=(",", ":")).encode())
        return f"{h}.{b}.{b64e(self.key.sign(f'{h}.{b}'.encode()))}"


class FakeApi:
    """A scripted license server: answers per path; ``down`` simulates an outage."""

    down = False
    answers: dict = {}
    calls: list = []

    def __init__(self, url):
        self.url = url

    def call(self, method, path, body=None, token=None):
        FakeApi.calls.append((method, path, body))
        if FakeApi.down:
            raise ServerUnavailable("offline")
        answer = FakeApi.answers[path]
        if isinstance(answer, Exception):
            raise answer
        return answer(body) if callable(answer) else answer


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AKENO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AKENO_DEVICE_ID", "test-machine")
    FakeApi.down = False
    FakeApi.calls = []
    signer = Signer()
    now = time.time()

    def lease(lt="monthly", lexp=None, grace=72 * 3600):
        lexp = lexp if lexp is not None else (None if lt == "lifetime" else now + 30 * 86400)
        exp = now + grace if lexp is None else min(lexp, now + grace)
        return signer.sign("akeno-lease", {"ent": "akeno-suite", "lt": lt, "dev": "dev-1",
                                           "dfp": device.fingerprint(), "iat": int(now), "exp": int(exp),
                                           "lexp": None if lexp is None else int(lexp), "act": int(now)})

    def validate(lt="monthly", result="ok", cert=False, **kw):
        out = {"result": result, "server_time": now, "license": {"status": "active", "license_type": lt},
               "lease": lease(lt, **kw) if result == "ok" else None, "offline_certificate": None,
               "certificate_revoked": False, "validation_interval_seconds": 3600}
        if cert:
            out["offline_certificate"] = signer.sign("akeno-offline-license", {
                "ent": "akeno-suite", "lt": "lifetime", "dev": "dev-1", "dfp": device.fingerprint(), "serial": "s1",
                "act": int(now)})
        return out

    tokens = {"access_token": "a", "refresh_token": "r", "expires_in": 900,
              "user": {"id": "u1", "email": "player@example.test", "display_name": "P"}}
    FakeApi.answers = {"/auth/login": tokens, "/auth/register": tokens, "/auth/refresh": tokens,
                       "/devices/register": {"id": "dev-1", "label": "Gaming PC"},
                       "/licenses/validate": validate("monthly"), "/auth/logout": {}}

    def make(**kw):
        cfg = cfgmod.LicensingConfig("https://licenses.invalid", {signer.kid: signer.public})
        return LicenseManager(cfg, SecureStore(tmp_path / "license"), FakeApi)
    return make, validate, signer


def test_states_from_sign_in_to_active(env):
    make, validate, _ = env
    m = make()
    assert m.view().status is Status.NOT_LOGGED_IN
    FakeApi.answers["/licenses/validate"] = validate(result="no_license") | {"license": {"status": "none"}}
    assert m.sign_in("player@example.test", "pw").status is Status.NOT_ACTIVATED
    FakeApi.answers["/licenses/validate"] = validate("monthly")
    FakeApi.answers["/licenses/redeem"] = {"license": {"status": "active"}, "code_type": "monthly"}
    view = m.redeem("AKENO-AAAA-BBBB-CCCC-DDDD")
    assert view.status is Status.ACTIVE_ONLINE and view.unlocked and view.license_type == "monthly"


def test_cached_lease_after_restart_and_outage(env):
    make, validate, _ = env
    make().sign_in("player@example.test", "pw")
    FakeApi.down = True
    restart = make()
    assert restart.view().status is Status.ACTIVE_CACHED
    assert restart.check_if_due().unlocked                    # the outage changes nothing


def test_lifetime_offline_and_expiring_and_expired(env):
    make, validate, _ = env
    FakeApi.answers["/licenses/validate"] = validate("lifetime", cert=True)
    make().sign_in("player@example.test", "pw")
    FakeApi.down = True
    assert make().view().status is Status.LIFETIME_OFFLINE
    FakeApi.down = False
    FakeApi.answers["/licenses/validate"] = validate("trial_24h", lexp=time.time() + 3600)
    m = make()
    m.sign_out()
    m.sign_in("player@example.test", "pw")
    assert m.view().status is Status.EXPIRING
    FakeApi.answers["/licenses/validate"] = validate(result="expired") | {
        "license": {"status": "expired", "license_type": "trial_24h"}}
    assert m.validate().status is Status.EXPIRED


def test_forged_or_foreign_lease_does_not_unlock(env, tmp_path):
    make, validate, _ = env
    m = make()
    other = Signer()
    out = validate()
    out["lease"] = other.sign("akeno-lease", {"ent": "akeno-suite", "lt": "monthly", "dev": "dev-1",
                                              "dfp": device.fingerprint(), "iat": 1, "exp": time.time() + 99})
    FakeApi.answers["/licenses/validate"] = out
    FakeApi.answers["/system/keys"] = {"keys": [{"kid": other.kid, "public_key": other.public}]}
    with pytest.raises(Exception):
        m.sign_in("player@example.test", "pw")                # unknown key, no endorsement: refused
    assert not m.view().unlocked


def test_lease_from_another_pc_is_ignored(env, monkeypatch):
    make, validate, _ = env
    make().sign_in("player@example.test", "pw")
    monkeypatch.setenv("AKENO_DEVICE_ID", "a-copied-state-file-on-another-pc")
    FakeApi.down = True
    assert not make().view().unlocked


def test_auth_failure_versus_outage(env):
    make, validate, _ = env
    m = make()
    m.sign_in("player@example.test", "pw")
    m._access = None
    FakeApi.answers["/auth/refresh"] = ApiError(401, "invalid_refresh_token", "Please sign in again.")
    with pytest.raises(ApiError):
        m.validate()
    assert m.view().needs_sign_in and m.view().unlocked and not m.due()
    FakeApi.answers["/auth/refresh"] = ApiError(403, "account_disabled", "disabled")
    m.state["refresh_token"] = "r"
    with pytest.raises(ApiError):
        m.validate()
    assert m.view().status is Status.DISABLED


def test_clock_guard():
    state = {}
    g = ClockGuard(state)
    first = g.now()
    assert not first.rolled_back and state["high_water"] >= first.now
    state["high_water"] = time.time() + 3600                   # seen an hour "later" before
    g2 = ClockGuard(state)
    second = g2.now()
    assert second.rolled_back and second.now >= state["high_water"] - 1
    g2.learn(time.time())
    assert not state["rolled_back"] and state["high_water"] <= time.time() + 1


def test_storage_survives_damage(tmp_path):
    store = SecureStore(tmp_path)
    store.save({"a": 1})
    store.save({"a": 2})
    store.path.write_bytes(b"\x00garbage")
    assert store.load() == {"a": 1}                           # the previous good copy
    assert (tmp_path / "state.unreadable").exists()


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="DPAPI exists on Windows only")
def test_dpapi_round_trip(tmp_path):
    store = SecureStore(tmp_path)
    store.save({"refresh_token": "secret-value"})
    assert b"secret-value" not in store.path.read_bytes()
    assert store.load() == {"refresh_token": "secret-value"}


def test_server_address_rules():
    assert check_url("https://licenses.example/") == "https://licenses.example"
    assert check_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    for bad in ("http://licenses.example", "ftp://x", "https://"):
        with pytest.raises(ValueError):
            check_url(bad)


def test_fingerprint_is_pseudonymous(monkeypatch):
    monkeypatch.setenv("AKENO_DEVICE_ID", "1234-guid")
    fp = device.fingerprint()
    assert len(fp) == 64 and "1234" not in fp
    monkeypatch.setenv("AKENO_DEVICE_ID", "other")
    assert device.fingerprint() != fp


def test_gate_pages_and_errors(env, qtbot):
    from akeno.ui.license_gate import LicenseGate
    make, validate, _ = env
    m = make()
    gate = LicenseGate(m)
    qtbot.addWidget(gate)
    gate.show()
    assert gate.current() == "signin"
    FakeApi.answers["/auth/login"] = ApiError(401, "totp_required", "code")
    gate.si_email.setText("player@example.test")
    gate.si_password.setText("pw")
    gate._sign_in()
    qtbot.waitUntil(lambda: gate.si_totp.isVisible(), timeout=5000)
    assert "authenticator" in gate.notice.text().lower()
    FakeApi.answers["/auth/login"] = {"access_token": "a", "refresh_token": "r", "expires_in": 900,
                                      "user": {"id": "u1", "email": "player@example.test"}}
    FakeApi.answers["/licenses/validate"] = validate(result="no_license") | {"license": {"status": "none"}}
    unlocked = []
    gate.unlocked.connect(unlocked.append)
    gate.si_totp.setText("123456")
    gate._sign_in()
    qtbot.waitUntil(lambda: gate.current() == "redeem", timeout=5000)
    FakeApi.answers["/licenses/redeem"] = ApiError(400, "invalid_code", "nope")
    gate.code.setText("akeno aaaa bbbb cccc dddd")
    gate.code.textEdited.emit(gate.code.text())
    assert gate.code.text() == "AKENO-AAAA-BBBB-CCCC-DDDD"
    gate._redeem()
    qtbot.waitUntil(lambda: "does not exist" in gate.notice.text(), timeout=5000)
    FakeApi.answers["/licenses/redeem"] = {"license": {"status": "active"}, "code_type": "monthly"}
    FakeApi.answers["/licenses/validate"] = validate("monthly")
    gate._redeem()
    qtbot.waitUntil(lambda: bool(unlocked), timeout=5000)
    assert unlocked[0].unlocked


def test_gate_device_limit_dialog(env, qtbot):
    from akeno.licensing.manager import DeviceLimitReached
    from akeno.ui.license_gate import LicenseGate
    make, validate, _ = env
    gate = LicenseGate(make())
    qtbot.addWidget(gate)
    gate._failed(DeviceLimitReached(2, [{"id": "d1", "label": "Old PC", "last_seen_at": "2026-01-01T00:00:00Z"},
                                        {"id": "d2", "label": "Laptop", "last_seen_at": "2026-02-01T00:00:00Z"}]))
    assert gate.current() == "devices" and gate.dev_list.count() == 2


def test_shell_opens_suite_only_when_unlocked_and_locks_again(env, qtbot):
    from PySide6.QtWidgets import QWidget
    from akeno.ui.shell import SuiteShell
    make, validate, _ = env
    m = make()
    built = []

    def build():
        w = QWidget()
        qtbot.addWidget(w)
        built.append(w)
        return w
    shell = SuiteShell(m, build)
    FakeApi.down = True
    shell.start()
    assert not built and shell.gate is not None and shell.gate.isVisible()     # nothing heavy before a license
    FakeApi.down = False
    m.sign_in("player@example.test", "pw")
    qtbot.waitUntil(lambda: bool(built) and built[0].isVisible(), timeout=5000)
    assert shell.gate is None
    FakeApi.answers["/licenses/validate"] = validate(result="expired") | {"license": {"status": "expired"}}
    m.validate()
    qtbot.waitUntil(lambda: not built[0].isVisible(), timeout=5000)
    assert shell.gate is not None and shell.gate.current() == "redeem"
    assert len(built) == 1                      # the suite is kept, not rebuilt or closed
    shell.stop()
    shell.gate.close()


def test_main_window_has_account_and_license_pages(env, qtbot, tmp_path, monkeypatch):
    from akeno.services import AppServices
    from akeno.core.db import Database
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    make, validate, _ = env
    m = make()
    m.sign_in("player@example.test", "pw")
    svc = AppServices(db=Database(tmp_path / "db.sqlite3"))
    win = MainWindow(Controller(svc), m)
    qtbot.addWidget(win)
    win.go("license")
    assert win.current_page() == "license"
    assert win.pages["license"].summary.values["type"].text() in ("Monthly (30 days)", "Monatlich (30 Tage)")
    win.go("account")
    assert win.pages["account"].email.text() == "player@example.test"
