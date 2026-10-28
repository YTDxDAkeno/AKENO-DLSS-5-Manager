"""AKENO VPN: choose a country, connect, see the verified exit. The one-click path of the VPN tab.

Countries come from two places only: the shipped target list (shown so a player sees which countries are planned and
why they cannot be chosen yet) and the AKENO control plane's catalog of currently admitted, healthy exits. A country
is connectable only while the catalog lists an exit for it; nothing here invents a server, an IP or a latency.

Connected is shown only from the elevated session's own status: a fresh status record whose state is ``connected``
and which carries the observed IPv4/IPv6 exit after routing and DNS were verified. A status that stops updating (the
helper disappeared) is shown as needing recovery, never as connected. Errors are fixed codes translated here; raw
server or Windows texts never reach the window.
"""
import sys
import time

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from akeno.i18n import msg, t
from akeno.licensing.storage import SecureStore
from akeno.network.providers import COUNTRY_NAMES
from akeno.network.vpn import VPNClient
from akeno.paths import sub
from akeno.ui import theme
from akeno.ui.network_widgets import Details, Fact, StatusOrb, flag_pixmap, heading
from akeno.ui.widgets import Card, FlowLayout, StatusPill, button, confirm, label

TARGETS = tuple(COUNTRY_NAMES)         # TR, US, DE, NL, GB, FR, JP, CA: planned, shown even without an exit
STALE_SECONDS = 30                     # a running session rewrites its status at least every few seconds

STATES = {
    'disconnected': (msg('Disconnected'), 'unknown'), 'stopped': (msg('Disconnected'), 'unknown'),
    'starting': (msg('Starting…'), 'pending'), 'provisioning': (msg('Requesting VPN permission…'), 'pending'),
    'awaiting_exit': (msg('Waiting for the exit server…'), 'pending'),
    'connecting': (msg('Starting the tunnel…'), 'pending'),
    'awaiting_handshake': (msg('Waiting for the server'), 'pending'),
    'verifying': (msg('Verifying exit and routing…'), 'pending'), 'connected': (msg('Connected'), 'ok'),
    'reconnecting': (msg('Reconnecting…'), 'pending'), 'failed': (msg('Failed'), 'fail'),
    'verification_required': (msg('Not connected: verification incomplete'), 'warn'),
    'recovery_required': (msg('Needs recovery'), 'warn'),
}
ACTIVE = ('starting', 'provisioning', 'awaiting_exit', 'connecting', 'awaiting_handshake', 'verifying', 'connected',
          'reconnecting')
ERRORS = {
    'vpn_network_lost': msg('The connection to the VPN server was lost. AKENO reconnects with a fresh permission and '
                            'checks everything again before showing Connected.'),
    'vpn_network_changed': msg('Your network changed. AKENO checks the VPN again before showing Connected.'),
    'vpn_reconnect_exhausted': msg('Reconnecting failed three times, so AKENO disconnected. Windows uses your normal '
                                   'connection again. Try again later or choose another country.'),
    'vpn_verification_incomplete': msg('AKENO could not prove that all traffic and DNS lookups use the VPN, so it '
                                       'disconnected. Windows uses your normal connection again.'),
    'vpn_session_failed': msg('The VPN could not be started. Run the diagnostics and try again.'),
    'vpn_cleanup_incomplete': msg('The VPN was not removed completely. Click Recover to restore your normal '
                                  'connection.'),
}
UNAVAILABLE = msg('No verified AKENO exit server in this country yet.')
NOT_LOADED = msg('Availability not checked yet.')


def country_label(code):
    return t(COUNTRY_NAMES.get(code, code)) if code else ''


