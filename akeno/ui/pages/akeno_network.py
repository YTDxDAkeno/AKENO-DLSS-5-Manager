"""AKENO Network: LAN Play rooms and sessions, own WireGuard VPN, the public VPN catalog and connection diagnostics.

Every action has a real function or says plainly that it is not available yet. Nothing touches the network when the
page is built: LAN sessions and VPN connections start only on an explicit click and Windows' UAC prompt.
"""
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from akeno.i18n import msg, t
from akeno.licensing.storage import SecureStore
from akeno.network.client import LanClient
from akeno.network.profiles import ProfileStore
from akeno.paths import sub
from akeno.ui.widgets import (Card, CheckRow, Divider, EmptyState, FlowLayout, KeyValueGrid, PageHeader, ResponsiveRow,
                              StatTile, StatusPill, SubCard, button, clear_layout, confirm, info, label)

WINDOWS = sys.platform == 'win32'
BUILT = WINDOWS and bool(getattr(sys, 'frozen', False))

SESSION = {None: (msg('Not connected'), 'unknown'), 'stopped': (msg('Not connected'), 'unknown'),
           'starting': (msg('Starting…'), 'pending'), 'active': (msg('Connected'), 'ok'), 'failed': (msg('Failed'), 'fail')}
PATH = {'direct': (msg('Direct (P2P)'), 'ok'), 'relay': (msg('Relay'), 'info'), 'direct_trying': (msg('Connecting directly…'), 'pending'),
        'relay_trying': (msg('Connecting via relay…'), 'pending'), None: ('—', 'unknown')}
TUNNEL = {'disconnected': (msg('Disconnected'), 'unknown'), 'starting': (msg('Starting…'), 'pending'),
          'awaiting_handshake': (msg('Waiting for the server'), 'pending'), 'connected': (msg('Connected'), 'ok'),
          'stopping': (msg('Disconnecting…'), 'pending'), 'failed': (msg('Failed'), 'fail')}


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


