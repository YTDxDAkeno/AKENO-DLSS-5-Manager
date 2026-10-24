"""End to end: the Windows app's licensing code (``akeno/licensing``) against a real server over HTTP.

Covers what the app must survive: sign-in, activation, a lifetime license used offline on the next start, a
server outage, a clock turned back, a disabled account, a removed PC, key rotation and the device limit.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

REPO = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO))          # the app package; after the server tests

from akeno.licensing import DeviceLimitReached, LicenseManager, Status  # noqa: E402
from akeno.licensing import config as client_config  # noqa: E402
from akeno.licensing.api import ApiError, LicenseApi, ServerUnavailable  # noqa: E402
from akeno.licensing.storage import SecureStore  # noqa: E402
from akeno_server import clock as server_clock  # noqa: E402
from akeno_server import db as dbmod  # noqa: E402
from akeno_server import settings  # noqa: E402
from akeno_server.admin import service as admin  # noqa: E402
from akeno_server.auth import service as auth  # noqa: E402
from akeno_server.licensing import signing  # noqa: E402
from tests.helpers import PASSWORD, make_codes, staff  # noqa: E402


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(app):
    port = _port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(5)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AKENO_HOME", str(tmp_path / "akeno-home"))
    monkeypatch.setenv("AKENO_DEVICE_ID", "e2e-machine-guid-1")
    monkeypatch.delenv("AKENO_LICENSE_SERVER", raising=False)
    return tmp_path / "akeno-home"


def embedded_keys() -> dict:
    with dbmod.session() as db:
        return {k.kid: k.public_key for k in signing.public_keys(db)}


def client(url: str, keys: dict | None = None, api=LicenseApi, store_dir=None) -> LicenseManager:
    cfg = client_config.LicensingConfig(url, embedded_keys() if keys is None else keys)
    return LicenseManager(cfg, SecureStore(store_dir), api)


class DownApi(LicenseApi):
    """The network is gone: every call fails like a dropped connection."""

    def call(self, *a, **k):
        raise ServerUnavailable("connection refused")


def test_not_logged_in_then_sign_up_and_redeem(server, home):
    m = client(server)
    assert m.view().status is Status.NOT_LOGGED_IN and not m.view().unlocked
    view = m.create_account("player@example.test", PASSWORD, "Player", "de")
    assert view.status is Status.NOT_ACTIVATED and view.email == "player@example.test"
    view = m.redeem(make_codes("monthly")[0])
    assert view.status is Status.ACTIVE_ONLINE and view.unlocked and view.license_type == "monthly"
    assert 29.9 * 86400 < view.remaining <= 30 * 86400 and view.device_id
    assert view.offline_until - time.time() == pytest.approx(72 * 3600, abs=120)


def test_saved_state_is_not_plain_text_on_windows_only_here_json(server, home):
    m = client(server)
    m.create_account("player@example.test", PASSWORD)
    raw = (home / "license" / "state.bin").read_bytes()
    assert b"refresh_token" in raw                       # Linux test run: a 0600 JSON file (DPAPI on Windows)
    assert (home / "license" / "state.bin").stat().st_mode & 0o077 == 0


def test_lifetime_works_offline_on_next_start(server, home):
    m = client(server)
    m.create_account("player@example.test", PASSWORD)
    m.redeem(make_codes("lifetime")[0])
    assert m.state.get("certificate")
    later = client(server, api=DownApi)                     # a new start without network, years later
    later.clock.state["high_water"] = time.time()
    view = later.view()
    assert view.status is Status.LIFETIME_OFFLINE and view.unlocked and view.offline_eligible
    with pytest.raises(ServerUnavailable):
        later.validate()
    assert later.view().status is Status.LIFETIME_OFFLINE      # an outage never locks a lifetime license
    assert later.check_if_due().unlocked


def test_lifetime_survives_a_clock_turned_back(server, home):
    m = client(server)
    m.create_account("player@example.test", PASSWORD)
    m.redeem(make_codes("lifetime")[0])
    m.state["clock"]["high_water"] = time.time() + 10 * 86400
    m._save()
    restart = client(server, api=DownApi)
    view = restart.view()
    assert view.clock_problem and view.status is Status.LIFETIME_OFFLINE


def test_time_license_cached_then_grace_ends(server, home):
    m = client(server)
    m.create_account("player@example.test", PASSWORD)
    m.redeem(make_codes("monthly")[0])
    offline = client(server, api=DownApi)
    assert offline.view().status is Status.ACTIVE_CACHED
    offline.clock.state.update(offset=73 * 3600)            # 73 h later, still no connection
    offline.clock._anchor = None
    view = offline.view()
    assert view.status is Status.SERVER_UNAVAILABLE and not view.unlocked
    assert offline.state.get("refresh_token")                # nothing was thrown away
    back = client(server)
    back.clock.state.update(offset=0, high_water=0)
    back.clock._anchor = None
    assert back.validate().status is Status.ACTIVE_ONLINE


def test_clock_turned_back_needs_an_online_check(server, home):
    m = client(server)
    m.create_account("player@example.test", PASSWORD)
    m.redeem(make_codes("trial_24h")[0])
    m.state["clock"]["high_water"] = time.time() + 2 * 86400     # the clock was seen two days later before
    m._save()
    restart = client(server, api=DownApi)
    view = restart.view()
    assert view.clock_problem and view.status is Status.SERVER_UNAVAILABLE
    online = client(server)
    assert online.validate().status in (Status.ACTIVE_ONLINE, Status.EXPIRING)


def test_expiry_is_reported_and_data_kept(server, home):
    m = client(server)
    m.create_account("player@example.test", PASSWORD)
    m.redeem(make_codes("trial_24h")[0])
    assert m.view().status is Status.ACTIVE_ONLINE              # 24 h left: not "ending soon" yet (6 h)
    server_clock.advance(19 * 3600)
    m.clock.state.update(offset=19 * 3600)
    m.clock._anchor = None
    assert m.view().status is Status.EXPIRING
    server_clock.advance(6 * 3600)
    m.clock.state.update(offset=25 * 3600)
    m.clock._anchor = None
    m._access = None
    view = m.validate()
    assert view.status is Status.EXPIRED and not view.unlocked and m.state.get("refresh_token")
    view = m.redeem(make_codes("monthly")[0])
    assert view.unlocked and view.license_type == "monthly"


def test_auth_failure_is_not_an_outage(server, home):
    m = client(server)
    m.create_account("player@example.test", PASSWORD)
    m.redeem(make_codes("monthly")[0])
    with dbmod.transaction() as db:
        auth.revoke_all(db, auth.user_by_email(db, "player@example.test").id, "test")
    m._access = None
    with pytest.raises(ApiError) as err:
        m.validate()
    assert err.value.status == 401
    view = m.view()
    assert view.needs_sign_in and view.unlocked                  # the signed lease still runs its course
    assert not m.due()                                           # no retry loop without a new sign-in
    assert m.sign_in("player@example.test", PASSWORD).status is Status.ACTIVE_ONLINE


def test_disabled_account_locks_and_drops_offline_license(server, home):
    m = client(server)
    m.create_account("player@example.test", PASSWORD)
    m.redeem(make_codes("lifetime")[0])
    staff("boss@example.test")
    with dbmod.transaction() as db:
        boss = auth.user_by_email(db, "boss@example.test")
        admin.patch_user(db, boss, auth.user_by_email(db, "player@example.test"), status="disabled")
    m._access = None
    with pytest.raises(ApiError):
        m.validate()
    view = m.view()
    assert view.status is Status.DISABLED and not view.unlocked and not m.state.get("certificate")


def test_admin_reset_devices_reregisters(server, home):
    m = client(server)
    m.create_account("player@example.test", PASSWORD)
    m.redeem(make_codes("lifetime")[0])
    first = m.state["device_id"]
    staff("boss@example.test")
    with dbmod.transaction() as db:
        admin.reset_devices(db, auth.user_by_email(db, "boss@example.test"),
                            auth.user_by_email(db, "player@example.test"))
    view = m.validate()
    assert view.unlocked and m.state["device_id"] == first and m.state.get("certificate")


def test_device_limit_and_removing_a_pc(server, home, monkeypatch, tmp_path):
    with dbmod.transaction() as db:
        settings.put(db, "default_device_limit", 1)
    first = client(server, store_dir=tmp_path / "pc1")
    first.create_account("player@example.test", PASSWORD)
    first.redeem(make_codes("monthly")[0])
    monkeypatch.setenv("AKENO_DEVICE_ID", "second-pc")
    second = client(server, store_dir=tmp_path / "pc2")
    with pytest.raises(DeviceLimitReached) as err:
        second.sign_in("player@example.test", PASSWORD)
    assert err.value.limit == 1 and len(err.value.devices) == 1
    second.remove_device(err.value.devices[0]["id"])
    view = second.retry_device()
    assert view.unlocked
    monkeypatch.setenv("AKENO_DEVICE_ID", "e2e-machine-guid-1")
    first._access = None
    with pytest.raises(DeviceLimitReached):
        first.validate()                                         # the first PC lost its slot


def test_rotated_key_is_learned_through_endorsement(server, home):
    m = client(server)
    m.create_account("player@example.test", PASSWORD)
    m.redeem(make_codes("lifetime")[0])
    with dbmod.transaction() as db:
        new = signing.store().create(db)
    assert new.kid not in m.trusted_keys()
    m._access = None
    assert m.validate().unlocked
    assert new.kid in m.trusted_keys() and m.state["keys"][-1]["via"] == "endorsed"


def test_foreign_server_key_is_refused(server, home):
    """A server signing with a key that is not endorsed by a trusted key cannot unlock the app."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from akeno_server.auth.tokens import b64e
    other = Ed25519PrivateKey.generate()
    raw = signing.raw_public(other.public_key())
    m = client(server, keys={signing.kid_for(raw): b64e(raw)})
    with pytest.raises(Exception):
        m.create_account("player@example.test", PASSWORD)
        m.redeem(make_codes("monthly")[0])
    assert not m.view().unlocked


