"""AKENO Network: LAN Play rooms and sessions, the VPN (own WireGuard profiles and the location list) and diagnostics.

Layout: a short header, one notice line, a dashboard with the two connections (LAN Play, VPN), then three tabs
(LAN Play, VPN, Diagnostics). Technical explanations sit behind (i) buttons and "How it works" sections so the page
stays readable for players without network knowledge.

Every action has a real function or says plainly why it is not available. Building the page touches no network;
the first time it is shown it asks the AKENO server once (read-only) whether LAN Play is offered. LAN sessions and
VPN connections start only on an explicit click and Windows' UAC prompt. Every figure shown (ping, path, exit IP)
comes from a real measurement; without one the page shows "—".
"""
import platform
import sys
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLineEdit, QListWidget, QListWidgetItem, QTabBar, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from akeno.i18n import msg, t
from akeno.licensing.storage import SecureStore
from akeno.network import messages, providers
from akeno.network.client import LanClient
from akeno.network.profiles import ProfileStore
from akeno.paths import sub
from akeno.ui import icons, theme
from akeno.ui.account_ui import Banner
from akeno.ui.network_widgets import (ConnectionCard, Details, InfoButton, Steps, avatar_pixmap, flag_pixmap, heading,
                                      quality, quality_pixmap)
from akeno.ui.widgets import (Card, CheckRow, Divider, EmptyState, FlowLayout, KeyValueGrid, PageHeader, ResponsiveRow,
                              StatusPill, SubCard, button, clear_layout, confirm, info, label)

WINDOWS = sys.platform == 'win32'
BUILT = WINDOWS and bool(getattr(sys, 'frozen', False))

SESSION = {None: (msg('Not connected'), 'unknown'), 'stopped': (msg('Not connected'), 'unknown'),
           'starting': (msg('Starting…'), 'pending'), 'active': (msg('Connected'), 'ok'), 'failed': (msg('Failed'), 'fail')}
PATH = {'direct': (msg('Direct (P2P)'), 'ok'), 'relay': (msg('Relay'), 'info'), 'direct_trying': (msg('Connecting directly…'), 'pending'),
        'relay_trying': (msg('Connecting via relay…'), 'pending'), None: ('—', 'unknown')}
TUNNEL = {'disconnected': (msg('Disconnected'), 'unknown'), 'starting': (msg('Starting…'), 'pending'),
          'awaiting_handshake': (msg('Waiting for the server'), 'pending'), 'connected': (msg('Connected'), 'ok'),
          'stopping': (msg('Disconnecting…'), 'pending'), 'failed': (msg('Failed'), 'fail'),
          'recovery_required': (msg('Needs recovery'), 'warn')}
STEPS = (msg('Create or join a room'), msg('Start the LAN session'), msg('Allow your game'), msg('Play together'))
TABS = (msg('LAN Play'), msg('VPN'), msg('Diagnostics'))

# Explanations behind (i) buttons and "How it works" (kept off the main surface on purpose).
INFO_LAN = msg('LAN Play connects the PCs in a room through an encrypted WireGuard network. Each member gets a '
               'virtual IP address (10.203.x.x) that the others can reach; nothing else on your PC is opened.')
INFO_PATH = msg('Direct: your PCs talk peer-to-peer. Relay: traffic passes through the AKENO coordinator when a '
                'direct path cannot be opened (strict routers); it stays end-to-end encrypted either way.')
INFO_VIP = msg('Give this address to friends whose game asks for a server IP. It only works inside this room.')
INFO_PING = msg('Round-trip time measured through the tunnel to each connected member. "—" means nothing has been '
                'measured yet.')
HOW_LAN = msg('Games that connect by IP address work across the room: the host shares their virtual IP and the '
              'others type it into the game. Automatic LAN server search (broadcast) is not forwarded yet, so games '
              'that only find servers that way may not list the host. Not every game has been tested. LAN Play '
              'creates its own adapter and Windows Firewall rules for the room only: file sharing, Remote Desktop '
              'and NetBIOS stay unreachable for members, and your normal internet traffic is not changed.')
INFO_VPN = msg('The VPN sends your internet traffic through the server you choose. It is separate from LAN Play: '
               'you can use either one alone, or both.')
INFO_SOURCES = msg('AKENO server: run by AKENO. Your own server: a WireGuard profile you imported. Third-party '
                   'provider: a VPN service AKENO has reviewed. Only locations with a real, reviewed server can be '
                   'connected.')
HOW_VPN = msg('Full-tunnel profiles need IPv4, IPv6 and a DNS server, so no traffic or DNS lookup leaves outside the '
              'tunnel. The exit IP is what one HTTPS check observed; it does not prove that every app uses the '
              'tunnel. AKENO never changes routes on its own: the VPN connects only when you click Connect and '
              'Windows asks for permission. There is no kill switch yet: if the tunnel drops, Windows uses your '
              'normal connection again.')
VPNGATE = msg('VPN Gate states that it keeps connection logs for at least three months, that each volunteer server '
              'records packet headers for at least two weeks, and that logs are disclosed to police, prosecutors '
              'and courts on request. Its servers use OpenVPN, which AKENO does not integrate. Connecting stays '
              'disabled; the catalog can be loaded read-only for review.')


