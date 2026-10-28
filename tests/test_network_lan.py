"""LAN Play client logic with real Ed25519 credentials and a WireGuard stand-in (no tunnel, NAT or game traffic).

Real packets through real NAT and WireGuard are tested by tools/network_e2e/nat_lab.py.
"""
import base64
import json
import os
import secrets
import time
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from akeno.licensing import crypto
from akeno.network.lan import LanError, PathController, coordinator_of, interface_profile, verified_peers

ROOM = 'a' * 32
RELAY = ('11.203.0.1', 3478)


def wg_key():
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def signer():
    key = Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    b64 = base64.b64encode(raw).decode()
    kid = crypto.kid_for(b64)
    return key, kid, {kid: crypto.public_key(b64)}


def sign(private, kid, typ, payload):
    """The server's token format (server/akeno_server/licensing/signing.py); the app itself can only verify."""
    enc = lambda data: base64.urlsafe_b64encode(data).rstrip(b'=').decode()
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    head = enc(canonical({'alg': 'EdDSA', 'crv': 'Ed25519', 'kid': kid, 'typ': typ}))
    body = enc(canonical(payload))
    return f"{head}.{body}.{enc(private.sign(f'{head}.{body}'.encode('ascii')))}"


def credential(signer, public_key, ip, endpoint=None, room=ROOM, life=45, now=None, relay=None):
    key, kid, _ = signer
    now = int(now or time.time())
    payload = {'iss': 'akeno-lan', 'aud': 'akeno-lan-peer', 'sub': str(uuid.uuid4()), 'device_id': str(uuid.uuid4()),
               'room_id': room, 'public_key': public_key, 'virtual_ip': ip, 'iat': now, 'exp': now + life,
               'nonce': secrets.token_urlsafe(16), 'transport':'relay-only','relay':relay}
    if endpoint is not None: payload['endpoint']=endpoint
    return {'credential': sign(key, kid, 'akeno-lan-peer', payload)}


def test_only_individually_signed_room_members_become_peers(signer):
    own, a, b = wg_key(), wg_key(), wg_key()
    forger = (Ed25519PrivateKey.generate(), signer[1], None)
    result = {'peers': [
        credential(signer, a, '10.203.5.3'),
        credential(signer, own, '10.203.5.2'),                         # this PC itself
        credential(forger, b, '10.203.5.4'),                           # not signed by a trusted key
        credential(signer, b, '10.203.5.5', room='b' * 32),            # another room
        credential(signer, b, '10.203.5.6', life=46),                  # longer than a lease
        credential(signer, b, '10.203.5.7', ['192.168.1.10', 51820]),  # private endpoint
        credential(signer, b, '10.203.0.1'),                           # the coordinator's address
        {'credential': 'garbage'}, {}]}
    peers = verified_peers(result, signer[2], ROOM, own)
    assert [(p['public_key'], p['virtual_ip']) for p in peers] == [(a, '10.203.5.3')]
    assert all('endpoint' not in p for p in peers)


def test_expired_credentials_and_duplicates_are_ignored(signer):
    own, a = wg_key(), wg_key()
    old = credential(signer, a, '10.203.5.3', now=time.time() - 60)
    dup = [credential(signer, a, '10.203.5.3'), credential(signer, a, '10.203.5.4')]
    assert verified_peers({'peers': [old]}, signer[2], ROOM, own) == []
    assert len(verified_peers({'peers': dup}, signer[2], ROOM, own)) == 1


def test_signed_peer_cannot_be_spliced_into_another_relay_configuration(signer):
    own,a=wg_key(),wg_key()
    relay={'public_key':wg_key(),'host':'relay.akeno.example','port':3478}
    result={'coordinator':relay,'peers':[credential(signer,a,'10.203.5.3',relay=relay)]}
    assert len(verified_peers(result,signer[2],ROOM,own))==1
    result['coordinator']={**relay,'host':'attacker.example'}
    assert verified_peers(result,signer[2],ROOM,own)==[]


@pytest.mark.parametrize('host',['relay.example\nPrivateKey = evil','relay.example;cmd','relay example','a..b',':bad:'])
def test_coordinator_host_cannot_inject_wireguard_configuration(host):
    with pytest.raises(LanError):
        coordinator_of({'coordinator':{'public_key':wg_key(),'host':host,'port':3478}})


def test_interface_profile_has_the_coordinator_as_its_only_static_peer():
    from akeno.network.profiles import parse_wireguard
    coord = coordinator_of({'coordinator': {'public_key': wg_key(), 'host': 'lan.akeno.online', 'port': 3478}})
    text = interface_profile(wg_key(), '10.203.5.2', coord)
    p = parse_wireguard(text)
    assert p.routes == ('10.203.0.1/32',) and p.addresses == ('10.203.5.2/24',) and p.routing == 'custom'
    assert p.endpoint == 'lan.akeno.online:3478'
    with pytest.raises(LanError):
        interface_profile(wg_key(), '192.168.0.2', coord)
    with pytest.raises(LanError):
        coordinator_of({'coordinator': {'public_key': 'x', 'host': 'h', 'port': 1}})


