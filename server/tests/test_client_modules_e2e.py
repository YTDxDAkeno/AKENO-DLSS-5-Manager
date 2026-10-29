"""End to end: module licenses with the Windows app's licensing code (``akeno/licensing``) against a real server.

A user gets an AKENO VPN Lifetime code, signs up, redeems it and uses AKENO VPN only; later a second code adds
AKENO Full. Also: AKENO Full for 30 days on top of VPN Lifetime falls back to VPN only, the VPN works offline,
an AKENO 3.1.1 PC keeps its lifetime license through the update, a server without module licenses, revocation
and local tampering.
"""

from __future__ import annotations

import base64
import json

import pytest

from tests.test_client_e2e import DownApi, client, home, server  # noqa: F401  (adds the app to sys.path)
from akeno.licensing import LicenseManager, Status  # noqa: E402
from akeno.licensing.api import ApiError, LicenseApi  # noqa: E402
from akeno_server import clock as server_clock  # noqa: E402
from akeno_server import db as dbmod  # noqa: E402
from akeno_server import settings  # noqa: E402
from akeno_server.admin import service as admin  # noqa: E402
from akeno_server.auth import service as auth  # noqa: E402
from akeno_server.models import Role, User  # noqa: E402
from tests.helpers import PASSWORD, make_codes  # noqa: E402

VPN, LAN, SUITE = "akeno-vpn", "akeno-lan-play", "akeno-suite"
DAY = 86400


@pytest.fixture(autouse=True)
def vpn_licenses_on():
    with dbmod.transaction() as db:
        settings.put(db, "vpn_module_licenses", True)


def vpn_lifetime_code() -> str:
    return make_codes("lifetime", entitlement=VPN)[0]


class OldServerApi(LicenseApi):
    """A license server from before module licenses: it refuses the ``access`` field like the real one did."""

    def call(self, method, path, body=None, token=None):
        if isinstance(body, dict) and "access" in body:
            raise ApiError(422, "validation_error", "Some fields are invalid.")
        return super().call(method, path, body, token)


def signed_up(url, email="vpn@example.test", api=LicenseApi) -> LicenseManager:
    m = client(url, api=api)
    m.create_account(email, PASSWORD, "Player", "de")
    return m


def test_vpn_lifetime_code_unlocks_akeno_vpn_only(server, home):
    m = signed_up(server)
    assert m.view().status is Status.NOT_ACTIVATED and not m.view().unlocked
    view = m.redeem(vpn_lifetime_code())
    assert view.unlocked and view.entitlements == [VPN] and not view.full
    assert view.allows("vpn") and not view.allows("lan_play") and not view.allows("dlss")
    assert not view.allows("desktop") and not view.allows("gaming") and not view.allows("*")
    assert view.page_allowed("akeno_network") and view.page_allowed("license") and view.page_allowed("updates")
    assert not view.page_allowed("home") and not view.page_allowed("fivem") and not view.page_allowed("wallpapers")
    assert view.grants[VPN]["exp"] is None and view.offline_eligible
    assert m.state.get("access_certificate") and not m.state.get("certificate")
    # the next start without internet: the signed certificate keeps AKENO VPN working, for good
    offline = client(server, api=DownApi)
    view = offline.view()
    assert view.status is Status.LIFETIME_OFFLINE and view.entitlements == [VPN] and view.allows("vpn")


def test_a_later_full_code_unlocks_everything_and_vpn_stays(server, home):
    m = signed_up(server)
    m.redeem(vpn_lifetime_code())
    view = m.redeem(make_codes("lifetime")[0])
    assert view.full and set(view.entitlements) == {SUITE, VPN}
    assert view.allows("dlss") and view.allows("lan_play") and view.allows("desktop") and view.page_allowed("home")


def test_full_for_30_days_then_back_to_vpn_only(server, home):
    m = signed_up(server)
    m.redeem(vpn_lifetime_code())
    view = m.redeem(make_codes("monthly")[0])
    assert view.full and view.status in (Status.ACTIVE_ONLINE, Status.EXPIRING)
    server_clock.advance(31 * DAY)
    m.clock.state.clear()                      # the PC's clock moves on with the server's (same as a real month)
    m._access = None
    m.sign_in("vpn@example.test", PASSWORD)
    view = m.validate()
    assert view.unlocked and view.entitlements == [VPN] and not view.full
    assert view.allows("vpn") and not view.allows("dlss") and not view.page_allowed("home")


