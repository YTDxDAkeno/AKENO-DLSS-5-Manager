"""Module licenses on the app side without a server: the access documents, what each license unlocks, offline
expiry of single modules, forged or foreign documents, and the main window with locked modules."""

from __future__ import annotations

import time

import pytest

from akeno.licensing import Status, device
from akeno.licensing import clock as clockmod
from akeno.licensing import entitlements as ents
from tests.test_licensing_client import FakeApi, Later, Signer, env  # noqa: F401

ACCESS = {"iss": "akeno-licensing", "aud": "akeno-desktop", "ent": "akeno-access"}
DAY = 86400


def access_answer(signer, grants, result="ok", cert=None, now=None):
    """The server's access answer to one validation request (signed for the request's own nonce)."""
    now = now or time.time()

    def answer(body):
        nonce = body.get("nonce")
        assert body.get("access") == 1, "the app asks for module licenses"
        bound = dict(ACCESS, sub="u1", dev="dev-1", dfp=device.fingerprint())
        out = {"result": result, "server_time": now, "license": {"status": "none"}, "lease": None,
               "offline_certificate": None, "certificate_revoked": False, "validation_interval_seconds": 3600}
        if result == "ok":
            out["lease"] = signer.sign("akeno-access-lease", dict(bound, grants=grants, iat=int(now), nbf=int(now),
                                                                  exp=int(now + 72 * 3600), nonce=nonce))
        if cert:
            out["offline_certificate"] = signer.sign("akeno-access-offline", dict(
                bound, ents=cert, grants=[g for g in grants if g["exp"] is None], iat=int(now), nbf=int(now),
                serial="s9"))
        out["validation"] = signer.sign("akeno-access-validation", dict(
            bound, nonce=nonce, res=result, crev=False, grants=grants if result == "ok" else [], iat=int(now),
            exp=int(now + 600)))
        return out
    return answer


def grant(ent, exp=None, lt=None):
    return {"ent": ent, "lt": lt or ("lifetime" if exp is None else "timed"), "exp": exp, "act": int(time.time())}


def signed_in(make, signer, grants, cert=None):
    FakeApi.answers["/licenses/validate"] = access_answer(signer, grants, cert=cert)
    m = make()
    m.sign_in("player@example.test", "pw")
    return m


def test_vpn_lifetime_unlocks_the_vpn_and_nothing_else(env):
    make, _, signer = env
    m = signed_in(make, signer, [grant(ents.VPN)], cert=[ents.VPN])
    view = m.view()
    assert view.unlocked and view.entitlements == [ents.VPN] and not view.full
    assert view.allows("vpn") and not view.allows("lan_play") and not view.allows("dlss")
    assert [k for k in ("home", "library", "fivem", "doctor", "wallpapers", "community", "sync")
            if view.page_allowed(k)] == []
    assert all(view.page_allowed(k) for k in ("akeno_network", "account", "license", "updates", "settings"))
    FakeApi.down = True
    offline = make().view()                                     # next start, no internet: the certificate counts
    assert offline.status is Status.LIFETIME_OFFLINE and offline.allows("vpn") and not offline.allows("dlss")


def test_lan_and_vpn_combine_and_full_unlocks_every_page(env):
    make, _, signer = env
    view = signed_in(make, signer, [grant(ents.VPN), grant(ents.LAN_PLAY, time.time() + 30 * DAY)]).view()
    assert view.allows("vpn") and view.allows("lan_play") and not view.allows("desktop")
    view = signed_in(make, signer, [grant(ents.SUITE)], cert=[ents.SUITE]).view()
    assert view.full and all(view.page_allowed(k) for k in ("home", "fivem", "wallpapers", "akeno_network"))


def test_full_for_30_days_ends_offline_and_the_vpn_stays(env, monkeypatch):
    make, _, signer = env
    now = time.time()
    signed_in(make, signer, [grant(ents.SUITE, now + 2 * DAY, "monthly"), grant(ents.VPN)], cert=[ents.VPN])
    FakeApi.down = True
    view = make().view()
    assert view.full and view.status is Status.EXPIRING
    monkeypatch.setattr(clockmod, "time", Later(3 * DAY))       # the Full month ended while the PC was offline
    view = make().view()
    assert view.unlocked and view.entitlements == [ents.VPN] and not view.allows("dlss")


def test_a_timed_module_ends_with_its_own_expiry_inside_the_lease(env, monkeypatch):
    make, _, signer = env
    signed_in(make, signer, [grant(ents.LAN_PLAY, time.time() + 3600)])
    FakeApi.down = True
    assert make().view().allows("lan_play")
    monkeypatch.setattr(clockmod, "time", Later(2 * 3600))      # the lease still runs; LAN Play does not
    view = make().view()
    assert not view.unlocked and not view.allows("lan_play")


