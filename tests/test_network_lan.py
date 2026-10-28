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


def credential(signer, public_key, ip, endpoint=None, room=ROOM, life=45, now=None):
    key, kid, _ = signer
    now = int(now or time.time())
    payload = {'iss': 'akeno-lan', 'aud': 'akeno-lan-peer', 'sub': str(uuid.uuid4()), 'device_id': str(uuid.uuid4()),
               'room_id': room, 'public_key': public_key, 'virtual_ip': ip, 'iat': now, 'exp': now + life,
               'nonce': secrets.token_urlsafe(16), 'endpoint': endpoint}
    return {'credential': sign(key, kid, 'akeno-lan-peer', payload)}


def test_only_individually_signed_room_members_become_peers(signer):
    own, a, b = wg_key(), wg_key(), wg_key()
    forger = (Ed25519PrivateKey.generate(), signer[1], None)
    result = {'peers': [
        credential(signer, a, '10.203.5.3', ['45.1.2.3', 51820]),
        credential(signer, own, '10.203.5.2'),                         # this PC itself
        credential(forger, b, '10.203.5.4'),                           # not signed by a trusted key
        credential(signer, b, '10.203.5.5', room='b' * 32),            # another room
        credential(signer, b, '10.203.5.6', life=46),                  # longer than a lease
        credential(signer, b, '10.203.5.7', ['192.168.1.10', 51820]),  # private endpoint
        credential(signer, b, '10.203.0.1'),                           # the coordinator's address
        {'credential': 'garbage'}, {}]}
    peers = verified_peers(result, signer[2], ROOM, own)
    assert [(p['public_key'], p['virtual_ip'], p['endpoint']) for p in peers] == [(a, '10.203.5.3', ('45.1.2.3', 51820))]


def test_expired_credentials_and_duplicates_are_ignored(signer):
    own, a = wg_key(), wg_key()
    old = credential(signer, a, '10.203.5.3', now=time.time() - 60)
    dup = [credential(signer, a, '10.203.5.3'), credential(signer, a, '10.203.5.4')]
    assert verified_peers({'peers': [old]}, signer[2], ROOM, own) == []
    assert len(verified_peers({'peers': dup}, signer[2], ROOM, own)) == 1


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


def test_direct_first_then_relay_after_the_grace_period():
    c, wg, clock = controller()
    a = wg_key()
    c.apply([peer(a, clock=clock)])
    assert wg.peers_[a]['endpoint'] == ('45.1.2.3', 51820) and c.tick()[a]['mode'] == 'direct_trying'
    clock[0] += PathController.DIRECT_GRACE + 1
    c.apply([peer(a, clock=clock)])
    assert c.tick()[a]['mode'] == 'relay_trying' and wg.peers_[a]['endpoint'] == RELAY
    clock[0] += 1
    wg.handshake(a)
    assert c.tick()[a]['mode'] == 'relay'


def test_mode_is_what_wireguard_reports():
    c, wg, clock = controller()
    a, b = wg_key(), wg_key()
    c.apply([peer(a, clock=clock), peer(b, '10.203.5.4', clock=clock)])
    clock[0] += 1
    wg.handshake(a)
    wg.handshake(b, via=RELAY)          # B reached us through the relay: WireGuard roamed to it
    state = c.tick()
    assert state[a]['mode'] == 'direct' and state[b]['mode'] == 'relay'


def test_a_moved_member_gets_a_new_handshake_on_its_new_endpoint():
    c, wg, clock = controller()
    a = wg_key()
    c.apply([peer(a, clock=clock)])
    clock[0] += 1
    wg.handshake(a)
    assert c.tick()[a]['mode'] == 'direct'
    clock[0] += 30
    c.apply([peer(a, endpoint=('45.1.2.3', 51821), clock=clock)])
    # The old session would still look fresh: the peer is re-added so only a new handshake counts.
    assert a in wg.removed and wg.peers_[a]['latest_handshake'] is None
    assert c.tick()[a]['mode'] == 'direct_trying' and wg.peers_[a]['endpoint'] == ('45.1.2.3', 51821)
    clock[0] += 1
    wg.handshake(a)
    assert c.tick()[a]['mode'] == 'direct'


def test_lost_connection_is_retried_direct_first():
    c, wg, clock = controller()
    a = wg_key()
    c.apply([peer(a, clock=clock, expires=clock[0] + 10_000)])
    clock[0] += 1
    wg.handshake(a, via=RELAY)
    assert c.tick()[a]['mode'] == 'relay'
    clock[0] += PathController.STALE + 1
    assert c.tick()[a]['mode'] == 'direct_trying' and wg.peers_[a]['endpoint'] == ('45.1.2.3', 51820)


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
