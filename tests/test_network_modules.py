"""LAN Play and VPN are switched on separately and never by an install or update."""
import sqlite3

from akeno.components.service import ComponentError, ComponentService, NetworkModules


def test_off_by_default_and_independent(tmp_path):
    m = NetworkModules(tmp_path / 'c.sqlite3')
    assert m.status() == {'lan_play': False, 'vpn': False}
    m.set_enabled('vpn', True)
    assert m.status() == {'lan_play': False, 'vpn': True}
    m.set_enabled('lan_play', True)
    m.set_enabled('vpn', False)
    assert m.status() == {'lan_play': True, 'vpn': False}


def test_shares_the_component_state_without_touching_security_center(tmp_path):
    state = tmp_path / 'c.sqlite3'
    ComponentService(root=tmp_path, state=state, frozen=False)
    NetworkModules(state).set_enabled('lan_play', True)
    with sqlite3.connect(state) as db:
        assert db.execute("SELECT id FROM components").fetchall() == [('network:lan_play',)]


def test_invalid_values_and_damaged_state(tmp_path):
    m = NetworkModules(tmp_path / 'c.sqlite3')
    for bad in (('wallpaper', True), ('vpn', 1), ('vpn', 'yes')):
        try:
            m.set_enabled(*bad)
            raise AssertionError(bad)
        except ComponentError:
            pass
    (tmp_path / 'broken.sqlite3').write_bytes(b'not a database' * 100)
    broken = NetworkModules.__new__(NetworkModules)
    broken.state = tmp_path / 'broken.sqlite3'
    assert broken.enabled('vpn') is False