def test_forged_foreign_or_unknown_grants_unlock_nothing(env):
    make, _, signer = env
    stranger = Signer()
    FakeApi.answers["/licenses/validate"] = access_answer(stranger, [grant(ents.SUITE)], cert=[ents.SUITE])
    m = make()
    with pytest.raises(Exception):
        m.sign_in("player@example.test", "pw")                  # another key: the answer is refused
    assert not make().view().unlocked
    m = signed_in(make, signer, [grant("akeno-everything"), grant(ents.VPN)])
    assert m.view().entitlements == [ents.VPN]                   # an id this build does not know is ignored
    # a certificate whose list of entitlements does not match its grants is not accepted
    m.state["access_certificate"] = signer.sign("akeno-access-offline", dict(
        ACCESS, sub="u1", dev="dev-1", dfp=device.fingerprint(), ents=[ents.VPN], grants=[grant(ents.SUITE)],
        iat=int(time.time()), nbf=int(time.time()), serial="x"))
    m.state.pop("access_lease")
    assert not m.view().unlocked
    # a suite-family document cannot pose as an access document and the other way round
    m.state["access_certificate"] = signer.sign("akeno-offline-license", dict(
        ACCESS, dev="dev-1", dfp=device.fingerprint(), ents=[ents.SUITE], grants=[grant(ents.SUITE)], lt="lifetime"))
    assert not m.view().unlocked


def test_a_certificate_for_another_pc_is_ignored(env, monkeypatch):
    make, _, signer = env
    signed_in(make, signer, [grant(ents.VPN)], cert=[ents.VPN])
    monkeypatch.setenv("AKENO_DEVICE_ID", "another-machine")
    FakeApi.down = True
    assert not make().view().unlocked


def test_an_older_server_is_asked_the_original_way(env):
    make, validate, signer = env
    from akeno.licensing.api import ApiError
    original = validate("monthly")

    def old_server(body):
        if "access" in body:
            raise ApiError(422, "validation_error", "Some fields are invalid.")
        return original(body)
    FakeApi.answers["/licenses/validate"] = old_server
    m = make()
    view = m.sign_in("player@example.test", "pw")
    assert m._legacy_server and view.full and view.entitlements == [ents.SUITE]


# -- the main window -------------------------------------------------------------------------------------------------
def _window(qtbot, tmp_path, m):
    from akeno.core.db import Database
    from akeno.services import AppServices
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    win = MainWindow(Controller(AppServices(db=Database(tmp_path / "db.sqlite3"))), m)
    qtbot.addWidget(win)
    return win


def test_main_window_with_a_vpn_license_builds_only_the_vpn(env, qtbot, tmp_path):
    from akeno.ui.locked import LockedModulePage
    from akeno.ui.pages.akeno_network import AkenoNetworkPage
    make, _, signer = env
    m = signed_in(make, signer, [grant(ents.VPN)], cert=[ents.VPN])
    win = _window(qtbot, tmp_path, m)
    assert win.current_page() == "akeno_network"
    assert isinstance(win.pages["akeno_network"], AkenoNetworkPage)
    for key in ("home", "library", "fivem", "downloads", "booster", "doctor", "security", "wallpapers", "sync"):
        assert isinstance(win.pages[key], LockedModulePage), key
    for key in ("dashboard", "profiles", "install"):
        assert key not in win.pages
    assert {"home", "fivem", "wallpapers"} <= set(win.locked_pages) and "license" not in win.locked_pages
    assert win.sidebar.buttons["home"].locked and not win.sidebar.buttons["akeno_network"].locked
    assert not win.allows("dlss") and win.allows("vpn")
    page = win.pages["fivem"]
    page.auto_check()                                          # calls on a locked page do nothing
    win.go("fivem")
    page.redeem_button.click()
    assert win.current_page() == "license"
    network = win.pages["akeno_network"]
    assert network.lan_locked.isVisibleTo(network.tab_pages[0]) and network.tab_bar.currentIndex() == 1


def test_a_full_code_rebuilds_the_window_with_every_page(env, qtbot, tmp_path):
    from akeno.ui.locked import LockedModulePage
    make, _, signer = env
    m = signed_in(make, signer, [grant(ents.VPN)], cert=[ents.VPN])
    win = _window(qtbot, tmp_path, m)
    win.go("license")
    FakeApi.answers["/licenses/validate"] = access_answer(signer, [grant(ents.SUITE), grant(ents.VPN)],
                                                          cert=[ents.SUITE, ents.VPN])
    view = m.validate()
    assert win.apply_license(view) is True
    qtbot.waitUntil(lambda: win.current_page() == "license", timeout=3000)
    assert not any(isinstance(p, LockedModulePage) for p in win.pages.values())
    assert "dashboard" in win.pages and win.locked_pages == []
    assert win.apply_license(view) is False                    # nothing changed: no rebuild
    FakeApi.answers["/licenses/validate"] = access_answer(signer, [grant(ents.VPN)], cert=[ents.VPN])
    assert win.apply_license(m.validate()) is True             # AKENO Full ended: back to the VPN only
    assert isinstance(win.pages["home"], LockedModulePage)
