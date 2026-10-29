"""Free VPN: choose a country, connect, see the verified exit. VPN Gate community servers over official OpenVPN.

Availability comes only from the official VPN Gate list (fetched on demand, cached without profiles). A country is
"available" while the list has a usable server there; the planned target countries stay visible with "Not available"
when the list has none. Nothing here invents a server, an address, a country or a ping.

"Connected" is shown only from the elevated session's own fresh status with the observed exit address, at least two
geolocation confirmations, verified DNS and blocked IPv6. A status that stops updating is shown as needing recovery.
Before the first connection the user must accept the privacy notice (VPN Gate keeps logs; volunteers operate the
servers; no anonymity promise).
"""
import json
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLineEdit, QVBoxLayout

from akeno.i18n import msg, t
from akeno.network.providers import COUNTRY_NAMES
from akeno.paths import sub
from akeno.ui import icons, theme
from akeno.ui.network_vpn import CountryTile, duration
from akeno.ui.network_widgets import Details, Fact, StatusOrb, heading
from akeno.ui.widgets import Card, FlowLayout, StatusPill, button, confirm, label

TARGETS = ('US', 'TR', 'DE', 'JP', 'NL', 'FR', 'CA', 'GB')
STALE_SECONDS = 30
STATES = {
    'disconnected': (msg('Disconnected'), 'unknown'), 'stopped': (msg('Disconnected'), 'unknown'),
    'starting': (msg('Starting…'), 'pending'), 'searching': (msg('Looking for a free server…'), 'pending'),
    'connecting': (msg('Connecting to a free server…'), 'pending'),
    'verifying': (msg('Checking IP address, country and leaks…'), 'pending'),
    'connected': (msg('Connected'), 'ok'), 'reconnecting': (msg('Reconnecting…'), 'pending'),
    'switching': (msg('Switching server…'), 'pending'), 'unavailable': (msg('Not available'), 'warn'),
    'failed': (msg('Failed'), 'fail'), 'recovery_required': (msg('Needs recovery'), 'warn'),
}
ACTIVE = ('starting', 'searching', 'connecting', 'verifying', 'connected', 'reconnecting', 'switching')
ERRORS = {
    'free_vpn_no_server': msg('Right now no free VPN server is available in {country}. AKENO never switches to '
                              'another country on its own. Try again later or choose another country.'),
    'free_vpn_all_failed': msg('None of the free servers in {country} accepted a connection. Community servers '
                               'come and go; try again later or choose another country.'),
    'free_vpn_catalog_unavailable': msg('The VPN Gate server list is not reachable right now. Check your Internet '
                                        'connection and try again.'),
    'free_vpn_component_missing': msg('Install the VPN component (official OpenVPN) first.'),
    'free_vpn_leak': msg('A leak check failed (IP address, IPv6 or DNS), so AKENO closed the connection for your '
                         'safety. Windows uses your normal connection again.'),
    'free_vpn_unverified': msg('AKENO could not verify the connection, so it closed it. Windows uses your normal '
                               'connection again.'),
    'free_vpn_connection_lost': msg('The free server went away and no other server in {country} answered. Windows '
                                    'uses your normal connection again.'),
    'free_vpn_cleanup_incomplete': msg('The VPN was not removed completely. Click Recover to restore your normal '
                                       'connection.'),
    'free_vpn_session_failed': msg('The free VPN stopped unexpectedly. Click Recover, then try again.'),
    'free_vpn_busy': msg('Disconnect LAN Play and other VPN connections first. Only one runs at a time.'),
}
PRIVACY = msg(
    'The free VPN uses VPN Gate, an academic experiment of the University of Tsukuba (Japan). Its servers are run by '
    'volunteers and by the university.\n\n'
    '• The server operator and VPN Gate can see your real public IP address.\n'
    '• VPN Gate keeps connection logs for three months or more, every volunteer server records packet headers of all '
    'users for at least two weeks, and these logs are disclosed to police, prosecutors, lawyers or courts that are '
    'authorised by law.\n'
    '• Servers can go offline at any time, speed varies a lot, and some countries are missing at times.\n'
    '• Unencrypted traffic (HTTP, plain DNS) can be read by the server operator. Use HTTPS.\n'
    '• This is not an audited no-logs VPN and AKENO cannot guarantee anonymity.\n'
    '• Use it lawfully: VPN Gate prohibits abuse and cooperates with authorities.\n\n'
    'AKENO installs the official OpenVPN component only after you agree, chooses a server automatically, checks '
    'the public IP address, its country (two independent sources), DNS and IPv6 before showing Connected, and keeps '
    'no browsing or traffic history.')