def test_build_without_keys_pins_on_first_sign_in(server, home):
    m = client(server, keys={})
    m.create_account("player@example.test", PASSWORD)
    assert m.state["keys"][0]["via"] == "pinned"
    assert m.redeem(make_codes("monthly")[0]).unlocked


def test_sign_out_forgets_license_but_nothing_else(server, home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings-marker").write_text("keep me")
    m = client(server)
    m.create_account("player@example.test", PASSWORD)
    m.redeem(make_codes("lifetime")[0])
    view = m.sign_out()
    assert view.status is Status.NOT_LOGGED_IN and not m.state.get("certificate")
    assert (home / "settings-marker").read_text() == "keep me"


def test_maintenance_is_an_outage(server, home):
    m = client(server)
    m.create_account("player@example.test", PASSWORD)
    m.redeem(make_codes("monthly")[0])
    with dbmod.transaction() as db:
        settings.put(db, "maintenance_mode", True)
    with pytest.raises(ServerUnavailable) as err:
        m.validate()
    assert err.value.maintenance and m.view().unlocked


def test_wrong_password_and_codes_surface_server_codes(server, home):
    m = client(server)
    with pytest.raises(ApiError) as err:
        m.sign_in("ghost@example.test", PASSWORD)
    assert err.value.code == "invalid_credentials"
    m.create_account("player@example.test", PASSWORD)
    with pytest.raises(ApiError) as err:
        m.redeem("AKENO-AAAA-BBBB-CCCC-DDDD")
    assert err.value.code == "invalid_code"