def test_an_akeno_3_1_1_lifetime_pc_keeps_working_through_the_update(server, home):
    old = signed_up(server, "veteran@example.test", api=OldServerApi)      # asks the original way
    view = old.redeem(make_codes("lifetime")[0])
    assert view.full and old.state.get("certificate") and not old.state.get("access_certificate")
    updated = client(server, api=DownApi)                                  # AKENO 3.2.0, offline at its first start
    view = updated.view()
    assert view.status is Status.LIFETIME_OFFLINE and view.full and view.allows("dlss")
    online = client(server)
    view = online.validate()                                               # online: access documents replace them
    assert view.full and online.state.get("access_certificate") and not online.state.get("certificate")


def test_a_server_without_module_licenses_still_works_suite_only(server, home):
    m = signed_up(server, "legacy@example.test", api=OldServerApi)
    view = m.redeem(make_codes("monthly")[0])
    assert m._legacy_server and view.full and m.state.get("lease") and not m.state.get("access_lease")
    with pytest.raises(ApiError) as exc:                                    # a module code needs the new server
        m.redeem(vpn_lifetime_code())
    assert exc.value.code == "update_required"


def test_revoking_the_vpn_locks_it_and_removes_the_certificate(server, home):
    m = signed_up(server)
    m.redeem(vpn_lifetime_code())
    with dbmod.transaction() as db:
        boss = auth.create_user(db, "boss@example.test", PASSWORD, role=Role.ADMIN, verified=True)
        target = auth.user_by_email(db, "vpn@example.test")
        admin.grant_status(db, boss, target, VPN, "revoked", "refund")
    view = m.validate()
    assert not view.unlocked and not view.allows("vpn")
    assert not m.state.get("access_certificate") and not m.state.get("access_lease")


def _forge(token: str, change) -> str:
    head, body, sig = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    change(payload)
    new = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{head}.{new}.{sig}"


def test_local_tampering_never_unlocks_a_module(server, home):
    m = signed_up(server)
    m.redeem(make_codes("monthly", entitlement=LAN)[0])
    assert m.view().entitlements == [LAN]
    # edit the saved lease to list AKENO Full: the signature no longer matches, nothing is unlocked by it
    m.state["access_lease"] = _forge(m.state["access_lease"], lambda p: p["grants"].append(
        {"ent": SUITE, "lt": "lifetime", "exp": None, "act": 0}))
    assert not m.view().allows("dlss") and not m.view().allows("vpn")
    # the unsigned display copy and a hand-written "license" say AKENO Full: they unlock nothing
    m.state["access"] = {"grants": {SUITE: {"lt": "lifetime", "exp": None}}}
    m.state["license"] = {"status": "active", "license_type": "lifetime"}
    assert not m.view().unlocked
    # a VPN-only certificate of another account is not this PC's either
    other = signed_up(server, "other@example.test")
    other.redeem(vpn_lifetime_code())
    with dbmod.session() as db:
        assert db.query(User).count() == 3


def test_the_admin_api_issues_five_vpn_lifetime_codes_that_each_work_once(server, home):
    with dbmod.transaction() as db:
        boss = auth.create_user(db, "boss@example.test", PASSWORD, role=Role.ADMIN, verified=True)
        _, items = admin.generate_codes(db, boss, "lifetime", 5, entitlement=VPN, note="for friends")
        codes = [plain for plain, _ in items]
    assert len(set(codes)) == 5
    first = signed_up(server, "friend1@example.test")
    assert first.redeem(codes[0]).entitlements == [VPN]
    second = signed_up(server, "friend2@example.test")
    with pytest.raises(ApiError) as exc:
        second.redeem(codes[0])
    assert exc.value.code == "code_used"
    assert second.redeem(codes[1]).entitlements == [VPN]
