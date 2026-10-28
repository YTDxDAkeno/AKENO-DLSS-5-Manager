"""Relay-only transport lock: ABI layout, filter plan, transaction and ownership rules with a BFE stand-in.

These tests do not prove Windows enforcement; tools/lan_windows_e2e.py sends real encrypted direct datagrams at the
WireGuardNT socket on a Windows runner for that.
"""
import ctypes
import ipaddress
import uuid

import pytest

from akeno.network import wfp
from akeno.network.lan import LanError
from akeno.network.lan_windows import LanSession, recover, tunnel_name

ROOM = 'cd' * 16
NAME = tunnel_name(ROOM)
RELAY = ('45.11.12.13', 3478)

pytestmark = pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason='x64 ABI layout')


def test_structures_match_the_documented_x64_fwpm_layout():
    assert ctypes.sizeof(wfp.GUID) == 16
    assert ctypes.sizeof(wfp.VALUE) == 16
    assert ctypes.sizeof(wfp.FILTER_CONDITION) == 40
    assert wfp.FILTER_CONDITION.conditionValue.offset == 24
    assert ctypes.sizeof(wfp.ACTION) == 20
    assert ctypes.sizeof(wfp.SESSION) == 72
    assert ctypes.sizeof(wfp.PROVIDER_) == 64
    assert ctypes.sizeof(wfp.SUBLAYER_) == 72 and wfp.SUBLAYER_.weight.offset == 64
    offsets = {name: getattr(wfp.FILTER, name).offset for name, _ in wfp.FILTER._fields_}
    assert offsets == {'filterKey': 0, 'displayData': 16, 'flags': 32, 'providerKey': 40, 'providerData': 48,
                       'layerKey': 64, 'subLayerKey': 80, 'weight': 96, 'numFilterConditions': 112,
                       'filterCondition': 120, 'action': 128, 'context': 152, 'reserved': 168, 'filterId': 176,
                       'effectiveWeight': 184}
    assert ctypes.sizeof(wfp.FILTER) == 200


def test_guid_uses_the_windows_mixed_endian_layout():
    value = uuid.UUID('5926dfc8-e3cf-4426-a283-dc393f5d0f9d')
    guid = wfp.GUID.of(value)
    assert (guid.Data1, guid.Data2, guid.Data3) == (0x5926dfc8, 0xe3cf, 0x4426)
    assert bytes(guid.Data4) == bytes.fromhex('a283dc393f5d0f9d')
    assert bytes(guid) == value.bytes_le and guid.uuid() == value


def test_plan_permits_only_the_relay_and_blocks_every_other_v4_v6_peer():
    rules = {rule['rule']: rule for rule in wfp.plan(NAME, 49173, RELAY)}
    assert set(rules) == set(wfp.RULES)
    relay = int(ipaddress.ip_address(RELAY[0]))
    for layer in ('in4', 'out4'):
        permit = rules[f'{layer}-relay']
        assert permit['action'] == wfp.FWP_ACTION_PERMIT and permit['weight'] > rules[f'{layer}-block']['weight']
        assert permit['conditions'] == [(wfp.CONDITION_PROTOCOL, wfp.FWP_UINT8, 17),
                                        (wfp.CONDITION_LOCAL_PORT, wfp.FWP_UINT16, 49173),
                                        (wfp.CONDITION_REMOTE_ADDRESS, wfp.FWP_UINT32, relay),
                                        (wfp.CONDITION_REMOTE_PORT, wfp.FWP_UINT16, 3478)]
    for layer in ('in4', 'out4', 'in6', 'out6'):
        block = rules[f'{layer}-block']
        assert block['action'] == wfp.FWP_ACTION_BLOCK and block['layer'] == layer
        assert block['conditions'] == [(wfp.CONDITION_PROTOCOL, wfp.FWP_UINT8, 17),
                                       (wfp.CONDITION_LOCAL_PORT, wfp.FWP_UINT16, 49173)]
    assert len({rule['key'] for rule in rules.values()}) == 6
    assert all(rule['key'] == wfp.filter_key(NAME, name) for name, rule in rules.items())
    assert wfp.filter_key(tunnel_name('ef' * 16), 'in4-block') != rules['in4-block']['key']