def _bytes(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024


def _ago(stamp):
    if not stamp:
        return '—'
    seconds = max(0, int(time.time() - stamp))
    return t('{n} s ago').format(n=seconds) if seconds < 120 else t('{n} min ago').format(n=seconds // 60)


def country_name(code):
    if not code:
        return ''
    name = providers.COUNTRY_NAMES.get(code) or providers.catalog()['countries'].get(code) or code
    return t(name)


class AkenoNetworkPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.window = ctl, window
        self.members_data, self.session, self.catalog_servers = [], {}, []
        self.room_info, self.service, self.game_allowed, self._probed = {}, None, False, False
        self.vpn_started, self.profile_meta, self.location_items, self.diag_rows = None, [], {}, []
        self.prefs = providers.Preferences(sub('network') / 'vpn-preferences.json')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        lay.addWidget(PageHeader(t('AKENO Network'), t('Play LAN games with friends and protect your connection with '
                                                       'a VPN. Nothing connects until you start it.')))
        self.notice = Banner()
        self.work_status = self.notice.label           # the line every action reports to
        lay.addWidget(self.notice)
        lay.addWidget(self._dashboard())
        self.tab_bar = QTabBar()
        self.tab_bar.setDrawBase(False)
        self.tab_bar.setExpanding(False)
        for name, icon in zip(TABS, ('wifi', 'shield', 'stethoscope')):
            self.tab_bar.addTab(icons.icon(icon, theme.TEXT_DIM, 16), t(name))
        lay.addWidget(self.tab_bar)
        self.tab_pages = [self._lan_tab(), self._vpn_tab(), self._diagnostics_tab()]
        for w in self.tab_pages:
            lay.addWidget(w)
        self.tab_bar.currentChanged.connect(self.show_tab)
        lay.addStretch(1)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll_session)
        self.show_tab(0)
        self.refresh_profiles()
        self.show_room()

    # -- frame --------------------------------------------------------------------------------------------------------
    def say(self, text, kind='info'):
        self.notice.show_message(text, kind)

    def show_tab(self, index):
        for i, w in enumerate(self.tab_pages):
            w.setVisible(i == index)        # hidden tabs take no height inside the page's scroll area
        if self.tab_bar.currentIndex() != index:
            self.tab_bar.setCurrentIndex(index)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._probed:
            self._probed = True
            self.probe_service()

    def probe_service(self):
        """One read-only GET: does the AKENO server offer LAN Play? A server without it is shown as such."""
        license_ = getattr(self.window, 'license', None)
        if license_ is None:
            return

        def done(result):
            self.service = result
            self.apply_service()

        def failed(_exc):
            self.service = None                 # unknown (offline): actions report their own error
            self.apply_service()
        self.ctl.run('akeno-network-status', lambda: LanClient(license_, _Memory()).status(), done, failed, silent=True)

    def apply_service(self):
        off = self.service is not None and not self.service.get('enabled')
        for b in (self.create_button, self.join_button):
            b.setEnabled(not off)
            b.setToolTip(t('LAN Play is not available on the AKENO server yet.') if off else '')
        if off:
            self.say(t('LAN Play is not active on the AKENO server yet. You can prepare everything else; rooms '
                       'can be created as soon as it is switched on.'), 'warn')
        self.show_room()

    # -- dashboard ----------------------------------------------------------------------------------------------------
    def _dashboard(self):
        row = ResponsiveRow(spacing=16)
        self.lan_dash = ConnectionCard(t('LAN Play'), 'wifi')
        self.lan_dash.right.addWidget(InfoButton(t(INFO_LAN)))
        self.tile_room = self.lan_dash.add_fact(t('Room'))
        self.tile_members = self.lan_dash.add_fact(t('Members connected'))
        self.tile_ping = self.lan_dash.add_fact(t('Average ping'), hint=t(INFO_PING))
        self.tile_paths = self.lan_dash.add_fact(t('Direct / relay'), hint=t(INFO_PATH))
        self.tile_vip = self.lan_dash.add_fact(t('Your virtual IP'), hint=t(INFO_VIP))
        row.add(self.lan_dash, 1)
        self.vpn_dash = ConnectionCard(t('VPN'), 'shield')
        self.vpn_dash.right.addWidget(InfoButton(t(INFO_VPN)))
        self.tile_location = self.vpn_dash.add_fact(t('Location'))
        self.tile_exit = self.vpn_dash.add_fact(t('Exit IP'))
        self.tile_provider = self.vpn_dash.add_fact(t('Provider'))
        self.tile_protocol = self.vpn_dash.add_fact(t('Protocol'))
        self.tile_vpn = self.tile_status = self.vpn_dash.add_fact(t('Status'), t('Disconnected'))
        self.vpn_dash.set_state(t('Disconnected'), 'unknown', t('Your internet traffic uses your normal connection.'))
        row.add(self.vpn_dash, 1)
        return row

    # -- LAN Play -----------------------------------------------------------------------------------------------------
    def _lan_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        self.session_pill = StatusPill(t('No room'), 'unknown')
        card = Card(t('LAN Play'), 'wifi', right=self.session_pill)
        self.lan_card = card
        self.steps = Steps([t(s) for s in STEPS])
        card.body.addWidget(self.steps)
        card.body.addWidget(Details(t('How LAN Play works'), t(HOW_LAN)))
        # without a room: create or join
        self.no_room = QWidget()
        row = ResponsiveRow(spacing=12)
        create = SubCard()
        create.lay.addWidget(heading(t('Create a room'), t('You become the owner: you invite friends, can remove '
                                                          'members and delete the room.')))
        self.room_name = QLineEdit()
        self.room_name.setPlaceholderText(t('Room name'))
        self.room_name.setMaxLength(64)
        self.create_password = QLineEdit()
        self.create_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.create_password.setPlaceholderText(t('Optional room password'))
        self.create_password.setMaxLength(128)
        create.lay.addWidget(self.room_name)
        create.lay.addWidget(self.create_password)
        self.create_button = button(t('Create room'), 'plus', 'primary', on_click=self.create)
        create.lay.addWidget(self.create_button)
        row.add(create, 1)
        join = SubCard()
        join.lay.addWidget(heading(t('Join with an invitation'), t('Ask the room owner for the invitation code. A '
                                                                  'code is valid for 10 minutes.')))
        self.invite_in = QLineEdit()
        self.invite_in.setPlaceholderText(t('Invitation code'))
        self.invite_in.setMaxLength(128)
        self.join_password = QLineEdit()
        self.join_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.join_password.setPlaceholderText(t('Room password, if it has one'))
        self.join_password.setMaxLength(128)
        join.lay.addWidget(self.invite_in)
        join.lay.addWidget(self.join_password)
        self.join_button = button(t('Join room'), 'link', on_click=self.join)
        join.lay.addWidget(self.join_button)
        row.add(join, 1)
        box = QVBoxLayout(self.no_room)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(row)
        card.body.addWidget(self.no_room)
        # with a room
        self.in_room = QWidget()
        room = QVBoxLayout(self.in_room)
        room.setContentsMargins(0, 0, 0, 0)
        room.setSpacing(12)
        title = QHBoxLayout()
        title.setSpacing(10)
        self.room_title = label('', 'h2')
        self.owner_pill = StatusPill(t('Owner'), 'info')
        title.addWidget(self.room_title)
        title.addWidget(self.owner_pill)
        title.addStretch(1)
        self.copy_ip_button = button(t('Copy virtual IP'), 'copy', 'ghost', on_click=self.copy_ip, tip=t(INFO_VIP))
        title.addWidget(self.copy_ip_button)
        room.addLayout(title)
        self.room_grid = KeyValueGrid(150)
        room.addWidget(self.room_grid)
        self.invite_box = SubCard()
        self.invite_box.lay.addWidget(heading(t('Invite friends'), t('Send the code to your friends. In AKENO they '
                                                                    'open AKENO Network → LAN Play and paste it under '
                                                                    '“Join with an invitation”. A new code makes the '
                                                                    'old one stop working.')))
        self.invite_out = QLineEdit()
        self.invite_out.setReadOnly(True)
        self.invite_out.setEchoMode(QLineEdit.EchoMode.Password)
        self.invite_out.setPlaceholderText(t('Create a new invitation to share it'))
        line = QHBoxLayout()
        line.setSpacing(8)
        line.addWidget(self.invite_out, 1)
        line.addWidget(button(t('Show'), 'eye', 'ghost', on_click=self.toggle_invite))
        line.addWidget(button(t('Copy'), 'copy', on_click=self.copy_invite))
        self.rotate_button = button(t('New invitation'), 'refresh', on_click=self.rotate,
                                    tip=t('Creates a new code valid for 10 minutes; the old one stops working.'))
        line.addWidget(self.rotate_button)
        self.invite_box.lay.addLayout(line)
        room.addWidget(self.invite_box)
        actions = FlowLayout(spacing=8)
        self.start_button = button(t('Start LAN session'), 'play', 'primary', on_click=self.start_session,
                                   tip=t('Windows asks once for administrator rights to create the LAN adapter.'))
        self.stop_button = button(t('Stop session'), 'stop', on_click=self.stop_session)
        self.allow_button = button(t('Allow a game…'), 'gamepad', on_click=self.allow_game,
                                   tip=t('Lets room members reach the game you pick. Nothing else on this PC is opened.'))
        for b in (self.start_button, self.stop_button, self.allow_button,
                  button(t('Refresh members'), 'refresh', 'ghost', on_click=self.members),
                  button(t('Recover'), 'restore', 'ghost', on_click=self.recover_session,
                         tip=t('Removes a LAN adapter and firewall rules left behind by an interrupted session.'))):
            actions.addWidget(b)
        room.addLayout(actions)
        if not BUILT:
            room.addWidget(label(t('LAN sessions run in the installed Windows application only.'), 'muted', wrap=True))
        room.addWidget(Divider())
        room.addWidget(heading(t('Members'), t(INFO_PATH)))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([t('Member'), t('Virtual IP'), t('Connection'), t('Ping'),
                                              t('Last handshake'), t('Traffic')])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setIconSize(QSize(26, 26))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        room.addWidget(self.table)
        self.empty = EmptyState('user', t('No members yet'), t('Share the invitation. Members appear here after '
                                                              'joining; refresh to update.'))
        room.addWidget(self.empty)
        actions = FlowLayout(spacing=8)
        self.remove_button = button(t('Remove member'), 'x', None, on_click=self.remove_member)
        self.leave_button = button(t('Leave room'), 'external', 'ghost', on_click=self.leave)
        self.delete_button = button(t('Delete room'), 'trash', 'danger', on_click=self.delete_room)
        for b in (self.remove_button, self.leave_button, self.delete_button):
            actions.addWidget(b)
        room.addLayout(actions)
        card.body.addWidget(self.in_room)
        for b in (self.start_button, self.stop_button, self.allow_button):
            b.setEnabled(BUILT)
        lay.addWidget(card)
        return page

    def identity(self):
        return SecureStore(sub('network') / 'lan-identity')

    def lan(self):
        return LanClient(self.window.license, self.identity())

    def room(self):
        room = self.identity().load().get('room_id') if WINDOWS else None
        if not room:
            raise ValueError(t('No room selected.'))
        return room

    def current_room(self):
        try:
            return self.room()
        except (ValueError, OSError):
            return None

    def own_ip(self):
        return self.session.get('virtual_ip') or next((m['virtual_ip'] for m in self.members_data if m.get('self')),
                                                      None)

    def show_room(self):
        room = self.current_room()
        self.no_room.setVisible(room is None)
        self.in_room.setVisible(room is not None)
        state = self.session.get('state')
        text, pill_state = SESSION.get(state, SESSION[None])
        if room is None:
            self.session_pill.set(t('No room'), 'unknown')
            off = self.service is not None and not self.service.get('enabled')
            self.lan_dash.set_state(t('Not connected'), 'unknown',
                                    t('LAN Play is not active on the AKENO server yet.') if off else
                                    t('Create a room or join one with an invitation.'))
            self.steps.set_current(0)
            for tile in (self.tile_room, self.tile_members, self.tile_ping, self.tile_paths, self.tile_vip):
                tile.set('—')
            return
        owner = bool(self.room_info.get('owner'))
        name = self.room_info.get('name') or room[:8] + '…'
        own = self.own_ip()
        self.room_title.setText(name)
        self.owner_pill.setVisible(owner)
        self.invite_box.setVisible(owner or not self.room_info)
        self.remove_button.setEnabled(owner)
        self.delete_button.setVisible(owner or not self.room_info)
        self.leave_button.setVisible(not owner)
        self.copy_ip_button.setEnabled(bool(own))
        rows = [(t('Your virtual IP'), own or t('After the first refresh')), (t('Session'), StatusPill(t(text), pill_state))]
        if self.session.get('error'):
            rows.append((t('Problem'), t(self.session['error'])))
        self.room_grid.set_rows(rows)
        self.session_pill.set(t(text), pill_state)
        detail = {'active': t('Your PC is in the room network.'), 'starting': t('Creating the LAN adapter…'),
                  'failed': t('The session stopped. Use Recover, then start it again.')}.get(
            state, t('Start the LAN session to connect to the other members.'))
        self.lan_dash.set_state(t(text), pill_state, detail)
        self.tile_room.set(name)
        self.tile_vip.set(own or '—')
        self.steps.set_current(1 if state != 'active' else 3 if self.game_allowed else 2)
        self.render_members()

    def render_members(self):
        active = self.session.get('state') == 'active'
        peers = self.session.get('peers', {}) if active else {}
        rows = list(self.members_data)
        self.table.setRowCount(len(rows))
        connected, direct, relay, pings = 0, 0, 0, []
        for i, m in enumerate(rows):
            live = peers.get(m['public_key'], {})
            mode = live.get('mode')
            if mode in ('direct', 'relay'):
                connected += 1
                direct += mode == 'direct'
                relay += mode == 'relay'
            latency = live.get('latency_ms')
            if latency is not None:
                pings.append(latency)
            path = (t('This PC'), 'ok') if m.get('self') else PATH.get(mode, PATH[None])
            if not m.get('self') and m.get('status') == 'expired':
                path = (t('Offline'), 'unknown')
            who = t('You') if m.get('self') else (m.get('name') or t('Member') + ' ' + m['actor'][:6])
            if m.get('owner') and not m.get('self'):
                who += ' · ' + t('Owner')
            values = [who, m['virtual_ip'], t(path[0]), f'{latency:.0f} ms' if latency is not None else '—',
                      _ago(live.get('handshake')),
                      f"↓ {_bytes(live.get('rx', 0))}  ↑ {_bytes(live.get('tx', 0))}" if live else '—']
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if c == 0:
                    item.setData(256, m['actor'])
                    item.setIcon(QIcon(avatar_pixmap(m['actor'], m.get('name') or ('You' if m.get('self') else 'M'))))
                    if m.get('owner'):
                        item.setToolTip(t('Room owner'))
                elif c == 2:
                    item.setForeground(_colour(path[1]))
                elif c == 3 and latency is not None:
                    item.setIcon(QIcon(quality_pixmap(quality(latency))))
                self.table.setItem(i, c, item)
        # the table is as tall as its rows (max 16 members): no inner scrolling inside the scrolling page
        self.table.setFixedHeight(self.table.horizontalHeader().height() + 40 * max(1, len(rows)) + 4)
        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        self.tile_members.set(str(connected) if active else '—')
        self.tile_paths.set(f'{direct} / {relay}' if active else '—')
        self.tile_ping.set(f'{sum(pings) / len(pings):.0f} ms' if pings else '—')

    def work(self, fn, done):
        self.say(t('Working…'), 'info')

        def complete(value):
            self.notice.clear()
            done(value)

        def failed(exc):
            self.say(t(messages.text(exc)), 'error')
        # silent: the page shows its own translated message; the status bar must not repeat a raw server answer
        if not self.ctl.run('akeno-network', fn, complete, failed, silent=True):
            self.say(t('A network operation is already running.'), 'warn')

    def create(self):
        name, password = self.room_name.text().strip(), self.create_password.text()
        if not name:
            self.say(t('Enter a room name with 1 to 64 characters.'), 'warn')
            return
        self.work(lambda: self.lan().create(name, password), self.room_changed)

    def join(self):
        invite, password = self.invite_in.text().strip(), self.join_password.text()
        if not invite:
            self.say(t('Paste the invitation code you received.'), 'warn')
            return
        self.work(lambda: self.lan().join(invite, password), self.room_changed)

    def room_changed(self, result):
        self.create_password.clear()
        self.join_password.clear()
        self.invite_in.clear()
        created = bool(result.get('invite'))
        if created:
            self.invite_out.setText(result['invite'])
        self.members_data = []
        self.room_info = {'owner': True, 'name': self.room_name.text().strip()} if created else {}
        self.show_room()
        self.members()
        if created:
            self.say(t('Room created. Copy the invitation and send it to your friends: they paste it under “Join '
                       'with an invitation”. Then start the LAN session.'), 'success')

    def members(self):
        def show(result):
            own = self.identity().load().get('public_key')
            self.members_data = [dict(m, self=m.get('public_key') == own) for m in result['members']]
            if isinstance(result.get('room'), dict):
                self.room_info = result['room']
            self.show_room()
        self.work(lambda: self.lan().heartbeat(self.room()), show)

    def leave(self):
        if not confirm(self, t('Leave room'), t('Leave this room? You need a new invitation to come back.')):
            return
        self.stop_if_running()
        self.work(lambda: self.lan().leave(self.room()), lambda _: self.left())

    def delete_room(self):
        if not confirm(self, t('Delete room'), t('Delete this private room and remove its memberships?')):
            return
        self.stop_if_running()
        self.work(lambda: self.lan().delete(self.room()), lambda _: self.left())

    def left(self):
        self.members_data, self.session, self.room_info, self.game_allowed = [], {}, {}, False
        self.invite_out.clear()
        self.timer.stop()
        self.show_room()

    def rotate(self):
        self.work(lambda: self.lan().rotate_invite(self.room()), lambda result: self.invite_out.setText(result['invite']))

    def toggle_invite(self):
        normal = self.invite_out.echoMode() == QLineEdit.EchoMode.Normal
        self.invite_out.setEchoMode(QLineEdit.EchoMode.Password if normal else QLineEdit.EchoMode.Normal)

    def copy_invite(self):
        from PySide6.QtWidgets import QApplication
        if self.invite_out.text():
            QApplication.clipboard().setText(self.invite_out.text())
            self.say(t('Invitation copied. It is valid for 10 minutes.'), 'success')

    def copy_ip(self):
        from PySide6.QtWidgets import QApplication
        if self.own_ip():
            QApplication.clipboard().setText(self.own_ip())
            self.say(t('Virtual IP copied. Friends enter it in the game to join your server.'), 'success')

    def remove_member(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        member = item.data(256) if item else None
        if not member:
            self.say(t('Select a member in the list first.'), 'warn')
            return
        if not self.members_data[row].get('self') and \
                confirm(self, t('Remove member'), t('Remove the selected member from this room? They cannot rejoin.')):
            self.work(lambda: self.lan().remove_member(self.room(), member), lambda _: self.members())

    # LAN session (elevated helper) ---------------------------------------------------------------------------------
    def start_session(self):
        if not confirm(self, t('Start LAN session'), t(
                'Windows asks for administrator rights once. AKENO then creates the LAN adapter with the official '
                'WireGuard service and firewall rules for this room only: file sharing, Remote Desktop and other '
                'services on this PC stay unreachable for members. The session runs until you stop it.')):
            return
        from akeno.network.helper import invoke
        room = self.room()

        def started(result):
            self.session = {'state': result.get('state', 'starting')}
            self.timer.start()
            self.show_room()
        self.work(lambda: invoke('lan_session', room), started)

    def stop_session(self):
        from akeno.network.helper import stop_lan
        try:
            stop_lan(self.room())
            self.say(t('Stopping the LAN session…'))
            self.game_allowed = False
            self.timer.start()
        except (ValueError, OSError) as exc:
            info(self, t('AKENO Network'), t(messages.text(exc)), error=True)

    def stop_if_running(self):
        if self.session.get('state') in ('starting', 'active'):
            self.stop_session()

    def recover_session(self):
        from akeno.network.helper import invoke
        self.work(lambda: invoke('lan_recover', self.room()), lambda _: self.poll_session())

    def allow_game(self):
        if self.session.get('state') != 'active':
            info(self, t('Allow a game…'), t('Start the LAN session first.'))
            return
        path, _ = QFileDialog.getOpenFileName(self, t('Choose the game program'), '', t('Programs (*.exe)'))
        if path:
            from akeno.network.helper import allow_game
            allow_game(self.room(), path)
            self.game_allowed = True
            self.say(t('{name} will be reachable for room members.').format(name=Path(path).name), 'success')
            self.show_room()

    def poll_session(self):
        if not WINDOWS:
            return
        from akeno.network.helper import lan_status
        try:
            state = lan_status()
        except OSError:
            return
        if state.get('room') not in (None, self.current_room()):
            return
        self.session = state
        if state.get('state') in ('stopped', 'failed', None):
            self.timer.stop()
            self.game_allowed = False
        self.show_room()

    # -- VPN ----------------------------------------------------------------------------------------------------------
    def _vpn_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        row = ResponsiveRow(spacing=16)
        self.vpn_pill = StatusPill(t('Disconnected'), 'unknown')
        places = Card(t('VPN locations'), 'globe', right=self.vpn_pill)
        places.body.addWidget(heading(t('Choose where your traffic leaves to the internet'), t(INFO_SOURCES)))
        self.location_search = QLineEdit()
        self.location_search.setPlaceholderText(t('Search country or profile'))
        self.location_search.addAction(icons.icon('search', theme.TEXT_MUTED, 16),
                                       QLineEdit.ActionPosition.LeadingPosition)
        self.location_search.setClearButtonEnabled(True)
        self.location_search.textChanged.connect(self.render_locations)
        places.body.addWidget(self.location_search)
        self.location_list = QListWidget()
        self.location_list.setMinimumHeight(300)
        self.location_list.setIconSize(QSize(24, 16))
        self.location_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.location_list.currentItemChanged.connect(lambda *_: self.location_selected())
        places.body.addWidget(self.location_list)
        self.location_reason = label('', 'muted', wrap=True)
        places.body.addWidget(self.location_reason)
        big = QHBoxLayout()
        big.setSpacing(10)
        self.connect_button = button(t('Connect'), 'power', 'primary', on_click=lambda: self.vpn('connect'))
        self.disconnect_button = button(t('Disconnect'), 'stop', on_click=lambda: self.vpn('disconnect'))
        self.favourite_button = button(t('Favourite'), 'star', 'ghost', on_click=self.toggle_favourite)
        for b in (self.connect_button, self.disconnect_button):
            b.setMinimumHeight(44)
            b.setMinimumWidth(160)
        big.addWidget(self.connect_button)
        big.addWidget(self.disconnect_button)
        big.addStretch(1)
        big.addWidget(self.favourite_button)
        places.body.addLayout(big)
        row.add(places, 3)
        side = QWidget()
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.setSpacing(16)
        own = Card(t('Your WireGuard profiles'), 'key')
        own.body.addWidget(heading(t('Import from your own server or VPN provider'), t(
            'AKENO stores the profile encrypted for your Windows account (DPAPI) and connects through the official '
            'WireGuard service. Private keys never leave this PC.')))
        self.profiles = QComboBox()
        own.body.addWidget(self.profiles)
        actions = FlowLayout(spacing=8)
        for text, icon, action in ((msg('Import profile…'), 'file', self.import_profile),
                                   (msg('Rename'), 'edit', self.rename_profile),
                                   (msg('Set country…'), 'globe', self.tag_country),
                                   (msg('Remove'), 'trash', self.remove_profile)):
            b = button(t(text), icon, 'ghost' if action != self.import_profile else None, on_click=action)
            b.setEnabled(WINDOWS)
            actions.addWidget(b)
        own.body.addLayout(actions)
        side_lay.addWidget(own)
        conn = Card(t('Connection'), 'activity')
        self.vpn_grid = KeyValueGrid(130)
        self.vpn_grid.set_rows([(t('Exit IP'), t('Not checked'))])
        conn.body.addWidget(self.vpn_grid)
        actions = FlowLayout(spacing=8)
        self.status_button = button(t('Status'), 'activity', 'ghost', on_click=lambda: self.vpn('status'))
        self.exit_button = button(t('Check exit IP'), 'globe', 'ghost', on_click=lambda: self.vpn('exit_ip'))
        actions.addWidget(self.status_button)
        actions.addWidget(self.exit_button)
        conn.body.addLayout(actions)
        conn.body.addWidget(Details(t('Security notes'), t(HOW_VPN)))
        side_lay.addWidget(conn)
        hist = Card(t('Connection history'), 'clock', right=button(t('Clear history'), 'trash', 'ghost',
                                                                 on_click=self.clear_history))
        hist.body.addWidget(heading(t('Only when, where and how long'), t(
            'AKENO keeps the last 20 connections on this PC: start, end and location. No addresses, traffic or '
            'visited sites are recorded.'), 'muted'))
        self.history_box = QVBoxLayout()
        self.history_box.setSpacing(4)
        hist.body.addLayout(self.history_box)
        side_lay.addWidget(hist)
        side_lay.addStretch(1)
        row.add(side, 2)
        lay.addWidget(row)
        lay.addWidget(self._vpngate_card())
        for b in (self.disconnect_button, self.status_button, self.exit_button):
            b.setEnabled(BUILT)
        self.render_history()
        return page

    def store(self):
        return ProfileStore(sub('network') / 'profiles.sqlite3')

    def refresh_profiles(self):
        self.profiles.clear()
        self.profile_meta = []
        if not WINDOWS:
            self.profiles.addItem(t('Profiles are stored with Windows DPAPI (Windows only)'))
            self.profiles.setEnabled(False)
        else:
            try:
                self.profile_meta = self.store().metadata()
                for p in self.profile_meta:
                    self.profiles.addItem(p['name'], p['id'])
                if not self.profile_meta:
                    self.profiles.addItem(t('No profile imported yet'))
            except Exception as exc:
                self.say(t(messages.text(exc)), 'error')
        self.render_locations()

    def render_locations(self):
        selected = self.selected_location()
        items = providers.search(providers.locations(self.profile_meta, self.prefs), self.location_search.text(),
                                 country_name)
        self.location_list.blockSignals(True)
        self.location_list.clear()
        self.location_items = {}
        for loc in items:
            name = loc.name if loc.kind == providers.OWN else (country_name(loc.country) or loc.name)
            source = t(providers.KIND_NAMES[loc.kind])
            line2 = (t('Ready') + ' · ' + loc.protocol) if loc.available else t(loc.reason)
            item = QListWidgetItem(QIcon(flag_pixmap(loc.country)),
                                   f"{'★ ' if loc.favourite else ''}{name}\n{source} · {line2}")
            item.setData(256, loc.id)
            item.setSizeHint(QSize(0, 50))
            item.setToolTip(t(loc.reason) if loc.reason else source)
            if not loc.available:
                item.setForeground(_colour('unknown'))
            self.location_list.addItem(item)
            self.location_items[loc.id] = loc
            if selected and loc.id == selected.id:
                self.location_list.setCurrentItem(item)
        if items and self.location_list.currentRow() < 0:
            self.location_list.setCurrentRow(0)
        self.location_list.blockSignals(False)
        self.location_selected()
        if not items:
            self.location_reason.setText(t('No location matches your search.'))

    def selected_location(self):
        item = self.location_list.currentItem() if hasattr(self, 'location_list') else None
        return self.location_items.get(item.data(256)) if item else None

    def location_selected(self):
        loc = self.selected_location()
        if loc is None:
            self.connect_button.setEnabled(False)
            self.location_reason.setText('')
            return
        self.location_reason.setText(t(loc.reason) if not loc.available else
                                     t('{source}: {name}').format(source=t(providers.KIND_NAMES[loc.kind]), name=loc.name))
        self.connect_button.setEnabled(BUILT and loc.available and bool(loc.profile_id))
        self.favourite_button.setText(t('Remove favourite') if loc.favourite else t('Favourite'))
        if loc.profile_id:
            index = self.profiles.findData(loc.profile_id)
            if index >= 0:
                self.profiles.setCurrentIndex(index)

    def toggle_favourite(self):
        loc = self.selected_location()
        if loc:
            self.prefs.toggle_favourite(loc.id)
            self.render_locations()

    def import_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, t('Import WireGuard profile'), '', 'WireGuard (*.conf)')
        if not path:
            return
        try:
            with open(path, 'rb') as f:
                raw = f.read(65537)
            if len(raw) > 65536:
                raise ValueError(t('Profile is too large.'))
            self.store().add(Path(path).stem, raw.decode('utf-8-sig'))
            self.refresh_profiles()
            self.say(t('Profile imported. Select it in the list and click Connect.'), 'success')
        except Exception as exc:
            info(self, t('AKENO Network'), t(messages.text(exc)), error=True)

    def rename_profile(self):
        ident = self.profiles.currentData()
        if not ident:
            return
        name, ok = QInputDialog.getText(self, t('Rename profile'), t('Profile name'))
        if ok:
            try:
                self.store().rename(ident, name)
                self.refresh_profiles()
            except Exception as exc:
                info(self, t('AKENO Network'), t(messages.text(exc)), error=True)

    def tag_country(self):
        ident = self.profiles.currentData()
        if not ident:
            return
        codes = [''] + sorted(providers.catalog()['countries'], key=country_name)
        names = [t('Unknown')] + [f'{country_name(c)} ({c})' for c in codes[1:]]
        current = self.prefs.country_of(ident) or ''
        choice, ok = QInputDialog.getItem(self, t('Set country…'), t('Where is this server?'), names,
                                          codes.index(current) if current in codes else 0, False)
        if ok:
            self.prefs.set_country(ident, codes[names.index(choice)] or None)
            self.render_locations()

    def remove_profile(self):
        ident = self.profiles.currentData()
        if not ident:
            return
        state = SecureStore(sub('network') / 'tunnel').load()
        if state.get('profile_id') == ident and state.get('phase') != 'disconnected':
            info(self, t('AKENO Network'), t('Disconnect or recover this profile before removing it.'))
            return
        if confirm(self, t('Remove profile'), t('Remove the encrypted VPN profile?')):
            try:
                self.store().remove(ident)
                self.prefs.set_country(ident, None)
                self.refresh_profiles()
            except Exception as exc:
                info(self, t('AKENO Network'), t(messages.text(exc)), error=True)

    def vpn(self, action):
        loc = self.selected_location()
        ident = loc.profile_id if loc and loc.profile_id else self.profiles.currentData()
        if action == 'connect':
            if not loc or not loc.available or not ident:
                info(self, t('Connect VPN'), t(loc.reason) if loc and loc.reason else t('Choose a location first.'))
                return
            if not confirm(self, t('Connect VPN'), t(
                    'Allow the official WireGuard service to apply this profile and change its routes/DNS? The '
                    'connection persists until you disconnect it.')):
                return
        from akeno.network.helper import invoke
        self.work(lambda: invoke(action, ident), lambda result: self.show_vpn(result, loc))

    def show_vpn(self, result, loc=None):
        state = result.get('state')
        text, pill = TUNNEL.get(state, (str(state), 'unknown'))
        self.vpn_pill.set(t(text), pill)
        self.tile_status.set(t(text))
        where = (loc.name if loc.kind == providers.OWN else country_name(loc.country)) if loc else '—'
        connected = state == 'connected'
        self.vpn_dash.set_state(t(text), pill, t('Your internet traffic goes through {where}.').format(where=where)
                                if connected else t('Your internet traffic uses your normal connection.'))
        if connected:
            self.tile_location.set(where)
            self.tile_provider.set(t(providers.KIND_NAMES[loc.kind]) if loc else '—')
            self.tile_protocol.set(loc.protocol if loc else 'WireGuard')
            if self.vpn_started is None:
                self.vpn_started = time.time()
                self.prefs.record(where, self.vpn_started)
        else:
            for tile in (self.tile_location, self.tile_provider, self.tile_protocol):
                tile.set('—')
            if self.vpn_started is not None and state == 'disconnected':
                hist = self.prefs.history()
                if hist:
                    self.prefs.record(hist[0]['location'], self.vpn_started, time.time())
                self.vpn_started = None
        exit_ip = result.get('exit_ip')
        self.tile_exit.set(exit_ip or ('—' if not connected else self.tile_exit.value.text()))
        rows = [(t('Exit IP'), exit_ip if exit_ip else t('Not checked'))]
        if result.get('last_handshake'):
            rows.append((t('Last handshake'), _ago(result['last_handshake'])))
        self.vpn_grid.set_rows(rows)
        self.render_history()

    def render_history(self):
        clear_layout(self.history_box)
        entries = self.prefs.history()[:6]
        if not entries:
            self.history_box.addWidget(label(t('No connections yet.'), 'muted'))
            return
        for h in entries:
            start = time.strftime('%d.%m. %H:%M', time.localtime(h['started']))
            span = (t('{n} min').format(n=max(1, (h['ended'] - h['started']) // 60)) if h.get('ended')
                    else t('connected'))
            self.history_box.addWidget(label(f"{start} · {h['location']} · {span}", 'dim'))

    def clear_history(self):
        self.prefs.clear_history()
        self.render_history()

    # -- VPN Gate review (connecting disabled) ---------------------------------------------------------------------
    def _vpngate_card(self):
        card = Card(t('VPN Gate (disabled)'), 'lock', right=StatusPill(t('Not available'), 'unknown'))
        details = Details(t('Why it is disabled and what the catalog lists'), t(VPNGATE))
        line = QHBoxLayout()
        line.setSpacing(8)
        self.countries = QComboBox()
        self.countries.addItem(t('Load the catalog first'))
        self.countries.setEnabled(False)
        line.addWidget(self.countries, 1)
        line.addWidget(button(t('Load catalog'), 'refresh', on_click=self.catalog))
        details.body.addLayout(line)
        self.location_grid = KeyValueGrid(130)
        self.location_grid.set_rows([(t('USA'), t('Not checked')), (t('Turkey'), t('Not checked'))])
        details.body.addWidget(self.location_grid)
        connect = button(t('Connect (not available yet)'), 'lock')
        connect.setEnabled(False)
        details.body.addWidget(connect)
        card.body.addWidget(details)
        self.vpngate_details = details
        return card

    def catalog(self):
        from akeno.network.vpngate import countries, fetch_catalog, reachable

        def load():
            servers = fetch_catalog()
            # Only the two target countries are probed, at most five servers each, from this PC.
            probes = {code: [reachable(s) for s in [s for s in servers if s.country == code][:5]]
                      for code in ('US', 'TR')}
            return servers, probes

        def show(result):
            servers, probes = result
            self.catalog_servers = servers
            self.countries.clear()
            counts = {}
            for s in servers:
                counts[s.country] = counts.get(s.country, 0) + 1
            for code, name in countries(servers):
                self.countries.addItem(t('{name} ({code}) · {n} servers').format(name=name, code=code, n=counts[code]),
                                       code)
            self.countries.setEnabled(bool(servers))
            if not servers:
                self.countries.addItem(t('The catalog lists no usable servers right now'))

            def where(code):
                n = counts.get(code, 0)
                if not n:
                    return t('Not in the current catalog')
                states = probes.get(code, [])
                ok = states.count('reachable')
                if ok:
                    reach = t('{ok} of {n} checked reachable from this PC').format(ok=ok, n=len(states))
                elif states.count('unreachable'):
                    reach = t('not reachable from this PC')
                else:
                    reach = t('reachability unknown (UDP)')
                return t('{n} servers in the catalog').format(n=n) + ' · ' + reach
            self.location_grid.set_rows([(t('USA'), where('US')), (t('Turkey'), where('TR'))])
            self.say(t('Catalog loaded. Public VPN connections remain disabled.'))
        self.work(load, show)

    # -- diagnostics --------------------------------------------------------------------------------------------------
    def _diagnostics_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        right = QWidget()
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        self.export_button = button(t('Export report…'), 'export', 'ghost', on_click=self.export_diagnostics,
                                    tip=t('Saves the results as a text file without passwords, tokens or keys.'))
        self.export_button.setEnabled(False)
        rl.addWidget(self.export_button)
        rl.addWidget(button(t('Run diagnostics'), 'activity', 'primary', on_click=self.diagnose))
        card = Card(t('Connection diagnostics'), 'stethoscope', right=right)
        self.diag_box = QVBoxLayout()
        self.diag_box.setSpacing(4)
        self.diag_box.addWidget(label(t('Checks what LAN Play and the VPN need on this PC. Read-only: nothing is '
                                        'changed.'), 'dim', wrap=True))
        card.body.addLayout(self.diag_box)
        lay.addWidget(card)
        return page

    def diagnose(self):
        license_ = getattr(self.window, 'license', None)

        def show(rows):
            self.diag_rows = rows
            clear_layout(self.diag_box)
            for state, title, detail in rows:
                self.diag_box.addWidget(CheckRow(state, t(title), t(detail)))
            self.export_button.setEnabled(bool(rows))
        self.work(lambda: diagnostics(license_), show)

    def export_diagnostics(self):
        path, _ = QFileDialog.getSaveFileName(self, t('Export report…'), 'akeno-network-diagnostics.txt',
                                              t('Text files (*.txt)'))
        if path:
            Path(path).write_text(report(self.diag_rows), encoding='utf-8')
            self.say(t('Report saved: {p}').format(p=path), 'success')


class _Memory:
    """In-memory identity for the read-only status probe (it never registers a key)."""

    def load(self):
        return {}

    def save(self, _value):
        pass


def _colour(state):
    from PySide6.QtGui import QColor
    return QColor(theme.STATE_COLORS.get(state, theme.TEXT_DIM))


def report(rows):
    """Plain-text diagnostics export. Contains only the check results and the AKENO/Windows version: no tokens,
    passwords, keys, addresses or account data (the rows never carry them)."""
    from akeno import __version__
    lines = [f'AKENO Network diagnostics · AKENO {__version__} · {platform.system()} {platform.release()} · '
             f'{time.strftime("%Y-%m-%d %H:%M")}', '']
    for state, title, detail in rows:
        lines.append(f'[{state.upper():4}] {t(title)}: {t(detail)}')
    return redact('\n'.join(lines) + '\n')


def redact(text):
    """Defence in depth for the export: removes anything shaped like a WireGuard/base64 key, bearer token or JWT."""
    import re
    text = re.sub(r'[A-Za-z0-9+/]{42,43}=', '[key removed]', text)
    text = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[token removed]', text)
    return re.sub(r'(?i)(bearer|token|password|private_?key)\s*[:=]\s*\S+', r'\1: [removed]', text)


def diagnostics(license_manager):
    """(state, title, detail) rows. Read-only checks; Windows-only facts are reported as such elsewhere."""
    rows = server_checks(license_manager)
    if not WINDOWS:
        return rows + [('warn', msg('Windows required'),
                        msg('LAN Play and the VPN use the official WireGuard service on Windows.'))]
    device = (license_manager.state or {}).get('device_id') if license_manager else None
    rows.append(('ok' if device else 'fail', msg('Signed in on this PC'),
                 msg('This PC is registered with your AKENO account.') if device else msg('Sign in and register this PC first.')))
    try:
        unlocked = license_manager.view().unlocked
    except Exception:
        unlocked = False
    rows.append(('ok' if unlocked else 'fail', msg('AKENO license'), msg('Active.') if unlocked else
                 msg('LAN Play and connecting the VPN need an active license.')))
    from akeno.network.windows import NativeUnavailable, WindowsWireGuard, service_state
    try:
        WindowsWireGuard()._trusted('wireguard.exe')
        rows.append(('ok', msg('WireGuard for Windows'), msg('Installed and signed by WireGuard LLC.')))
    except NativeUnavailable:
        rows.append(('fail', msg('WireGuard for Windows'), msg('Install the official WireGuard for Windows from wireguard.com.')))
    try:
        manager = service_state('WireGuardManager', prefix='')
    except NativeUnavailable:
        manager = None
    rows.append(('ok' if manager == 'running' else 'warn', msg('WireGuard manager service'),
                 msg('Running.') if manager == 'running' else msg('Start WireGuard once so its manager service runs.')))
    try:
        from akeno.network.lan_windows import powershell
        enabled = 'True' in powershell('(Get-NetFirewallProfile -Name Public).Enabled')
    except Exception:
        enabled = None
    rows.append(('ok' if enabled else 'fail' if enabled is False else 'warn', msg('Windows Defender Firewall (Public)'),
                 msg('On: members cannot reach file sharing, Remote Desktop or other services.') if enabled else
                 msg('Off or unknown: LAN Play refuses to start without it.')))
    from akeno.network.helper import lan_status
    state = lan_status()
    if state.get('state') == 'failed':
        rows.append(('fail', msg('Last LAN session'), state.get('error') or msg('Failed.')))
    elif state.get('state') == 'active':
        rows.append(('ok', msg('LAN session'), msg('Running.')))
        modes = [p.get('mode') for p in (state.get('peers') or {}).values()]
        if modes:
            rows.append(('ok' if 'direct' in modes else 'info', msg('Connection path'),
                         msg('{d} direct (P2P), {r} via relay.', d=modes.count('direct'), r=modes.count('relay'))))
    journal = SecureStore(sub('network') / 'lan-journal').load()
    if journal.get('phase') in ('lan_starting', 'lan_active') and state.get('state') != 'active':
        rows.append(('warn', msg('Interrupted LAN session'), msg('Use Recover to remove its adapter and firewall rules.')))
    vpn = SecureStore(sub('network') / 'tunnel').load()
    if vpn.get('phase') == 'recovery_required':
        rows.append(('warn', msg('VPN tunnel needs recovery'), msg('Use Disconnect to remove the interrupted tunnel.')))
    elif vpn.get('phase') in (None, 'disconnected'):
        rows.append(('ok', msg('VPN routing'), msg('No AKENO VPN tunnel is active; Windows uses your normal routes.')))
    return rows


def server_checks(license_manager):
    """Name resolution and the AKENO server's LAN Play status (one read-only request each)."""
    if license_manager is None:
        return []
    import socket
    from urllib.parse import urlparse
    rows = []
    try:
        host = urlparse(license_manager._api().base).hostname
        socket.getaddrinfo(host, 443)
        rows.append(('ok', msg('DNS'), msg('The AKENO server name resolves.')))
    except Exception:
        return [('fail', msg('DNS'), msg('The AKENO server name does not resolve. Check your internet connection or DNS.'))]
    try:
        status = LanClient(license_manager, _Memory()).status()
        rows.append(('ok', msg('AKENO server'), msg('Reachable.')))
        rows.append(('ok' if status['enabled'] else 'warn', msg('LAN Play service'),
                     msg('Offered by the AKENO server.') if status['enabled'] else
                     msg('Not active on the AKENO server yet.')))
        if status['enabled']:
            rows.append(('ok' if status['relay'] else 'warn', msg('Coordinator and relay'),
                         msg('Available: direct connections are arranged, relay is the fallback.') if status['relay']
                         else msg('Not configured on the server: rooms work, but no data connection can be made.')))
    except Exception as exc:
        rows.append(('fail', msg('AKENO server'), messages.text(exc)))
    return rows
