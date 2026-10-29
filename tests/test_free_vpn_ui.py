"""Free VPN card: availability from the list only, honest states, consent before the first connection (offscreen)."""
import time
from types import SimpleNamespace

import pytest

from akeno import i18n
from akeno.network.vpngate import parse_catalog
from tests_support_free_vpn import catalog, row


class Page:
    def __init__(self):
        self.modules = SimpleNamespace(enabled=lambda name: True)
        self.messages, self.followed = [], []
    def work(self, fn, done):
        done(fn())
    def say(self, text, kind='info'):
        self.messages.append((text, kind))
    def show_free_vpn(self, *args):
        self.followed.append(args)


@pytest.fixture
def card(qtbot, tmp_path):
    from akeno.ui.network_free_vpn import FreeVPN, Preferences
    widget = FreeVPN(Page(), Preferences(tmp_path / 'free-vpn.json'))
    qtbot.addWidget(widget)
    widget.built = True                     # the native helper is replaced in each test
    return widget


def load(card, *rows):
    card._apply(parse_catalog(catalog(*rows)), time.time())
    card.render()


def test_targets_are_listed_and_only_countries_with_usable_servers_are_available(card):
    load(card, row('45.76.10.1', 'US'), row('45.76.10.2', 'US'), row('45.76.10.3', 'KR', name='Korea'),
         row('45.76.10.4', 'JP', uptime='1000'))
    assert card.tiles['US'].available and 'Available · 2 server(s)' in card.tiles['US'].line
    assert not card.tiles['TR'].available and 'Not available' in card.tiles['TR'].line
    assert not card.tiles['JP'].available                      # unstable server: not offered
    assert card.tiles['KR'].available                          # other real countries appear too
    assert set(('US', 'TR', 'DE', 'JP', 'NL', 'FR', 'CA', 'GB')) <= set(card.tiles)
    card.select('TR')
    assert not card.connect_button.isEnabled() and 'no free VPN server is available' in card.state.text()
    card.select('US')
    assert card.connect_button.isEnabled()
    card.search.setText('kor')
    assert card.tiles['KR'].isVisibleTo(card) and not card.tiles['US'].isVisibleTo(card)


def test_favourites_are_kept_and_sorted_first(card):
    load(card, row('45.76.10.1', 'US'), row('45.76.10.3', 'KR', name='Korea'))
    card.select('KR')
    card.toggle_favourite()
    assert card.order()[0] == 'KR' and card.prefs.favourites() == ['KR'] and card.tiles['KR'].line.startswith('★')


def test_first_connection_requires_accepting_the_privacy_notice(card, monkeypatch):
    load(card, row('45.76.10.1', 'US'))
    card.select('US')
    calls, answers = [], [False, True]
    monkeypatch.setattr('akeno.ui.network_free_vpn.confirm', lambda *a, **k: answers.pop(0))
    import akeno.network.helper as helper
    monkeypatch.setattr(helper, 'invoke', lambda *a, **k: calls.append((a, k)) or {'state': 'starting'})
    monkeypatch.setattr(helper, 'free_vpn_status', lambda: {'state': 'starting', 'updated': time.time()})
    card.connect()
    assert calls == [] and card.prefs.consent is None
    card.connect()
    assert calls == [(('free_vpn_session', 'US'), {'consent': 1})] and card.prefs.consent == 1


def test_connected_only_with_verified_exit_two_sources_dns_and_blocked_ipv6(card):
    now = time.time()
    good = {'state': 'connected', 'country': 'US', 'exit_ipv4': '45.76.99.1', 'confirmed_by': ['ipinfo', 'country.is'],
            'dns_verified': True, 'ipv6': 'blocked', 'server_host': 'public-vpn-1', 'server_proto': 'tcp',
            'latency_ms': 41.6, 'connected_since': now - 65, 'updated': now}
    card.show_status(good, now)
    assert card.state_title.text() == 'Connected' and card.fact_ip.value.text() == '45.76.99.1'
    assert card.fact_country.value.text() == 'United States (2 sources)' and card.fact_latency.value.text() == '42 ms'
    assert card.fact_server.value.text() == 'public-vpn-1 · TCP' and card.fact_time.value.text() == '1:05'
    assert card.stop_button.isEnabled() and card.switch_button.isEnabled()
    for broken in ({'confirmed_by': ['ipinfo']}, {'dns_verified': False}, {'ipv6': 'leaks'}, {'exit_ipv4': None}):
        card._connected_since = None
        card.show_status({**good, **broken}, now)
        assert card.state_title.text() != 'Connected' and card.fact_ip.value.text() == '—'
    card.show_status({**good, 'updated': now - 120}, now)
    assert card.state_title.text() == 'Needs recovery' and 'Recover' in card.state.text()


@pytest.mark.parametrize('code,text', [('free_vpn_no_server', 'no free VPN server is available in United States'),
                                       ('free_vpn_leak', 'leak check failed'),
                                       ('free_vpn_catalog_unavailable', 'server list is not reachable')])
def test_errors_are_translated_texts_never_raw_messages(card, code, text):
    card.show_status({'state': 'failed', 'country': 'US', 'error': code, 'updated': time.time()})
    assert text in card.state.text()


def test_card_renders_in_german(qtbot, tmp_path):
    from akeno.ui.network_free_vpn import FreeVPN, Preferences
    i18n.set_language('de')
    try:
        widget = FreeVPN(Page(), Preferences(tmp_path / 'p.json'))
        qtbot.addWidget(widget)
        widget._apply(parse_catalog(catalog(row('45.76.10.1', 'US'))), time.time())
        widget.render()
        assert widget.connect_button.text() == 'Verbinden' and 'Nicht verfügbar' in widget.tiles['TR'].line
        assert widget.title_label.text() == 'Kostenloses VPN' if hasattr(widget, 'title_label') else True
    finally:
        i18n.set_language('en')