@pytest.mark.parametrize('tunnel,port,relay', [
    ('AkenoVPN' + 'a' * 24, 49173, RELAY), ('AkenoLAN' + 'A' * 24, 49173, RELAY), (NAME, 80, RELAY),
    (NAME, 70000, RELAY), (NAME, '49173', RELAY), (NAME, True, RELAY), (NAME, 49173, ('::1', 3478)),
    (NAME, 49173, ('0.0.0.0', 3478)), (NAME, 49173, ('224.0.0.1', 3478)), (NAME, 49173, ('45.1.2.3', 0)),
    (NAME, 49173, ('45.1.2.3', '3478')), (NAME, 49173, ("45.1.2.3'; evil", 3478))])
def test_plan_rejects_unvalidated_boundaries(tunnel, port, relay):
    with pytest.raises((ValueError, wfp.TransportLockError)):
        wfp.plan(tunnel, port, relay)


class FakeBFE:
    """In-memory Base Filtering Engine with transactions; records every call."""

    def __init__(self, fail_on=None):
        self.filters, self.staged, self.calls, self.alive = {}, None, [], []
        self.provider = self.sublayer = False
        self.sublayer_owner = wfp.PROVIDER
        self.fail_on = fail_on

    def _view(self):
        return self.staged if self.staged is not None else self.filters

    def FwpmEngineOpen0(self, server, authn, identity, session, engine):
        self.calls.append('open')
        assert server is None and authn == wfp.RPC_C_AUTHN_WINNT
        assert session._obj.flags == 0, 'persistent objects need a non-dynamic session'
        engine._obj.value = 1
        return 0

    def FwpmEngineClose0(self, engine):
        self.calls.append('close')

    def FwpmTransactionBegin0(self, engine, flags):
        self.calls.append('begin')
        self.staged = dict(self.filters)
        return 0

    def FwpmTransactionCommit0(self, engine):
        self.calls.append('commit')
        self.filters, self.staged = self.staged, None
        return 0

    def FwpmTransactionAbort0(self, engine):
        self.calls.append('abort')
        self.staged = None
        return 0

    def FwpmProviderAdd0(self, engine, provider, sd):
        item = provider._obj
        assert item.providerKey.uuid() == wfp.PROVIDER and item.flags == wfp.FWPM_PROVIDER_FLAG_PERSISTENT
        assert not self.provider, 'existing registrations are looked up, never re-added'
        self.provider = True
        return 0

    def FwpmSubLayerAdd0(self, engine, sublayer, sd):
        item = sublayer._obj
        assert item.subLayerKey.uuid() == wfp.SUBLAYER and item.providerKey.contents.uuid() == wfp.PROVIDER
        assert item.flags == wfp.FWPM_SUBLAYER_FLAG_PERSISTENT and item.weight == 0xFFFF
        assert not self.sublayer, 'existing registrations are looked up, never re-added'
        self.sublayer = True
        return 0

    def FwpmProviderGetByKey0(self, engine, key, out):
        assert key._obj.uuid() == wfp.PROVIDER
        if not self.provider:
            return wfp.FWP_E_PROVIDER_NOT_FOUND
        item = wfp.PROVIDER_(wfp.GUID.of(wfp.PROVIDER))
        self.alive.append(item)
        out._obj.contents = item
        return 0

    def FwpmSubLayerGetByKey0(self, engine, key, out):
        assert key._obj.uuid() == wfp.SUBLAYER
        if not self.sublayer:
            return wfp.FWP_E_SUBLAYER_NOT_FOUND
        owner = wfp.GUID.of(self.sublayer_owner)
        item = wfp.SUBLAYER_(wfp.GUID.of(wfp.SUBLAYER), providerKey=ctypes.pointer(owner))
        self.alive.append((item, owner))
        out._obj.contents = item
        return 0

    def FwpmFilterAdd0(self, engine, item, sd, identifier):
        item = item._obj
        key = item.filterKey.uuid()
        self.calls.append(('add', key))
        if self.fail_on == 'add' and len([c for c in self.calls if isinstance(c, tuple) and c[0] == 'add']) == 4:
            return 0x80320011
        if key in self._view():
            return wfp.FWP_E_ALREADY_EXISTS
        conditions = [(c.fieldKey.uuid(), c.conditionValue.type,
                       {1: c.conditionValue.value.uint8, 2: c.conditionValue.value.uint16,
                        3: c.conditionValue.value.uint32}[c.conditionValue.type])
                      for c in item.filterCondition[:item.numFilterConditions]]
        self._view()[key] = {'provider': item.providerKey.contents.uuid(), 'flags': item.flags,
                             'layer': item.layerKey.uuid(), 'sublayer': item.subLayerKey.uuid(),
                             'action': item.action.type, 'weight': item.weight.value.uint8, 'conditions': conditions,
                             'name': item.displayData.name}
        return 0

    def FwpmFilterGetByKey0(self, engine, key, out):
        stored = self._view().get(key._obj.uuid())
        if stored is None:
            return wfp.FWP_E_FILTER_NOT_FOUND
        item = wfp.FILTER()
        provider = wfp.GUID.of(stored['provider'])
        item.providerKey = ctypes.pointer(provider)
        item.flags = stored['flags']
        item.subLayerKey = wfp.GUID.of(stored['sublayer'])
        self.alive.append((item, provider))
        out._obj.contents = item
        return 0

    def FwpmFilterDeleteByKey0(self, engine, key):
        self.calls.append(('delete', key._obj.uuid()))
        return 0 if self._view().pop(key._obj.uuid(), None) else wfp.FWP_E_FILTER_NOT_FOUND

    def FwpmFreeMemory0(self, pointer):
        pass


