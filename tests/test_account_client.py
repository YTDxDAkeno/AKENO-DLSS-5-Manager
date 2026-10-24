"""The app's account logic without a server: registration and verification state, password and e-mail changes, the
end of a session, "keep me signed in", what is stored and what is logged, and how the HTTP client reports failures.

The same flows against the real server are in server/tests/test_client_e2e.py.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from akeno.licensing import Status
from akeno.licensing.api import ApiError, LicenseApi, ServerUnavailable
from akeno.licensing.storage import SecureStore
from test_licensing_client import FakeApi, env  # noqa: F401  (the fixture)

EMAIL = "player@example.test"
PASSWORD = "correct horse battery"


def registered(delivery: str | None = "sent", verified: bool = False) -> dict:
    out = {"access_token": "access-secret-1", "refresh_token": "refresh-secret-1", "expires_in": 900,
           "user": {"id": "u1", "email": EMAIL, "display_name": "P", "email_verified": verified,
                    "created_at": "2026-10-01T12:00:00Z"}}
    if delivery is not None:
        out["verification_email"] = delivery
    return out


# -- registration and verification ------------------------------------------------------------------------------------
@pytest.mark.parametrize("delivery", ["sent", "failed", "unavailable"])
def test_registration_keeps_what_the_server_said_about_the_email(env, delivery):
    make, validate, _ = env
    FakeApi.answers["/auth/register"] = registered(delivery)
    FakeApi.answers["/licenses/validate"] = validate(result="no_license") | {"license": {"status": "none"}}
    m = make()
    view = m.create_account(EMAIL, PASSWORD, "P", "de")
    assert view.status is Status.NOT_ACTIVATED and view.email_verified is False
    assert m.verification["email"] == EMAIL and m.verification["delivery"] == delivery
    assert make().verification["delivery"] == delivery, "kept for the next start"


def test_registration_with_an_older_server_says_unknown(env):
    make, validate, _ = env
    FakeApi.answers["/auth/register"] = registered(None)
    m = make()
    m.create_account(EMAIL, PASSWORD)
    assert m.verification["delivery"] == "unknown"


def test_registration_is_never_sent_twice(env):
    make, _, _ = env
    FakeApi.answers["/auth/register"] = ServerUnavailable("timed out", kind="timeout")
    m = make()
    with pytest.raises(ServerUnavailable):
        m.create_account(EMAIL, PASSWORD)
    assert [c for c in FakeApi.calls if c[1] == "/auth/register"] == [("POST", "/auth/register", {
        "email": EMAIL, "password": PASSWORD, "display_name": "", "language": "en"})]
    assert not m.account


def test_verified_state_comes_from_the_server(env):
    make, _, _ = env
    FakeApi.answers["/auth/register"] = registered("sent")
    m = make()
    m.create_account(EMAIL, PASSWORD)
    seen = []
    m.listeners.append(seen.append)
    FakeApi.answers["/auth/me"] = registered()["user"]
    assert m.refresh_account()["email_verified"] is False and not seen
    m.state["account"]["email_verified"] = True             # a local flag alone ...
    FakeApi.answers["/auth/me"] = registered()["user"]
    assert m.refresh_account()["email_verified"] is False, "... is replaced by what the server says"
    FakeApi.answers["/auth/me"] = registered(verified=True)["user"]
    assert m.refresh_account()["email_verified"] is True
    assert seen and seen[-1].email_verified is True
    assert not m.verification, "nothing left to confirm"


def test_resend_verification(env):
    make, _, _ = env
    FakeApi.answers["/auth/register"] = registered("failed")
    m = make()
    m.create_account(EMAIL, PASSWORD)
    FakeApi.answers["/auth/verify-email/resend"] = {"status": "sent", "email": EMAIL}
    before = time.time()
    m.resend_verification()
    assert m.verification["delivery"] == "sent" and m.verification["sent_at"] >= before
    FakeApi.answers["/auth/verify-email/resend"] = ApiError(429, "rate_limited", "x", {"retry_after": 1800})
    with pytest.raises(ApiError) as err:
        m.resend_verification()
    assert err.value.details["retry_after"] == 1800
    FakeApi.answers["/auth/verify-email/resend"] = ServerUnavailable("x", kind="server", code="email_delivery_failed")
    with pytest.raises(ServerUnavailable) as down:
        m.resend_verification()
    assert down.value.code == "email_delivery_failed"
    FakeApi.answers["/auth/verify-email/resend"] = {"status": "already_verified", "email": EMAIL}
    m.resend_verification()
    assert m.account["email_verified"] is True and not m.verification


def test_change_email_waits_for_the_new_link(env):
    make, _, _ = env
    FakeApi.answers["/auth/register"] = registered("sent")
    m = make()
    m.create_account("typo@exmaple.test", PASSWORD)
    FakeApi.answers["/auth/change-email"] = {"status": "sent", "email": EMAIL}
    m.change_email(PASSWORD, f" {EMAIL} ")
    assert ("POST", "/auth/change-email", {"password": PASSWORD, "new_email": EMAIL}) in FakeApi.calls
    assert m.verification["email"] == EMAIL and m.verification["change"]
    FakeApi.answers["/auth/change-email"] = ApiError(400, "wrong_password", "x")
    with pytest.raises(ApiError):
        m.change_email("nope", EMAIL)


# -- the session ---------------------------------------------------------------------------------------------------
def test_change_password_and_sign_out_elsewhere(env):
    make, _, _ = env
    m = make()
    m.sign_in(EMAIL, PASSWORD)
    FakeApi.answers["/auth/change-password"] = {"status": "password_changed"}
    m.change_password(PASSWORD, "a brand new pass")
    assert ("POST", "/auth/change-password", {"current_password": PASSWORD,
                                              "new_password": "a brand new pass"}) in FakeApi.calls
    assert m.state.get("refresh_token"), "this PC stays signed in"
    FakeApi.answers["/auth/sessions/revoke-others"] = {"revoked": 2}
    assert m.revoke_other_sessions() == 2


def test_expired_session_asks_to_sign_in_again_but_keeps_the_license(env):
    make, validate, _ = env
    m = make()
    m.sign_in(EMAIL, PASSWORD)
    m._access = None
    FakeApi.answers["/auth/refresh"] = ApiError(401, "invalid_refresh_token", "Please sign in again.")
    with pytest.raises(ApiError):
        m.refresh_account()
    view = m.view()
    assert view.needs_sign_in and view.session_ended == "invalid_refresh_token"
    assert view.unlocked, "the signed lease keeps working until it ends"
    assert "refresh_token" not in m.store.load()
    FakeApi.answers["/auth/login"] = registered(verified=True)
    m.sign_in(EMAIL, PASSWORD)
    assert not m.view().needs_sign_in and m.view().session_ended is None


def test_a_session_ended_elsewhere(env):
    make, _, _ = env
    m = make()
    m.sign_in(EMAIL, PASSWORD)
    m._access = ("stale", time.monotonic() + 600)
    FakeApi.answers["/auth/me"] = ApiError(401, "session_revoked", "ended")
    FakeApi.answers["/auth/refresh"] = ApiError(401, "invalid_refresh_token", "ended")
    with pytest.raises(ApiError):
        m.refresh_account()
    assert m.view().session_ended == "invalid_refresh_token" and m.view().needs_sign_in


def test_disabled_account_locks(env):
    make, _, _ = env
    m = make()
    m.sign_in(EMAIL, PASSWORD)
    FakeApi.answers["/auth/me"] = ApiError(403, "account_disabled", "disabled")
    with pytest.raises(ApiError):
        m.refresh_account()
    assert m.view().status is Status.DISABLED and not m.view().unlocked


def test_logout_forgets_the_account_but_not_the_choice(env):
    make, _, _ = env
    m = make()
    m.sign_in(EMAIL, PASSWORD, remember=False)
    m.sign_out()
    assert ("POST", "/auth/logout", {}) in FakeApi.calls
    assert not m.account and not m.state.get("refresh_token") and m.view().status is Status.NOT_LOGGED_IN
    assert m.remember is False, "the checkbox keeps its last state"
    assert make().view().status is Status.NOT_LOGGED_IN


def test_keep_me_signed_in(env):
    make, _, _ = env
    m = make()
    m.sign_in(EMAIL, PASSWORD, remember=True)
    assert make().state.get("refresh_token") == "r"
    m.sign_out()
    m.sign_in(EMAIL, PASSWORD, remember=False)
    assert m.state.get("refresh_token") == "r", "in memory for this run"
    assert "refresh_token" not in m.store.load(), "never written to disk"
    restart = make()
    view = restart.view()
    assert view.needs_sign_in and view.email == "player@example.test" and view.session_ended == "not_remembered"
    assert not restart.due(), "nothing to check online without a sign-in"


def test_server_info_is_cached_and_says_whether_verification_is_required(env):
    make, _, _ = env
    m = make()
    FakeApi.answers["/system/version"] = {"require_email_verification": True}
    assert m.server_info()["require_email_verification"] and m.verification_required()
    FakeApi.answers["/system/version"] = {"require_email_verification": False}
    assert m.server_info()["require_email_verification"], "cached"
    assert not m.server_info(fresh=True)["require_email_verification"]


def test_view_never_waits_for_a_request_in_the_background(env):
    make, _, _ = env
    m = make()
    m.sign_in(EMAIL, PASSWORD)
    first = m.view()
    gate = threading.Event()
    release = threading.Event()

    def slow(body):
        gate.set()
        release.wait(5)
        return {"status": "sent", "email": EMAIL}
    FakeApi.answers["/auth/verify-email/resend"] = slow
    worker = threading.Thread(target=m.resend_verification)
    worker.start()
    assert gate.wait(5)
    started = time.monotonic()
    assert m.view().status is first.status and not m.due()
    assert time.monotonic() - started < 2, "the UI thread is not blocked by the network"
    release.set()
    worker.join(5)


# -- nothing secret on disk or in the log ---------------------------------------------------------------------------
def test_no_password_or_access_token_is_stored(env, tmp_path):
    make, _, _ = env
    FakeApi.answers["/auth/login"] = registered(verified=True)
    m = make()
    m.sign_in(EMAIL, "my secret password")
    raw = (tmp_path / "license" / "state.bin").read_bytes()
    assert b"my secret password" not in raw and b"access-secret-1" not in raw
    assert set(json.loads(raw)) >= {"account", "refresh_token"}, "(a 0600 JSON file off Windows, DPAPI on it)"


def test_state_is_encrypted_with_dpapi_on_windows(tmp_path, monkeypatch):
    """The Windows code path (DPAPI) without Windows: the bytes written are the protected ones, never the JSON."""
    from akeno.licensing import storage
    calls = []

    def fake_dpapi(data: bytes, protect: bool) -> bytes:
        calls.append(protect)
        return bytes(b ^ 0x5A for b in data)
    monkeypatch.setattr(storage, "_dpapi", fake_dpapi)
    store = SecureStore(tmp_path)
    store.protected = True
    store.save({"refresh_token": "refresh-secret-1"})
    assert b"refresh-secret-1" not in store.path.read_bytes() and b"refresh_token" not in store.path.read_bytes()
    assert store.load() == {"refresh_token": "refresh-secret-1"} and calls == [True, False]


def test_state_file_is_private_elsewhere(env, tmp_path):
    import os
    import sys
    make, _, _ = env
    make().sign_in(EMAIL, PASSWORD)
    if sys.platform != "win32":
        assert os.stat(tmp_path / "license" / "state.bin").st_mode & 0o077 == 0


def test_no_credentials_in_the_log(env, caplog):
    make, validate, _ = env
    FakeApi.answers["/auth/register"] = registered("sent")
    FakeApi.answers["/auth/refresh"] = registered()
    FakeApi.answers["/auth/change-password"] = {"status": "password_changed"}
    FakeApi.answers["/auth/password-reset/request"] = ApiError(429, "rate_limited", "slow down", {"retry_after": 5})
    FakeApi.answers["/auth/me"] = ApiError(401, "invalid_refresh_token", "Please sign in again.")
    with caplog.at_level(logging.DEBUG):
        m = make()
        m.create_account(EMAIL, "my secret password")
        m.change_password("my secret password", "another secret pw")
        with pytest.raises(ApiError):
            m.request_password_reset(EMAIL)
        m._access = None
        FakeApi.answers["/auth/refresh"] = ApiError(401, "invalid_refresh_token", "x")
        with pytest.raises(ApiError):
            m.refresh_account()
        m.check_if_due()
    text = caplog.text + "".join(str(r.args) for r in caplog.records)
    for secret in ("my secret password", "another secret pw", "access-secret-1", "refresh-secret-1"):
        assert secret not in text


# -- the HTTP client against a real local server --------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    script: list = []
    seen: list = []

    def log_message(self, *a):
        pass

    def _answer(self):
        _Handler.seen.append((self.command, self.path, dict(self.headers)))
        step = _Handler.script.pop(0) if _Handler.script else (200, {}, {})
        if step == "drop":
            self.connection.close()
            return
        if step == "slow":
            time.sleep(1.5)
            step = (200, {}, {"late": True})
        status, headers, body = step
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = _answer


@pytest.fixture
def local():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Handler.script, _Handler.seen = [], []
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _err(code, message="m", **details):
    return {"error": {"code": code, "message": message, **({"details": details} if details else {})}}


def test_failures_are_told_apart(local):
    api = LicenseApi(local, timeout=1)
    _Handler.script = [(503, {}, _err("email_unavailable")), (503, {}, _err("maintenance")),
                       (500, {}, _err("internal_error")), (502, {}, b"<html>Bad gateway</html>"),
                       (404, {}, b"not json"), (429, {"Retry-After": "120"}, _err("rate_limited")),
                       (401, {}, _err("invalid_credentials")), "slow"]
    with pytest.raises(ServerUnavailable) as e:
        api.call("POST", "/auth/password-reset/request", {"email": EMAIL})
    assert e.value.code == "email_unavailable" and e.value.kind == "server"
    with pytest.raises(ServerUnavailable) as e:
        api.call("POST", "/x", {})
    assert e.value.kind == "maintenance" and e.value.maintenance
    with pytest.raises(ServerUnavailable) as e:
        api.call("POST", "/x", {})
    assert e.value.kind == "server"
    with pytest.raises(ServerUnavailable) as e:
        api.call("POST", "/x", {})
    assert e.value.kind == "server" and e.value.code == ""
    with pytest.raises(ServerUnavailable) as e:
        api.call("POST", "/x", {})
    assert e.value.kind == "network", "an answer that is not the AKENO server's (a proxy, a captive portal)"
    with pytest.raises(ApiError) as a:
        api.call("POST", "/x", {})
    assert a.value.code == "rate_limited" and a.value.details["retry_after"] == 120
    with pytest.raises(ApiError) as a:
        api.call("POST", "/auth/login", {})
    assert a.value.auth_failure and a.value.code == "invalid_credentials"
    with pytest.raises(ServerUnavailable) as e:
        api.call("POST", "/x", {})
    assert e.value.kind == "timeout"


def test_reads_are_repeated_once_writes_never(local, monkeypatch):
    from akeno.licensing import api as apimod
    monkeypatch.setattr(apimod, "RETRY_DELAY", 0.01)
    api = LicenseApi(local, timeout=2)
    _Handler.script = ["drop", (200, {}, {"ok": 1})]
    assert api.call("GET", "/system/version") == {"ok": 1}
    assert len(_Handler.seen) == 2
    _Handler.seen.clear()
    _Handler.script = ["drop", (200, {}, {"ok": 1})]
    with pytest.raises(ServerUnavailable):
        api.call("POST", "/auth/register", {"email": EMAIL})
    assert len(_Handler.seen) == 1, "a registration is never sent twice"


def test_requests_identify_the_app_version_and_carry_the_token_only_when_given(local):
    from akeno import __version__
    api = LicenseApi(local)
    _Handler.script = [(200, {}, {}), (200, {}, {})]
    api.call("GET", "/system/version")
    api.call("GET", "/auth/me", token="tok")
    first, second = _Handler.seen
    assert first[2]["User-Agent"].startswith(f"AKENO/{__version__} (") and "Authorization" not in first[2]
    assert second[2]["Authorization"] == "Bearer tok"
    assert first[1] == "/api/v1/system/version"


def test_unreachable_server(monkeypatch):
    from akeno.licensing import api as apimod
    monkeypatch.setattr(apimod, "RETRY_DELAY", 0.01)
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(ServerUnavailable) as e:
        LicenseApi(f"http://127.0.0.1:{port}", timeout=2).call("GET", "/system/version")
    assert e.value.kind == "offline"


# -- the official addresses ------------------------------------------------------------------------------------------
def test_official_api_and_website(monkeypatch):
    from akeno.licensing import config as cfgmod
    monkeypatch.delenv("AKENO_LICENSE_SERVER", raising=False)
    monkeypatch.delenv("AKENO_WEBSITE", raising=False)
    cfg = cfgmod.load()
    assert cfg.server_url == cfgmod.OFFICIAL_SERVER == "https://api.akeno.online"
    assert cfg.website == cfgmod.OFFICIAL_WEBSITE == "https://akeno.online"
    assert cfg.page("/forgot-password") == "https://akeno.online/forgot-password"
    assert cfgmod.website_for("https://api.akeno.online") == "https://akeno.online"
    assert cfgmod.website_for("https://licenses.example") == "https://licenses.example"     # one host for both
    assert cfgmod.website_for("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    monkeypatch.setenv("AKENO_LICENSE_SERVER", "http://127.0.0.1:8000")
    dev = cfgmod.load()
    assert dev.server_url == "http://127.0.0.1:8000" and dev.website == "http://127.0.0.1:8000"


def test_an_older_server_without_the_new_functions(env):
    from akeno.ui.account_ui import error_text
    make, _, _ = env
    m = make()
    m.sign_in(EMAIL, PASSWORD)
    FakeApi.answers["/auth/sessions/revoke-others"] = ApiError(404, "not_found", "Not Found")
    with pytest.raises(ApiError) as err:
        m.revoke_other_sessions()
    assert error_text(err.value).startswith("The AKENO server does not offer this yet")
    assert not m.view().needs_sign_in, "a missing function is not a lost sign-in"