class FakeWireGuard:
    """Peers as `wg` shows them. Handshakes happen only when the test says the path works."""

    def __init__(self, clock):
        self.clock, self.peers_ = clock, {}
        self.removed = []

    def set_peer(self, key, endpoint, ip):
        self.peers_[key] = {'endpoint': endpoint, 'latest_handshake': None, 'rx': 0, 'tx': 0, 'ip': ip}

    def remove_peer(self, key):
        if self.peers_.pop(key, None) is not None:
            self.removed.append(key)

    def peers(self):
        return {k: dict(v) for k, v in self.peers_.items()}

    def handshake(self, key, via=None):
        p = self.peers_[key]
        p['latest_handshake'] = int(self.clock[0])
        if via:
            p['endpoint'] = via


def controller():
    clock = [1000.0]
    wg = FakeWireGuard(clock)
    return PathController(wg, RELAY, '10.203.5.0/24', clock=lambda: clock[0]), wg, clock


def peer(key, ip='10.203.5.3', endpoint=('45.1.2.3', 51820), expires=None, clock=None):
    return {'public_key': key, 'virtual_ip': ip, 'endpoint': endpoint, 'expires': expires or clock[0] + 45}


def test_only_relay_is_tried_even_when_a_caller_supplies_an_endpoint():
    c, wg, clock = controller()
    a = wg_key()
    c.apply([peer(a, clock=clock)])
    assert wg.peers_[a]['endpoint'] == RELAY and c.tick()[a]['mode'] == 'relay_trying'
    clock[0] += 1
    wg.handshake(a)
    assert c.tick()[a]['mode'] == 'relay'
    clock[0] += PathController.STALE + 1
    c.apply([peer(a, clock=clock)])
    assert c.tick()[a]['mode'] == 'relay_trying' and wg.peers_[a]['endpoint'] == RELAY


def test_wireguard_roaming_to_a_direct_endpoint_is_refused_and_reset():
    c, wg, clock = controller()
    a = wg_key()
    c.apply([peer(a, clock=clock)])
    clock[0] += 1
    wg.handshake(a, via=('45.1.2.3', 51820))
    state = c.tick()
    assert state[a]['mode'] == 'relay_trying' and wg.peers_[a]['endpoint'] == RELAY
    assert 'endpoint' not in state[a]


def test_peer_endpoint_changes_are_never_followed():
    c, wg, clock = controller()
    a = wg_key()
    c.apply([peer(a, clock=clock)])
    clock[0] += 1
    wg.handshake(a)
    assert c.tick()[a]['mode'] == 'relay'
    clock[0] += 30
    c.apply([peer(a, endpoint=('45.1.2.3', 51821), clock=clock)])
    assert wg.peers_[a]['endpoint'] == RELAY and c.tick()[a]['mode'] == 'relay'


def test_lost_relay_connection_never_falls_back_to_a_direct_path():
    c, wg, clock = controller()
    a = wg_key()
    c.apply([peer(a, clock=clock, expires=clock[0] + 10_000)])
    clock[0] += 1
    wg.handshake(a)
    assert c.tick()[a]['mode'] == 'relay'
    clock[0] += PathController.STALE + 1
    assert c.tick()[a]['mode'] == 'relay_trying' and wg.peers_[a]['endpoint'] == RELAY


def test_a_silent_relay_path_is_retried_without_exposing_a_peer_endpoint():
    c, wg, clock = controller()
    a = wg_key()
    c.apply([peer(a, clock=clock, expires=clock[0] + 10_000)])
    clock[0] += 1
    wg.handshake(a)
    wg.peers_[a]['rx'] = 148
    assert c.tick()[a]['mode'] == 'relay'
    clock[0] += PathController.SILENT + 1
    c.apply([peer(a, clock=clock, expires=clock[0] + 10_000)])
    assert c.tick()[a]['mode'] == 'relay_trying' and a in wg.removed
    assert wg.peers_[a]['endpoint'] == RELAY


def test_lease_expiry_removal_and_subnet_enforcement():
    c, wg, clock = controller()
    a, b, outsider = wg_key(), wg_key(), wg_key()
    c.apply([peer(a, clock=clock, expires=clock[0] + 5), peer(b, '10.203.5.4', clock=clock),
             peer(outsider, '10.203.9.9', clock=clock)])
    assert set(wg.peers_) == {a, b}                          # another room's subnet never becomes a peer
    clock[0] += 6
    c.tick()
    assert a not in wg.peers_ and b in wg.peers_             # lease not renewed: removed without the server
    c.apply([])
    assert wg.peers_ == {}                                   # removed from the room / banned


def test_member_without_a_proven_endpoint_goes_straight_to_the_relay():
    c, wg, clock = controller()
    a = wg_key()
    c.apply([peer(a, endpoint=None, clock=clock)])
    assert wg.peers_[a]['endpoint'] == RELAY and c.tick()[a]['mode'] == 'relay_trying'


def test_room_subnet_must_be_inside_the_lan_range():
    with pytest.raises(LanError):
        PathController(FakeWireGuard([0]), RELAY, '192.168.0.0/24')