def test_install_is_one_transaction_with_persistent_owned_filters_and_is_verified():
    bfe = FakeBFE()
    lock = wfp.TransportLock(NAME, api=bfe)
    assert not lock.verify()
    assert lock.install(49173, RELAY) == 6
    assert bfe.calls.count('begin') == bfe.calls.count('commit') == 1 and 'abort' not in bfe.calls
    assert len(bfe.filters) == 6 and lock.verify()
    for key, item in bfe.filters.items():
        assert item['provider'] == wfp.PROVIDER and item['sublayer'] == wfp.SUBLAYER
        assert item['flags'] & wfp.FWPM_FILTER_FLAG_PERSISTENT and NAME in item['name']
    relay = [item for item in bfe.filters.values() if item['action'] == wfp.FWP_ACTION_PERMIT]
    assert {item['layer'] for item in relay} == {wfp.LAYERS['in4'], wfp.LAYERS['out4']}
    assert all((wfp.CONDITION_REMOTE_ADDRESS, wfp.FWP_UINT32, int(ipaddress.ip_address(RELAY[0])))
               in item['conditions'] for item in relay)


def test_reinstall_replaces_atomically_without_a_moment_of_missing_filters():
    bfe = FakeBFE()
    lock = wfp.TransportLock(NAME, api=bfe)
    lock.install(49173, RELAY)
    bfe.calls.clear()
    lock.install(50001, ('45.11.12.99', 3479))
    # Deletes and adds happen inside one transaction: other BFE clients only see the old or the new set.
    begin, commit = bfe.calls.index('begin'), bfe.calls.index('commit')
    changes = [i for i, call in enumerate(bfe.calls) if isinstance(call, tuple)]
    assert changes and all(begin < i < commit for i in changes)
    ports = {value for item in bfe.filters.values() for field, _, value in item['conditions']
             if field == wfp.CONDITION_LOCAL_PORT}
    assert ports == {50001} and len(bfe.filters) == 6


def test_failed_install_aborts_and_leaves_no_partial_lock():
    bfe = FakeBFE(fail_on='add')
    lock = wfp.TransportLock(NAME, api=bfe)
    with pytest.raises(wfp.TransportLockError, match='transport lock'):
        lock.install(49173, RELAY)
    assert 'abort' in bfe.calls and 'commit' not in bfe.calls and bfe.filters == {}
    assert bfe.calls[-1] == 'close'


def test_remove_deletes_only_this_tunnels_akeno_filters():
    bfe = FakeBFE()
    other = wfp.TransportLock(tunnel_name('ef' * 16), api=bfe)
    other.install(50002, RELAY)
    lock = wfp.TransportLock(NAME, api=bfe)
    lock.install(49173, RELAY)
    foreign = uuid.uuid4()
    bfe.filters[foreign] = {'provider': uuid.uuid4(), 'flags': 1, 'sublayer': uuid.uuid4()}
    assert lock.remove() == 6 and not lock.verify()
    assert other.verify() and foreign in bfe.filters
    assert lock.remove() == 0


