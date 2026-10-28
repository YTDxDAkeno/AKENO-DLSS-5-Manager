"""AKENO Network page: states and honesty of what it shows (fixture data, no network, no tunnel).

Optionally saves screenshots (AKENO_NETWORK_SCREENSHOTS=<dir>); those are labelled fixture images, not evidence of
a real connection.
"""
import os
import time
from types import SimpleNamespace

import pytest

from akeno import i18n
from akeno.network.vpngate import Server

ROOM = 'ab' * 16
ME, FRIEND, RELAYED = 'K' * 43 + '=', 'L' * 43 + '=', 'M' * 43 + '='


class Ctl:
    """Runs network work synchronously, like the real controller would on completion."""
    busy = {}

    def run(self, key, fn, done=None, failed=None, **kw):
        try:
            value = fn()
        except Exception as exc:              # noqa: BLE001
            if failed:
                failed(exc)
            return True
        if done:
            done(value)
        return True


@pytest.fixture
def page(qtbot):
    from akeno.ui.pages.akeno_network import AkenoNetworkPage
    p = AkenoNetworkPage(Ctl(), SimpleNamespace(license=None))
    qtbot.addWidget(p)
    p.resize(1500, 1200)
    p.show()
    return p


def in_session(page, monkeypatch):
    monkeypatch.setattr(page, 'current_room', lambda: ROOM)
    page.members_data = [
        {'actor': 'aaaaaaaa-0000', 'public_key': ME, 'virtual_ip': '10.203.5.2', 'status': 'registered', 'self': True},
        {'actor': 'bbbbbbbb-1111', 'public_key': FRIEND, 'virtual_ip': '10.203.5.3', 'status': 'registered'},
        {'actor': 'cccccccc-2222', 'public_key': RELAYED, 'virtual_ip': '10.203.5.4', 'status': 'registered'}]
    now = time.time()
    page.session = {'state': 'active', 'room': ROOM, 'virtual_ip': '10.203.5.2', 'peers': {
        FRIEND: {'mode': 'direct', 'latency_ms': 18.4, 'handshake': now - 12, 'rx': 2_400_000, 'tx': 1_900_000,
                 'virtual_ip': '10.203.5.3'},
        RELAYED: {'mode': 'relay', 'latency_ms': 41.0, 'handshake': now - 30, 'rx': 650_000, 'tx': 720_000,
                  'virtual_ip': '10.203.5.4'}}}
    page.show_room()


def cells(page, row):
    return [page.table.item(row, c).text() for c in range(page.table.columnCount())]


def test_without_a_room_only_create_and_join_are_offered(page):
    assert page.no_room.isVisible() and not page.in_room.isVisible()
    assert page.tile_members.value.text() == '—' and page.tile_ping.value.text() == '—'


def test_session_shows_each_members_real_path_ping_and_traffic(page, monkeypatch):
    in_session(page, monkeypatch)
    assert page.in_room.isVisible() and not page.no_room.isVisible()
    assert cells(page, 0)[:3] == ['You', '10.203.5.2', 'This PC']
    friend, relayed = cells(page, 1), cells(page, 2)
    assert friend[2] == 'Direct (P2P)' and friend[3] == '18 ms' and friend[4] == '12 s ago' and '2.3 MB' in friend[5]
    assert relayed[2] == 'Relay' and relayed[3] == '41 ms'
    assert page.tile_members.value.text() == '2' and page.tile_paths.value.text() == '1 / 1'
    assert page.tile_ping.value.text() == '30 ms' and page.tile_session.value.text() == 'Connected'
    _shot(page, 'akeno-network-session-en-fixture.png')


def test_without_a_running_session_no_path_is_claimed(page, monkeypatch):
    in_session(page, monkeypatch)
    page.session = {'state': 'stopped'}
    page.show_room()
    assert cells(page, 1)[2] == '—' and cells(page, 1)[3] == '—' and page.tile_members.value.text() == '—'


def test_locations_show_only_countries_the_catalog_really_lists(page, monkeypatch):
    def server(country, name, n):
        return Server(f'h{n}', f'45.0.0.{n}', country, name, 20, 1, 1, '2 weeks', 'client')
    servers = [server('US', 'United States', 1), server('US', 'United States', 2), server('JP', 'Japan', 3)]
    monkeypatch.setattr('akeno.network.vpngate.fetch_catalog', lambda: servers)
    monkeypatch.setattr('akeno.network.vpngate.reachable', lambda s: 'reachable' if s.host == 'h1' else 'unreachable')
    page.catalog()
    rows = [page.location_grid.grid.itemAtPosition(r, 1).widget().text() for r in range(2)]
    assert rows == ['2 servers in the catalog · 1 of 2 checked reachable from this PC', 'Not in the current catalog']
    assert [page.countries.itemData(i) for i in range(page.countries.count())] == ['JP', 'US']


def test_page_renders_in_german(qtbot, monkeypatch):
    i18n.set_language('de')
    try:
        from akeno.ui.pages.akeno_network import AkenoNetworkPage
        p = AkenoNetworkPage(Ctl(), SimpleNamespace(license=None))
        qtbot.addWidget(p)
        p.resize(1500, 1200)
        p.show()
        assert p.session_pill.text().endswith('Kein Raum')
        in_session(p, monkeypatch)
        assert cells(p, 1)[2] == 'Direkt (P2P)' and cells(p, 2)[2] == 'Relay' and cells(p, 0)[0] == 'Du'
        assert p.tile_session.value.text() == 'Verbunden'
        _shot(p, 'akeno-network-session-de-fixture.png')
    finally:
        i18n.set_language('en')


def test_diagnostics_off_windows_say_what_is_needed():
    from akeno.ui.pages import akeno_network
    if akeno_network.WINDOWS:
        pytest.skip('Windows runs the real checks')
    [(state, title, _)] = akeno_network.diagnostics(None)
    assert state == 'warn' and title == 'Windows required'


def _shot(widget, name):
    folder = os.environ.get('AKENO_NETWORK_SCREENSHOTS')
    if folder:
        os.makedirs(folder, exist_ok=True)
        widget.grab().save(os.path.join(folder, name))


# -- regression: the "Not Found" bottom-left (production server without the LAN API) ------------------------------------

def test_server_without_lan_api_never_shows_not_found(page):
    from akeno.licensing.api import ApiError
    from akeno.network.client import LanUnavailable

    def bare_404():
        raise ApiError(404, 'not_found', 'Not Found')
    for failure in (bare_404, lambda: (_ for _ in ()).throw(LanUnavailable())):
        page.work(failure, lambda _: None)
        shown = page.work_status.text()
        assert 'Not Found' not in shown and 'not available on the AKENO server' in shown


def test_controller_status_bar_translates_server_answers(qtbot):
    from akeno.licensing.api import ApiError
    from akeno.ui.controller import status_text
    assert 'Not Found' not in status_text(ApiError(404, 'not_found', 'Not Found'))
    assert status_text(RuntimeError('disk full')) == 'disk full'


def test_room_errors_become_sentences(page):
    from akeno.licensing.api import ApiError

    def denied():
        raise ApiError(403, 'room_access_denied', 'Invalid or expired invitation.')
    page.work(denied, lambda _: None)
    assert 'Ask the room owner for a new one' in page.work_status.text()