CONSENT_FILE = 'free-vpn.json'


def country_name(code, names=None):
    if not code:
        return ''
    if code in COUNTRY_NAMES:
        return t(COUNTRY_NAMES[code])
    return (names or {}).get(code) or code


class Preferences:
    """Accepted notice version and favourite countries; plain JSON (nothing secret)."""

    def __init__(self, path=None):
        self.path = path or sub('network') / CONSENT_FILE
        try:
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(self.data, dict):
                raise ValueError()
        except (OSError, ValueError):
            self.data = {}

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data), encoding='utf-8')
        except OSError:
            pass

    @property
    def consent(self):
        return self.data.get('consent')

    def accept(self, version):
        self.data['consent'] = version
        self._save()

    def favourites(self):
        return [c for c in self.data.get('favourites', []) if isinstance(c, str) and len(c) == 2][:50]

    def toggle(self, code):
        fav = self.favourites()
        fav.remove(code) if code in fav else fav.append(code)
        self.data['favourites'] = fav
        self._save()
        return code in fav


class FreeVPN(Card):
    def __init__(self, page, prefs=None):
        self.pill = StatusPill(t('Disconnected'), 'unknown')
        super().__init__(t('Free VPN'), 'globe', right=self.pill)
        from akeno.network.free_vpn import CONSENT_VERSION
        self.consent_version = CONSENT_VERSION
        self.page, self.prefs = page, prefs or Preferences()
        self.availability, self.names, self.selected, self.loaded = {}, {}, None, False
        self.component = None
        self.active = False
        self._connected_since = None
        hero = QHBoxLayout()
        hero.setSpacing(12)
        self.orb = StatusOrb()
        hero.addWidget(self.orb, 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.state_title = label(t('Disconnected'), 'display')
        self.state = label(t('Choose a country and click Connect. AKENO picks a free server automatically.'),
                           'muted', wrap=True)
        texts.addWidget(self.state_title)
        texts.addWidget(self.state)
        hero.addLayout(texts, 1)
        self.body.addLayout(hero)
        facts = QGridLayout()
        facts.setHorizontalSpacing(20)
        facts.setVerticalSpacing(10)
        self.fact_ip = Fact(t('Public VPN IP'), hint=t('The address websites see, observed through the tunnel.'))
        self.fact_country = Fact(t('Verified country'), hint=t('Confirmed by at least two independent '
                                                              'geolocation services through the tunnel.'))
        self.fact_server = Fact(t('Server'), hint=t('The VPN Gate server AKENO chose automatically.'))
        self.fact_latency = Fact(t('Ping'), hint=t('Round trip of a DNS query through the VPN, measured after '
                                                   'connecting. "—" means not measured.'))
        self.fact_time = Fact(t('Connected for'))
        self.fact_provider = Fact(t('Provider'))
        self.fact_provider.set(t('VPN Gate · community'))
        for index, fact in enumerate((self.fact_ip, self.fact_country, self.fact_server, self.fact_latency,
                                      self.fact_time, self.fact_provider)):
            facts.addWidget(fact, index // 3, index % 3)
        self.body.addLayout(facts)
        self.component_row = QHBoxLayout()
        self.component_text = label('', 'muted', wrap=True)
        self.install_button = button(t('Install VPN component…'), 'download', on_click=self.install)
        self.component_row.addWidget(self.component_text, 1)
        self.component_row.addWidget(self.install_button)
        self.body.addLayout(self.component_row)
        self.body.addWidget(heading(t('Choose a country'), t(
            'Countries come from the current VPN Gate server list. A country is available only while a suitable '
            'server is listed there; AKENO never switches to another country without asking.')))
        self.search = QLineEdit()
        self.search.setPlaceholderText(t('Search country'))
        self.search.addAction(icons.icon('search', theme.TEXT_MUTED, 16), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.render)
        self.body.addWidget(self.search)
        self.grid = FlowLayout(spacing=10)
        self.tiles = {}
        self.body.addLayout(self.grid)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.connect_button = button(t('Connect'), 'power', 'primary', on_click=self.connect)
        self.stop_button = button(t('Disconnect'), 'stop', on_click=self.disconnect)
        for b in (self.connect_button, self.stop_button):
            b.setMinimumHeight(44)
            b.setMinimumWidth(170)
        self.switch_button = button(t('Switch server'), 'refresh', 'ghost', on_click=self.switch)
        self.favourite_button = button(t('Favourite'), 'star', 'ghost', on_click=self.toggle_favourite)
        self.refresh_button = button(t('Refresh list'), 'refresh', 'ghost', on_click=self.refresh)
        self.recover_button = button(t('Recover'), 'restore', 'ghost', on_click=self.recover)
        self.privacy_button = button(t('Privacy'), 'shield', 'ghost', on_click=self.show_privacy)
        row.addWidget(self.connect_button)
        row.addWidget(self.stop_button)
        row.addStretch(1)
        more = FlowLayout(spacing=8)
        for b in (self.switch_button, self.favourite_button, self.refresh_button, self.recover_button,
                  self.privacy_button):
            more.addWidget(b)
        self.body.addLayout(row)
        self.body.addLayout(more)
        self.summary = label(t('The server list has not been loaded yet.'), 'muted', wrap=True)
        self.body.addWidget(self.summary)
        self.body.addWidget(Details(t('Privacy and how the free VPN works'), t(PRIVACY)))
        self.windows = sys.platform == 'win32'
        self.built = self.windows and bool(getattr(sys, 'frozen', False))
        self._show_cached()
        self.render()
        self._update_buttons()
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.poll)
        if self.built:
            self.timer.start()      # reads the local DPAPI status only

    # -- countries ----------------------------------------------------------------------------------------------------
    def provider(self):
        from akeno.network.vpngate import VPNGateProvider
        return VPNGateProvider(sub('network') / 'vpngate-cache.json')

    def _show_cached(self):
        cached = self.provider().cached()
        if cached:
            servers, fetched = cached
            self._apply(servers, fetched, from_cache=True)

    def _apply(self, servers, fetched, from_cache=False):
        from akeno.network.vpngate import VPNGateProvider
        self.availability = VPNGateProvider.availability(servers)
        self.names = {s.country: s.country_name for s in servers}
        self.loaded = not from_cache
        when = time.strftime('%H:%M', time.localtime(fetched))
        total = sum(v['servers'] for v in self.availability.values())
        self.summary.setText(t('{n} usable free servers in {c} countries (list from {time}).').format(
            n=total, c=len(self.availability), time=when) if total else
            t('The current list has no usable free server. Try again later.'))

    def order(self):
        favourites = self.prefs.favourites()
        codes = list(dict.fromkeys(list(TARGETS) + sorted(self.availability, key=lambda c: country_name(c, self.names))))
        return sorted(codes, key=lambda c: (c not in favourites, c not in self.availability, c not in TARGETS,
                                            country_name(c, self.names).lower()))

    def render(self):
        needle = self.search.text().strip().lower()
        favourites = self.prefs.favourites()
        for code in self.order():
            tile = self.tiles.get(code)
            if tile is None:
                tile = self.tiles[code] = CountryTile(code, self.select)
                self.grid.addWidget(tile)
            entry = self.availability.get(code)
            star = '★ ' if code in favourites else ''
            if entry:
                ping = t(' · ~{ms} ms listed').format(ms=entry['ping_ms']) if entry.get('ping_ms') is not None else ''
                tile.set_status(star + t('Available · {n} server(s)').format(n=entry['servers']) + ping, True)
            else:
                tile.set_status(star + t('Not available'), False)
            visible = not needle or needle in country_name(code, self.names).lower() or needle in code.lower()
            tile.setVisible(visible)
        if self.selected is None:
            first = next((c for c in self.order() if c in self.availability), None)
            if first:
                self.select(first)
        self._update_buttons()

    def select(self, code):
        self.selected = code if code in self.tiles else None
        for other, tile in self.tiles.items():
            tile.setChecked(other == self.selected)
        if self.selected and self.selected not in self.availability and not self.active:
            self.state.setText(t(ERRORS['free_vpn_no_server']).format(country=country_name(self.selected, self.names)))
        self._update_buttons()

    def refresh(self):
        if not self.page.modules.enabled('vpn'):
            return

        def load():
            provider = self.provider()
            servers = provider.servers(refresh=True)
            return servers, time.time()

        def show(result):
            self._apply(*result)
            self.render()
        self.page.work(load, show)
        self.check_component()

    def check_component(self):
        if not self.windows:
            self.component_text.setText(t('The free VPN runs on Windows.'))
            self.install_button.setVisible(False)
            return
        from akeno.network import openvpn_windows

        def show(result):
            self.component = result
            ready = result.get('state') == 'ready'
            self.component_text.setText(t('VPN component ready (official OpenVPN {v}).').format(
                v=result.get('version') or '') if ready else t(
                'The free VPN needs the official OpenVPN component and an AKENO network adapter. AKENO installs them '
                'only after you agree (one Windows permission prompt); the download is checked against the reviewed '
                'official installer and its OpenVPN Inc. signature.'))
            self.install_button.setVisible(not ready)
            self._update_buttons()
        self.page.work(openvpn_windows.status, show)

    def toggle_favourite(self):
        if self.selected:
            self.prefs.toggle(self.selected)
            self.render()

    # -- actions ------------------------------------------------------------------------------------------------------
    def show_privacy(self):
        from akeno.ui.widgets import info
        info(self, t('Free VPN: privacy'), t(PRIVACY))

    def accepted(self):
        if self.prefs.consent == self.consent_version:
            return True
        if confirm(self, t('Free VPN: please read'), t(PRIVACY), ok_text=t('I understand and agree')):
            self.prefs.accept(self.consent_version)
            return True
        return False

    def install(self):
        if not self.built or not self.accepted():
            return
        if not confirm(self, t('Install VPN component'), t(
                'AKENO downloads the official OpenVPN {v} installer from openvpn.org, checks that it is exactly the '
                'reviewed file and signed by OpenVPN Inc., and installs only the program and its network driver '
                '(no extra apps or start-up services). Then it creates the "AKENO-FreeVPN" network adapter. Windows '
                'asks for permission once.').format(v=_component_version())):
            return
        from akeno.network.helper import invoke
        self.page.work(lambda: invoke('install_vpn_component', consent=True), lambda _: self.check_component())

    def connect(self):
        code = self.selected
        if not self.built or not code or code not in self.availability or self.active:
            return
        if self.component and self.component.get('state') != 'ready':
            self.state.setText(t(ERRORS['free_vpn_component_missing']))
            return
        if not self.accepted():
            return
        from akeno.network.helper import invoke
        self.state.setText(t('Connecting to {country}…').format(country=country_name(code, self.names)))
        self.page.work(lambda: invoke('free_vpn_session', code, consent=self.consent_version), lambda _: self.poll())

    def _request(self, **values):
        if not self.built:
            return
        from akeno.network.helper import free_vpn_request
        try:
            free_vpn_request(**values)
        except ValueError as exc:
            from akeno.network import messages
            self.page.say(t(messages.text(exc)), 'warn')

    def disconnect(self):
        self._request(stop=True)
        self.state.setText(t('Disconnecting…'))

    def switch(self):
        self._request(switch=True)
        self.state.setText(t('Switching to another server in the same country…'))

    def recover(self):
        if not self.built:
            return
        from akeno.network.helper import invoke
        self.page.work(lambda: invoke('free_vpn_recover'), lambda _: self.poll())

    # -- status -------------------------------------------------------------------------------------------------------
    def poll(self):
        if not self.built:
            return
        from akeno.network.helper import free_vpn_status
        try:
            result = free_vpn_status()
        except Exception:
            result = {'state': 'recovery_required', 'error': 'free_vpn_cleanup_incomplete'}
        self.show_status(result)

    def show_status(self, result, now=None):
        now = time.time() if now is None else now
        state = result.get('state') or 'disconnected'
        if state in ACTIVE and now - float(result.get('updated') or 0) > STALE_SECONDS:
            state, result = 'recovery_required', {'state': 'recovery_required', 'error': 'free_vpn_cleanup_incomplete'}
        connected = (state == 'connected' and bool(result.get('exit_ipv4')) and result.get('dns_verified') is True
                     and result.get('ipv6') == 'blocked' and len(result.get('confirmed_by') or []) >= 2)
        if state == 'connected' and not connected:
            state = 'verifying'
        text, colour = STATES.get(state, STATES['starting'])
        self.pill.set(t(text), colour)
        self.orb.set(colour)
        self.state_title.setText(t(text))
        country = country_name(result.get('country'), self.names)
        error = result.get('error')
        if connected:
            self.state.setText(t('Your internet traffic leaves through a free server in {country}.').format(
                country=country))
        elif error in ERRORS:
            self.state.setText(t(ERRORS[error]).format(country=country or t('this country')))
        elif state in ACTIVE:
            self.state.setText(t(text))
        else:
            self.state.setText(t('Your internet traffic uses your normal connection.'))
        if connected:
            since = result.get('connected_since')
            if self._connected_since is None:
                self._connected_since = float(since) if since else now
            self.fact_ip.set(str(result['exit_ipv4']))
            self.fact_country.set(t('{country} ({n} sources)').format(country=country,
                                                                      n=len(result.get('confirmed_by') or [])))
            host = str(result.get('server_host') or '—')
            proto = str(result.get('server_proto') or '').upper()
            self.fact_server.set(f'{host} · {proto}' if proto else host)
            latency = result.get('latency_ms')
            self.fact_latency.set(t('{n} ms').format(n=round(float(latency))) if isinstance(latency, (int, float))
                                  else '—')
            self.fact_time.set(duration(now - self._connected_since))
        else:
            self._connected_since = None
            for fact in (self.fact_ip, self.fact_country, self.fact_server, self.fact_latency, self.fact_time):
                fact.set('—')
        self.active = state in ACTIVE
        self.recovery = state == 'recovery_required'
        follow = getattr(self.page, 'show_free_vpn', None)
        if follow:
            follow(t(text), colour, country or '—', str(result.get('exit_ipv4') or '—') if connected else '—',
                   self.active or self.recovery)
        self._update_buttons()

    def _update_buttons(self):
        ready = self.component is None or self.component.get('state') == 'ready'
        self.connect_button.setEnabled(self.built and not self.active and ready and bool(self.selected)
                                       and self.selected in self.availability)
        self.stop_button.setEnabled(self.built and self.active)
        self.switch_button.setEnabled(self.built and self.active)
        self.recover_button.setEnabled(self.built)
        self.refresh_button.setEnabled(self.windows)
        self.favourite_button.setEnabled(bool(self.selected))
        self.install_button.setEnabled(self.built)


def _component_version():
    from akeno.network.openvpn_windows import VERSION
    return VERSION