def test_a_foreign_filter_under_an_akeno_key_is_never_deleted():
    bfe = FakeBFE()
    lock = wfp.TransportLock(NAME, api=bfe)
    lock.install(49173, RELAY)
    key = wfp.filter_key(NAME, 'out6-block')
    bfe.filters[key] = dict(bfe.filters[key], provider=uuid.uuid4())
    assert not lock.verify()
    with pytest.raises(wfp.TransportLockError, match='not owned'):
        lock.remove()
    assert 'abort' in bfe.calls and len(bfe.filters) == 6


def test_a_foreign_sublayer_under_the_akeno_key_stops_installation():
    bfe = FakeBFE()
    bfe.provider = bfe.sublayer = True
    bfe.sublayer_owner = uuid.uuid4()
    with pytest.raises(wfp.TransportLockError, match='another provider'):
        wfp.TransportLock(NAME, api=bfe).install(49173, RELAY)
    assert 'abort' in bfe.calls and bfe.filters == {}


def test_lock_requires_windows_off_windows():
    if wfp.sys.platform == 'win32':
        pytest.skip('native Windows has the API')
    with pytest.raises(wfp.TransportLockError, match='Windows'):
        wfp.TransportLock(NAME).verify()


class Store:
    def __init__(self, value=None):
        self.value = value or {}
        self.history = []

    def load(self):
        return dict(self.value)

    def save(self, value):
        self.value = value
        self.history.append(dict(value))


class Control:
    def refresh(self):
        return {'self': {'public_key': 'A' * 43 + '=', 'private_key': 'B' * 43 + '=', 'virtual_ip': '10.203.5.2'},
                'coordinator': {'host': '127.0.0.1', 'port': 3478, 'public_key': 'C' * 43 + '='}, 'peers': []}


class Firewall:
    def __init__(self, *args):
        pass

    def require_enabled(self):
        pass

    def relay_only(self, port, relay):
        pass

    def apply(self):
        pass

    def remove(self):
        pass


def session(events, lock_type, stop_after=1):
    class Native:
        def start(self, name, text):
            events.append('tunnel')
            raise RuntimeError('stop the fixture after the tunnel start')

        def stop(self, name, public_key=None):
            events.append('tunnel-stop')
    journal, status = Store(), Store()
    return LanSession(ROOM, Control(), Native(), journal, status, lambda: False, firewall=Firewall,
                      transport_lock=lock_type), journal, status


def test_session_installs_and_verifies_the_lock_before_the_tunnel_starts():
    events, bfe = [], FakeBFE()

    class Lock(wfp.TransportLock):
        def __init__(self, name):
            super().__init__(name, api=bfe)

        def install(self, port, relay):
            events.append(('lock', port, relay))
            return super().install(port, relay)
    lan, journal, _ = session(events, Lock)
    with pytest.raises(RuntimeError, match='fixture'):
        lan.run()
    assert events[0][0] == 'lock' and events[0][2] == ('127.0.0.1', 3478) and events[1] == 'tunnel'
    assert events[-1] == 'tunnel-stop' and bfe.filters == {}
    assert journal.history[0]['phase'] == 'lan_starting' and journal.history[0]['listen_port'] == events[0][1]


def test_unverifiable_lock_never_starts_the_tunnel():
    events = []

    class Lock:
        def __init__(self, name):
            pass

        def install(self, port, relay):
            events.append('lock')

        def verify(self):
            return False

        def remove(self):
            events.append('unlock')
    lan, _, status = session(events, Lock)
    with pytest.raises(LanError, match='IP protection'):
        lan.run()
    assert 'tunnel' not in events and events[-1] == 'unlock'
    assert not any(item.get('state') == 'active' for item in status.history)


def test_recovery_removes_tunnel_rules_and_lock_in_that_order():
    calls = []

    class Native:
        def stop(self, name, public_key=None):
            calls.append(('stop', name))

    class Lock:
        def __init__(self, name):
            calls.append(('lock', name))

        def remove(self):
            calls.append('unlock')
    journal = Store({'phase': 'lan_active', 'name': NAME, 'public_key': 'k'})
    recover(Native(), journal, run=lambda script: calls.append('rules'), transport_lock=Lock)
    assert calls == [('stop', NAME), 'rules', ('lock', NAME), 'unlock'] and journal.value == {'phase': 'disconnected'}