def duration(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    return f'{hours}:{rest // 60:02d}:{rest % 60:02d}' if hours else f'{rest // 60}:{rest % 60:02d}'


class CountryTile(QPushButton):
    """One country: flag, name and why it can or cannot be chosen. Checkable; a group keeps one selected."""

    def __init__(self, code, on_select):
        super().__init__()
        self.code = code
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIcon(QIcon(flag_pixmap(code, 30, 20)))
        self.setIconSize(QSize(30, 20))
        self.setMinimumSize(196, 58)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.clicked.connect(lambda: on_select(code))
        self.available = False
        self.set_status(t(NOT_LOADED), False)

    def set_status(self, line, available):
        self.available = available
        self.line = line
        self.setText(f'{country_label(self.code)}\n{line}')
        self.setAccessibleName(country_label(self.code))
        self.setAccessibleDescription(line)
        self.setToolTip(line)
        border = theme.ACCENT if available else theme.BORDER
        colour = theme.TEXT if available else theme.TEXT_MUTED
        self.setStyleSheet(
            f'QPushButton {{ text-align: left; padding: 8px 12px; border: 1px solid {theme.BORDER};'
            f' border-radius: {theme.RADIUS_CARD}px; background: {theme.SURFACE_2}; color: {colour}; }}'
            f' QPushButton:hover {{ border-color: {theme.BORDER_BTN}; }}'
            f' QPushButton:checked {{ border: 2px solid {border}; background: {theme.accent().soft(0.14)}; }}')


class AutomaticVPN(Card):
    def __init__(self, page):
        self.pill = StatusPill(t('Disconnected'), 'unknown')
        super().__init__(t('AKENO VPN'), 'globe', right=self.pill)
        self.page, self.servers, self.selected, self.loaded = page, [], None, False
        self._connected_since = None
        hero = QHBoxLayout()
        hero.setSpacing(12)
        self.orb = StatusOrb()
        hero.addWidget(self.orb, 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.state_title = label(t('Disconnected'), 'display')
        self.state = label(t('Your internet traffic uses your normal connection.'), 'muted', wrap=True)
        texts.addWidget(self.state_title)
        texts.addWidget(self.state)
        hero.addLayout(texts, 1)
        self.body.addLayout(hero)
        facts = QGridLayout()
        facts.setHorizontalSpacing(20)
        facts.setVerticalSpacing(10)
        self.fact_ip = Fact(t('Public VPN IP'), hint=t('The IPv4 address websites see, observed through the tunnel '
                                                       'after connecting.'))
        self.fact_ipv6 = Fact(t('IPv6'))
        self.fact_country = Fact(t('Verified country'), hint=t('The exit server\'s country was verified '
                                                              'independently; the observed IP must match it.'))
        self.fact_time = Fact(t('Connected for'))
        self.fact_latency = Fact(t('Latency'), hint=t('Shown only when AKENO measured it. "—" means not measured.'))
        self.fact_protection = Fact(t('Leak protection'), hint=t('Routes, IPv4, IPv6 and DNS were checked natively '
                                                                 'before Connected was shown.'))
        for index, fact in enumerate((self.fact_ip, self.fact_ipv6, self.fact_country, self.fact_time,
                                      self.fact_latency, self.fact_protection)):
            facts.addWidget(fact, index // 3, index % 3)
        self.body.addLayout(facts)
        self.body.addWidget(heading(t('Choose a country'), t(
            'A country can be chosen only while AKENO runs an authorised, healthy exit server there whose location '
            'was verified independently. Keys and the tunnel are created automatically for this PC; you never '
            'import a profile.')))
        self.grid = FlowLayout(spacing=10)
        self.tiles = {}
        for code in sorted(TARGETS, key=country_label):
            self._tile(code)
        self.body.addLayout(self.grid)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.connect_button = button(t('Connect'), 'power', 'primary', on_click=self.connect)
        self.stop_button = button(t('Disconnect'), 'stop', on_click=self.disconnect)
        for b in (self.connect_button, self.stop_button):
            b.setMinimumHeight(44)
            b.setMinimumWidth(170)
        self.refresh_button = button(t('Refresh countries'), 'refresh', 'ghost', on_click=self.refresh)
        self.recover_button = button(t('Recover'), 'restore', 'ghost', on_click=self.recover)
        self.diagnose_button = button(t('Diagnostics'), 'stethoscope', 'ghost', on_click=self.diagnose)
        row.addWidget(self.connect_button)
        row.addWidget(self.stop_button)
        row.addStretch(1)
        for b in (self.refresh_button, self.recover_button, self.diagnose_button):
            row.addWidget(b)
        self.body.addLayout(row)
        self.summary = label(t('No AKENO exits have been loaded yet.'), 'muted', wrap=True)
        self.body.addWidget(self.summary)
        self.body.addWidget(Details(t('What the VPN protects'), t(
            'Websites and games see the VPN server\'s address instead of your home IP. AKENO connects only after you '
            'click Connect and Windows asks for permission, verifies the observed IPv4/IPv6 exit, routes and DNS, and '
            'disconnects instead of showing Connected when a check fails. LAN Play and the VPN cannot run at the same '
            'time yet. A VPN does not make you anonymous: the VPN operator and the sites you sign in to still know '
            'who you are.')))
        self.built = sys.platform == 'win32' and bool(getattr(sys, 'frozen', False))
        self.stop_button.setEnabled(self.built)
        self.recover_button.setEnabled(self.built)
        self.refresh_button.setEnabled(sys.platform == 'win32')
        self.connect_button.setEnabled(False)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        if self.built:
            self.timer.start()   # reads the local DPAPI status only; never connects or contacts an API

    # -- countries ----------------------------------------------------------------------------------------------------
    def _tile(self, code):
        tile = CountryTile(code, self.select)
        self.tiles[code] = tile
        self.grid.addWidget(tile)
        return tile

    def select(self, code):
        self.selected = code if code in self.tiles else None
        for other, tile in self.tiles.items():
            tile.setChecked(other == self.selected)
        self._update_buttons()
        if self.selected and not self.tiles[self.selected].available:
            self.summary.setText(f'{country_label(self.selected)}: {self.tiles[self.selected].line}')

    def available(self, code):
        return any(server.get('country') == code for server in self.servers)

    def client(self):
        return VPNClient(self.page.window.license, SecureStore(sub('network') / 'vpn-identity'))

    def refresh(self):
        if not self.page.modules.enabled('vpn'):
            return
        if not getattr(self.page.window, 'license', None):
            self.page.say(t('Sign in before using AKENO Network.'), 'warn')
            return

        def show(result):
            self.servers = list(result['servers'])
            self.loaded = True
            for code in result['countries']:
                if code not in self.tiles:
                    self._tile(code)
            for code, tile in self.tiles.items():
                count = sum(1 for server in self.servers if server.get('country') == code)
                tile.set_status(t('Available · {n} server(s)').format(n=count) if count else t(UNAVAILABLE),
                                bool(count))
            if self.selected is None or not self.available(self.selected):
                first = next((code for code in sorted(self.tiles, key=country_label) if self.available(code)), None)
                self.select(first)
            self.summary.setText(t('{n} authorised healthy exits.').format(n=len(self.servers)) if self.servers else
                                 t('No authorised healthy country exits are available right now. Countries are '
                                   'enabled as soon as AKENO runs a verified server there.'))
            self._update_buttons()
        self.page.work(lambda: self.client().catalog(), show)

    # -- actions ------------------------------------------------------------------------------------------------------
    def connect(self):
        code = self.selected
        if not self.built or not code or not self.available(code):
            return
        if not confirm(self, t('Connect VPN'), t(
                'Connect through {country}? Windows asks for permission once. AKENO verifies the handshake, the '
                'observed IPv4/IPv6 exit, routes and DNS before showing Connected.').format(country=country_label(code))):
            return
        from akeno.network.helper import invoke
        self.page.work(lambda: invoke('vpn_session', code), lambda _: self.poll())

    def disconnect(self):
        if not self.built:
            return
        from akeno.network.helper import stop_vpn
        try:
            stop_vpn()
        except ValueError as exc:
            from akeno.network import messages
            self.page.say(t(messages.text(exc)), 'warn')
            return
        self.state.setText(t('Disconnecting…'))

    def recover(self):
        if not self.built:
            return
        from akeno.network.helper import invoke
        self.page.work(lambda: invoke('vpn_recover'), lambda _: self.poll())

    def diagnose(self):
        show_tab = getattr(self.page, 'show_tab', None)
        if show_tab:
            show_tab(2)
        run = getattr(self.page, 'diagnose', None)
        if run:
            run()

    # -- status -------------------------------------------------------------------------------------------------------
    def poll(self):
        if not self.built:
            return
        from akeno.network.helper import vpn_status
        try:
            result = vpn_status()
        except Exception:
            result = {'state': 'recovery_required'}
        self.show_status(result)

    def show_status(self, result, now=None):
        now = time.time() if now is None else now
        state = result.get('state') or 'disconnected'
        if state in ACTIVE and now - float(result.get('updated') or 0) > STALE_SECONDS:
            state, result = 'recovery_required', {'state': 'recovery_required', 'error': 'vpn_cleanup_incomplete'}
        connected = state == 'connected' and bool(result.get('exit_ipv4')) and bool(result.get('dns_verified')) \
            and bool(result.get('routing_verified'))
        if state == 'connected' and not connected:
            state = 'verifying'
        text, colour = STATES.get(state, STATES['starting'])
        self.pill.set(t(text), colour)
        self.orb.set(colour)
        self.state_title.setText(t(text))
        country = country_label(result.get('country'))
        if connected:
            self.state.setText(t('Your internet traffic leaves through {country}.').format(country=country))
        elif result.get('error') in ERRORS:
            self.state.setText(t(ERRORS[result['error']]))
        elif state in ACTIVE:
            self.state.setText(t('Connecting to {country}…').format(country=country) if country else t(text))
        else:
            self.state.setText(t('Your internet traffic uses your normal connection.'))
        if connected:
            since = result.get('connected_since')
            if self._connected_since is None:
                self._connected_since = float(since) if since else now
            self.fact_ip.set(str(result['exit_ipv4']))
            self.fact_ipv6.set(str(result.get('exit_ipv6') or '—'))
            self.fact_country.set(country or '—')
            self.fact_time.set(duration(now - self._connected_since))
            latency = result.get('latency_ms')
            self.fact_latency.set(t('{n} ms').format(n=round(float(latency))) if isinstance(latency, (int, float))
                                  else '—')
            self.fact_protection.set(t('Verified'))
        else:
            self._connected_since = None
            for fact in (self.fact_ip, self.fact_ipv6, self.fact_country, self.fact_time, self.fact_latency,
                         self.fact_protection):
                fact.set('—')
        self.active = state in ACTIVE or state == 'recovery_required'
        if self.active or getattr(self, '_dashboard', False):
            follow = getattr(self.page, 'show_managed_vpn', None)
            if follow:
                follow(t(text), colour, country or '—', str(result.get('exit_ipv4') or '—'))
        self._dashboard = self.active
        self._update_buttons()

    def _update_buttons(self):
        busy = getattr(self, 'active', False)
        self.connect_button.setEnabled(self.built and not busy and bool(self.selected) and
                                       self.available(self.selected))
