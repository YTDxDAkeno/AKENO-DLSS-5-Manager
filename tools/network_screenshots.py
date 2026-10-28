"""Screenshots of the AKENO Network page in the real themed window, with FIXTURE data (not a real connection).

    python tools/network_screenshots.py OUT_DIR

Writes akeno-network-*-{en,de}-fixture.png: the empty page, an active LAN session with two relayed
member, and diagnostics. The member list, paths and pings are synthetic; real connection evidence is in the CI runs
listed in docs/AKENO_NETWORK_PROGRESS.md.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(out: Path):
    os.environ.setdefault('AKENO_HOME', tempfile.mkdtemp(prefix='akeno-shots-'))
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    from akeno import i18n
    from akeno.app import build_suite
    from akeno.licensing import manager as license_manager
    from akeno.ui import theme
    from akeno.ui.widgets import CheckRow, clear_layout
    from akeno.ui.pages import akeno_network
    # fixture run: no request to the real AKENO server; show the state of a server that offers LAN Play
    akeno_network.AkenoNetworkPage.probe_service = lambda self: None
    from akeno.components.service import NetworkModules
    for module in ('lan_play', 'vpn'):               # the fixture home has both switched on
        NetworkModules().set_enabled(module, True)
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    theme.apply(app, None)

    def settle(ms=250):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        app.processEvents()

    for language in ('en', 'de'):
        i18n.set_language(language)
        ctl, win = build_suite(app, Namespace(smoke_test=True, screenshot=None), license_manager())
        win.resize(1920, 1080)
        win.show()
        win.go('akeno_network')
        page = win.pages['akeno_network']
        settle()
        win.grab().save(str(out / f'akeno-network-empty-{language}-fixture.png'))
        room = 'ab' * 16
        page.current_room = lambda: room
        me, friend, relayed = 'K' * 43 + '=', 'L' * 43 + '=', 'M' * 43 + '='
        page.members_data = [
            {'actor': 'aaaaaaaa', 'public_key': me, 'virtual_ip': '10.203.5.2', 'status': 'registered', 'self': True},
            {'actor': 'b7c1d2e3', 'public_key': friend, 'virtual_ip': '10.203.5.3', 'status': 'registered'},
            {'actor': 'f0e9d8c7', 'public_key': relayed, 'virtual_ip': '10.203.5.4', 'status': 'registered'}]
        now = time.time()
        page.session = {'state': 'active', 'room': room, 'virtual_ip': '10.203.5.2', 'peers': {
            friend: {'mode': 'relay', 'latency_ms': 18.4, 'handshake': now - 12, 'rx': 2_400_000, 'tx': 1_900_000,
                     'virtual_ip': '10.203.5.3'},
            relayed: {'mode': 'relay', 'latency_ms': 41.0, 'handshake': now - 30, 'rx': 650_000, 'tx': 720_000,
                      'virtual_ip': '10.203.5.4'}}}
        page.invite_out.setText('fixture-invitation-code')
        page.room_info = {'name': 'Friday LAN', 'owner': True}
        page.members_data[1]['name'] = 'Mira'
        page.show_room()
        settle()
        win.grab().save(str(out / f'akeno-network-session-{language}-fixture.png'))
        # VPN tab: one own profile (fixture) besides the real target-country list, which has no AKENO servers
        page.profile_meta = [{'id': 'f' * 32, 'name': 'Home server (fixture)'}]
        page.prefs.set_country('f' * 32, 'DE')
        page.render_locations()
        page.show_tab(1)
        settle()
        win.grab().save(str(out / f'akeno-network-vpn-{language}-fixture.png'))
        page.show_tab(2)
        clear_layout(page.diag_box)
        for state, title, detail in (('ok', 'Signed in on this PC', 'This PC is registered with your AKENO account.'),
                                     ('ok', 'AKENO license', 'Active.'),
                                     ('ok', 'WireGuard for Windows', 'Installed and signed by WireGuard LLC.'),
                                     ('ok', 'WireGuard manager service', 'Running.'),
                                     ('ok', 'Windows Defender Firewall (Public)',
                                      'On: members cannot reach file sharing, Remote Desktop or other services.'),
                                     ('ok', 'LAN session', 'Running.')):
            page.diag_box.addWidget(CheckRow(state, i18n.t(title), i18n.t(detail)))
        settle()
        host = page.parentWidget()
        while host is not None and not hasattr(host, 'verticalScrollBar'):
            host = host.parentWidget()
        if host is not None:
            host.verticalScrollBar().setValue(host.verticalScrollBar().maximum())
            settle()
        win.grab().save(str(out / f'akeno-network-diagnostics-{language}-fixture.png'))
        win.close()
        win.deleteLater()
        settle(100)
    print(f'fixture screenshots saved to {out}')


if __name__ == '__main__':
    main(Path(sys.argv[1] if len(sys.argv) > 1 else 'screenshots/network-fixtures'))