class AkenoNetworkPage(QWidget):
    def __init__(self, ctl, window):
        super().__init__()
        self.ctl, self.window = ctl, window
        self.members_data, self.session, self.catalog_servers = [], {}, []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        lay.addWidget(PageHeader(t('AKENO Network'), t('LAN Play with friends over an encrypted WireGuard network, and '
                                                       'your own WireGuard VPN. Nothing connects until you start it.')))
        lay.addWidget(self._overview())
        lay.addWidget(self._lan_card())
        row = ResponsiveRow(spacing=16)
        row.add(self._vpn_card(), 1)
        row.add(self._locations_card(), 1)
        lay.addWidget(row)
        lay.addWidget(self._diagnostics_card())
        self.work_status = label('', 'muted', wrap=True)
        lay.addWidget(self.work_status)
        lay.addStretch(1)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll_session)
        self.refresh_profiles()
        self.show_room()

    # -- overview ---------------------------------------------------------------------------------------------------
    def _overview(self):
        card = Card(t('Overview'), 'activity')
        tiles = QHBoxLayout()
        tiles.setSpacing(24)
        self.tile_session = StatTile(t('LAN Play'), t('Not connected'))
        self.tile_members = StatTile(t('Members connected'), '—')
        self.tile_paths = StatTile(t('Direct / relay'), '—', t('Direct: peer-to-peer. Relay: through the AKENO '
                                                               'coordinator, still end-to-end encrypted.'))
        self.tile_ping = StatTile(t('Average ping'), '—')
        self.tile_vpn = StatTile(t('VPN'), t('Disconnected'))
        for tile in (self.tile_session, self.tile_members, self.tile_paths, self.tile_ping, self.tile_vpn):
            tiles.addWidget(tile, 1)
        card.body.addLayout(tiles)
        return card

    # -- LAN Play ---------------------------------------------------------------------------------------------------
    def _lan_card(self):
        self.session_pill = StatusPill(t('Not connected'), 'unknown')
        card = Card(t('LAN Play'), 'wifi', right=self.session_pill)
        self.lan_card = card
        card.body.addWidget(label(t('Games that connect by IP address work across the room: share your virtual IP '
                                    'below. LAN discovery by broadcast is not forwarded yet, and not every game has '
                                    'been tested.'), 'dim', wrap=True))
        # without a room: create or join
        self.no_room = QWidget()
        row = ResponsiveRow(spacing=12)
        create = SubCard()
        create.lay.addWidget(label(t('Create a room'), 'h3'))
        self.room_name = QLineEdit()
        self.room_name.setPlaceholderText(t('Room name'))
        self.create_password = QLineEdit()
        self.create_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.create_password.setPlaceholderText(t('Optional room password'))
        create.lay.addWidget(self.room_name)
        create.lay.addWidget(self.create_password)
        create.lay.addWidget(button(t('Create room'), 'plus', 'primary', on_click=self.create))
        row.add(create, 1)
        join = SubCard()
        join.lay.addWidget(label(t('Join with an invitation'), 'h3'))
        self.invite_in = QLineEdit()
        self.invite_in.setPlaceholderText(t('Invitation code'))
        self.join_password = QLineEdit()
        self.join_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.join_password.setPlaceholderText(t('Room password, if it has one'))
        join.lay.addWidget(self.invite_in)
        join.lay.addWidget(self.join_password)
        join.lay.addWidget(button(t('Join room'), 'link', on_click=self.join))
        row.add(join, 1)
        box = QVBoxLayout(self.no_room)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(row)
        card.body.addWidget(self.no_room)
        # with a room
        self.in_room = QWidget()
        room = QVBoxLayout(self.in_room)
        room.setContentsMargins(0, 0, 0, 0)
        room.setSpacing(10)
        self.room_grid = KeyValueGrid(150)
        room.addWidget(self.room_grid)
        self.invite_out = QLineEdit()
        self.invite_out.setReadOnly(True)
        self.invite_out.setEchoMode(QLineEdit.EchoMode.Password)
        self.invite_out.setPlaceholderText(t('Create a new invitation to share it'))
        line = QHBoxLayout()
        line.setSpacing(8)
        line.addWidget(self.invite_out, 1)
        line.addWidget(button(t('Show'), 'eye', 'ghost', on_click=self.toggle_invite))
        line.addWidget(button(t('Copy'), 'copy', on_click=self.copy_invite))
        line.addWidget(button(t('New invitation'), 'refresh', on_click=self.rotate,
                              tip=t('Creates a new code valid for 10 minutes; the old one stops working.')))
        room.addLayout(line)
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
        room.addWidget(label(t('Members'), 'h3'))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([t('Member'), t('Virtual IP'), t('Connection'), t('Ping'),
                                              t('Last handshake'), t('Traffic')])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMinimumHeight(170)
        room.addWidget(self.table)
        self.empty = EmptyState('user', t('No members yet'), t('Share the invitation. Members appear here after '
                                                              'joining; refresh to update.'))
        room.addWidget(self.empty)
        actions = FlowLayout(spacing=8)
        for text, icon, kind, action in ((msg('Remove member'), 'x', None, self.remove_member),
                                         (msg('Leave room'), 'external', 'ghost', self.leave),
                                         (msg('Delete room'), 'trash', 'danger', self.delete_room)):
            actions.addWidget(button(t(text), icon, kind, on_click=action))
        room.addLayout(actions)
        room.addWidget(label(t('Only the room owner can remove members, create invitations and delete the room. '
                               'Removed members are blocked from this room for good.'), 'muted', wrap=True))
        card.body.addWidget(self.in_room)
        for b in (self.start_button, self.stop_button, self.allow_button):
            b.setEnabled(BUILT)
        return card

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

    def show_room(self):
        room = self.current_room()
        self.no_room.setVisible(room is None)
        self.in_room.setVisible(room is not None)
        if room is None:
            self.session_pill.set(t('No room'), 'unknown')
            return
        own = self.session.get('virtual_ip') or next((m['virtual_ip'] for m in self.members_data
                                                     if m.get('self')), None)
        text, state = SESSION.get(self.session.get('state'), SESSION[None])
        pill = StatusPill(t(text), state)
        self.room_grid.set_rows([(t('Room'), room[:8] + '…'), (t('Your virtual IP'), own or t('After the first refresh')),
                                 (t('Session'), pill)] + ([(t('Problem'), self.session['error'])]
                                                          if self.session.get('error') else []))
        self.session_pill.set(t(text), state)
        self.render_members()

    def render_members(self):
        peers = self.session.get('peers', {}) if self.session.get('state') == 'active' else {}
        rows = [m for m in self.members_data]
        self.table.setRowCount(len(rows))
        connected, direct, relay, pings = 0, 0, 0, []
        for i, m in enumerate(rows):
            live = peers.get(m['public_key'], {})
            mode = live.get('mode')
            if mode in ('direct', 'relay'):
                connected += 1
                direct += mode == 'direct'
                relay += mode == 'relay'
            if live.get('latency_ms') is not None:
                pings.append(live['latency_ms'])
            path = (t('This PC'), 'ok') if m.get('self') else PATH.get(mode, PATH[None])
            if not m.get('self') and m.get('status') == 'expired':
                path = (t('Offline'), 'unknown')
            values = [t('You') if m.get('self') else t('Member') + ' ' + m['actor'][:6], m['virtual_ip'], t(path[0]),
                      f"{live['latency_ms']:.0f} ms" if live.get('latency_ms') is not None else '—',
                      _ago(live.get('handshake')),
                      f"↓ {_bytes(live.get('rx', 0))}  ↑ {_bytes(live.get('tx', 0))}" if live else '—']
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if c == 0:
                    item.setData(256, m['actor'])
                self.table.setItem(i, c, item)
        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        session_text, _ = SESSION.get(self.session.get('state'), SESSION[None])
        self.tile_session.set(t(session_text))
        self.tile_members.set(str(connected) if self.session.get('state') == 'active' else '—')
        self.tile_paths.set(f'{direct} / {relay}' if self.session.get('state') == 'active' else '—')
        self.tile_ping.set(f'{sum(pings) / len(pings):.0f} ms' if pings else '—')

    def work(self, fn, done):
        self.work_status.setText(t('Working…'))

        def complete(value):
            self.work_status.setText('')
            done(value)

        def failed(exc):
            self.work_status.setText(t(str(exc)))
        if not self.ctl.run('akeno-network', fn, complete, failed):
            self.work_status.setText(t('A network operation is already running.'))

    def create(self):
        name, password = self.room_name.text(), self.create_password.text()
        self.work(lambda: self.lan().create(name, password), self.room_changed)

    def join(self):
        invite, password = self.invite_in.text(), self.join_password.text()
        self.work(lambda: self.lan().join(invite, password), self.room_changed)

    def room_changed(self, result):
        self.create_password.clear()
        self.join_password.clear()
        self.invite_in.clear()
        if result.get('invite'):
            self.invite_out.setText(result['invite'])
        self.members_data = []
        self.show_room()
        self.members()

    def members(self):
        def show(result):
            own = self.identity().load().get('public_key')
            self.members_data = [dict(m, self=m.get('public_key') == own) for m in result['members']]
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
        self.members_data, self.session = [], {}
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
            self.work_status.setText(t('Invitation copied. It is valid for 10 minutes.'))

    def remove_member(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        member = item.data(256) if item else None
        if member and not self.members_data[row].get('self') and \
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
            self.work_status.setText(t('Stopping the LAN session…'))
            self.timer.start()
        except (ValueError, OSError) as exc:
            info(self, t('AKENO Network'), str(exc), error=True)

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
            self.work_status.setText(t('{name} will be reachable for room members.').format(name=Path(path).name))

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
        self.show_room()

    # -- own VPN -----------------------------------------------------------------------------------------------------
    def _vpn_card(self):
        self.vpn_pill = StatusPill(t('Disconnected'), 'unknown')
        card = Card(t('AKENO VPN · own WireGuard profiles'), 'shield', right=self.vpn_pill)
        card.body.addWidget(label(t('Import a WireGuard profile from your own VPN server or provider. AKENO stores it '
                                    'encrypted for your Windows account and connects through the official WireGuard '
                                    'service.'), 'dim', wrap=True))
        self.profiles = QComboBox()
        card.body.addWidget(self.profiles)
        actions = FlowLayout(spacing=8)
        for text, icon, action in ((msg('Import profile…'), 'file', self.import_profile),
                                   (msg('Rename'), 'edit', self.rename_profile), (msg('Remove'), 'trash', self.remove_profile)):
            b = button(t(text), icon, 'ghost' if action != self.import_profile else None, on_click=action)
            b.setEnabled(WINDOWS)
            actions.addWidget(b)
        card.body.addLayout(actions)
        actions = FlowLayout(spacing=8)
        for text, icon, kind, action in ((msg('Connect'), 'power', 'primary', 'connect'),
                                         (msg('Disconnect'), 'stop', None, 'disconnect'),
                                         (msg('Status'), 'activity', 'ghost', 'status'),
                                         (msg('Check exit IP'), 'globe', 'ghost', 'exit_ip')):
            b = button(t(text), icon, kind, on_click=lambda _=False, a=action: self.vpn(a))
            b.setEnabled(BUILT)
            actions.addWidget(b)
        card.body.addLayout(actions)
        self.vpn_grid = KeyValueGrid(130)
        self.vpn_grid.set_rows([(t('Exit IP'), t('Not checked'))])
        card.body.addWidget(self.vpn_grid)
        card.body.addWidget(label(t('Full tunnels need IPv4, IPv6 and a DNS server in the profile. The exit IP is '
                                    'only what one HTTPS check observed; it does not prove that every app uses the '
                                    'tunnel.'), 'muted', wrap=True))
        return card

    def store(self):
        return ProfileStore(sub('network') / 'profiles.sqlite3')

    def refresh_profiles(self):
        self.profiles.clear()
        if not WINDOWS:
            self.profiles.addItem(t('Profiles are stored with Windows DPAPI (Windows only)'))
            self.profiles.setEnabled(False)
            return
        try:
            for p in self.store().metadata():
                self.profiles.addItem(p['name'], p['id'])
        except Exception as exc:
            self.work_status.setText(str(exc))

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
        except Exception as exc:
            info(self, t('AKENO Network'), t(str(exc)), error=True)

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
                info(self, t('AKENO Network'), t(str(exc)), error=True)

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
                self.refresh_profiles()
            except Exception as exc:
                info(self, t('AKENO Network'), t(str(exc)), error=True)

    def vpn(self, action):
        ident = self.profiles.currentData()
        if action == 'connect' and (not ident or not confirm(self, t('Connect VPN'), t(
                'Allow the official WireGuard service to apply this profile and change its routes/DNS? The '
                'connection persists until you disconnect it.'))):
            return
        from akeno.network.helper import invoke

        def show(result):
            text, state = TUNNEL.get(result.get('state'), (str(result.get('state')), 'unknown'))
            self.vpn_pill.set(t(text), state)
            self.tile_vpn.set(t(text))
            rows = [(t('Exit IP'), result['exit_ip'] if result.get('exit_ip') else t('Not checked'))]
            if result.get('last_handshake'):
                rows.append((t('Last handshake'), _ago(result['last_handshake'])))
            self.vpn_grid.set_rows(rows)
        self.work(lambda: invoke(action, ident), show)

    # -- public VPN catalog ------------------------------------------------------------------------------------------
    def _locations_card(self):
        card = Card(t('VPN locations'), 'globe')
        card.body.addWidget(label(t('Public servers from the VPN Gate academic project. Connecting to them is not '
                                    'available yet: their terms, the operators\' logging and the OpenVPN transport '
                                    'still have to be reviewed. Countries appear only when the catalog really lists '
                                    'servers there.'), 'dim', wrap=True))
        line = QHBoxLayout()
        line.setSpacing(8)
        self.countries = QComboBox()
        self.countries.addItem(t('Load the catalog first'))
        self.countries.setEnabled(False)
        line.addWidget(self.countries, 1)
        line.addWidget(button(t('Load catalog'), 'refresh', on_click=self.catalog))
        card.body.addLayout(line)
        self.location_grid = KeyValueGrid(130)
        self.location_grid.set_rows([(t('USA'), t('Not checked')), (t('Turkey'), t('Not checked'))])
        card.body.addWidget(self.location_grid)
        card.body.addWidget(label(t('VPN Gate states that it keeps connection logs for at least three months, that '
                                    'each volunteer server records packet headers for at least two weeks, and that '
                                    'logs are disclosed to police, prosecutors and courts on request.'), 'muted',
                                  wrap=True))
        connect = button(t('Connect (not available yet)'), 'lock')
        connect.setEnabled(False)
        card.body.addWidget(connect)
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
            self.work_status.setText(t('Catalog loaded. Public VPN connections remain disabled.'))
        self.work(load, show)

    # -- diagnostics -------------------------------------------------------------------------------------------------
    def _diagnostics_card(self):
        card = Card(t('Connection diagnostics'), 'stethoscope',
                    right=button(t('Run diagnostics'), 'activity', on_click=self.diagnose))
        self.diag_box = QVBoxLayout()
        self.diag_box.setSpacing(4)
        self.diag_box.addWidget(label(t('Checks what LAN Play and the VPN need on this PC. Read-only: nothing is '
                                        'changed.'), 'dim', wrap=True))
        card.body.addLayout(self.diag_box)
        return card

    def diagnose(self):
        license_ = self.window.license

        def run():
            return diagnostics(license_)

        def show(rows):
            clear_layout(self.diag_box)
            for state, title, detail in rows:
                self.diag_box.addWidget(CheckRow(state, t(title), t(detail)))
        self.work(run, show)


def diagnostics(license_manager):
    """(state, title, detail) rows. Read-only checks; Windows-only facts are reported as such elsewhere."""
    rows = []
    if not WINDOWS:
        return [('warn', msg('Windows required'), msg('LAN Play and the VPN use the official WireGuard service on Windows.'))]
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
    journal = SecureStore(sub('network') / 'lan-journal').load()
    if journal.get('phase') in ('lan_starting', 'lan_active') and state.get('state') != 'active':
        rows.append(('warn', msg('Interrupted LAN session'), msg('Use Recover to remove its adapter and firewall rules.')))
    vpn = SecureStore(sub('network') / 'tunnel').load()
    if vpn.get('phase') == 'recovery_required':
        rows.append(('warn', msg('VPN tunnel needs recovery'), msg('Use Disconnect to remove the interrupted tunnel.')))
    return rows
