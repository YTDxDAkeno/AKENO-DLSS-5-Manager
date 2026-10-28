"""Coordinator endpoint proof and relay policy with real WireGuard handshakes (no sockets: handle() only).

Real kernel/WireGuardNT traffic through the coordinator is tested separately (tools/network_e2e, Windows CI).
"""
import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from akeno_server.network.coordinator import Coordinator, Grant
from akeno_server.network import wireguard as wg
from wg_initiator import Member, opaque_transport, response_for, tai64n

A, B, C, X = ('198.51.100.10', 40001), ('203.0.113.20', 40002), ('192.0.2.30', 40003), ('192.0.2.99', 40099)


@pytest.fixture
def world():
    clock, wall = [100.0], [5000.0]
    grants = {}
    coordinator = Coordinator(X25519PrivateKey.generate(), lambda key: grants.get(key),
                              clock=lambda: clock[0], wall=lambda: wall[0])
    yield coordinator, grants, clock, wall
    coordinator.close()


def prove(coordinator, member, source):
    out = coordinator.handle(member.initiation(coordinator.responder.public), source)
    assert len(out) == 1 and out[0][1] == source
    member.consume(out[0][0])
    return out


def two_members(world, room='room-1'):
    coordinator, grants, clock, wall = world
    a, b = Member(), Member()
    grants[a.key] = grants[b.key] = Grant(room, wall[0] + 45)
    prove(coordinator, a, A)
    prove(coordinator, b, B)
    return a, b


def test_handshake_proves_the_endpoint_of_a_live_member_only(world):
    coordinator, grants, clock, wall = world
    member, stranger = Member(), Member()
    grants[member.key] = Grant('room-1', wall[0] + 45)
    prove(coordinator, member, A)
    assert coordinator.endpoint(member.key) == A
    assert coordinator.handle(stranger.initiation(coordinator.responder.public), X) == []
    assert coordinator.endpoint(stranger.key) is None


def test_replayed_initiation_cannot_move_a_proven_endpoint(world):
    coordinator, grants, clock, wall = world
    member = Member()
    grants[member.key] = Grant('room-1', wall[0] + 45)
    first = member.initiation(coordinator.responder.public)
    assert coordinator.handle(first, A)
    assert coordinator.handle(first, X) == []                     # captured and resent from elsewhere
    older = member.initiation(coordinator.responder.public, stamp=tai64n(-60))
    assert coordinator.handle(older, X) == []
    assert coordinator.endpoint(member.key) == A


def test_authenticated_keepalive_follows_roaming_forged_packets_do_not(world):
    coordinator, grants, clock, wall = world
    member = Member()
    grants[member.key] = Grant('room-1', wall[0] + 45)
    prove(coordinator, member, A)
    forged = bytearray(member.keepalive())
    forged[-1] ^= 1
    assert coordinator.handle(bytes(forged), X) == [] and coordinator.endpoint(member.key) == A
    assert coordinator.handle(member.keepalive(), X) == [] and coordinator.endpoint(member.key) == X


def test_relay_forwards_a_handshake_and_data_between_members_of_one_room(world):
    coordinator = world[0]
    a, b = two_members(world)
    init = a.initiation(b.public)                                   # mac1 keyed with B's public key
    assert coordinator.handle(init, A) == [(init, B)]
    reply = response_for(a, b, receiver_index=init[4:8])
    assert coordinator.handle(reply, B) == [(reply, A)]
    to_b, to_a = opaque_transport(reply[4:8]), opaque_transport(init[4:8])
    assert coordinator.handle(to_b, A) == [(to_b, B)]
    assert coordinator.handle(to_a, B) == [(to_a, A)]
    assert coordinator.stats['relayed'] == 4


def test_no_relay_across_rooms_from_unproven_sources_or_to_unknown_indices(world):
    coordinator, grants, clock, wall = world
    a, b = two_members(world)
    c = Member()
    grants[c.key] = Grant('room-2', wall[0] + 45)
    prove(coordinator, c, C)
    assert coordinator.handle(c.initiation(a.public), C) == []         # other room
    assert coordinator.handle(a.initiation(b.public), X) == []         # unproven tuple, valid packet
    assert coordinator.handle(opaque_transport(b'\1\2\3\4'), A) == []  # index never learned
    init = a.initiation(b.public)
    coordinator.handle(init, A)
    assert coordinator.handle(opaque_transport(init[4:8]), C) == []    # C cannot use a room-1 index


def test_revocation_and_lease_expiry_stop_relaying_immediately(world):
    coordinator, grants, clock, wall = world
    a, b = two_members(world)
    init = a.initiation(b.public)
    assert coordinator.handle(init, A)
    coordinator.revoke(b.key)
    grants.pop(b.key)
    assert coordinator.handle(a.initiation(b.public), A) == []
    assert coordinator.handle(response_for(a, b, init[4:8]), B) == []
    assert coordinator.endpoint(b.key) is None
    assert coordinator.handle(b.initiation(coordinator.responder.public), B) == []   # cannot re-prove
    wall[0] += 46                                                       # A's lease ends without renewal
    clock[0] += 2
    assert coordinator.endpoint(a.key) is None


def test_stale_proofs_are_not_handed_out(world):
    coordinator, grants, clock, wall = world
    a, b = two_members(world)
    grants[a.key] = grants[b.key] = Grant('room-1', wall[0] + 3600)
    clock[0] += Coordinator.ENDPOINT_TTL + 1
    assert coordinator.endpoint(a.key) is None
    assert coordinator.handle(a.initiation(b.public), A) == []


def test_malformed_datagrams_rate_limit_and_no_amplification(world):
    coordinator = world[0]
    a, b = two_members(world)
    for junk in (b'', b'\x01' * 147, b'\x04\0\0\0' + b'\0' * 30, b'GET / HTTP/1.1\r\n' * 4, b'\x05\0\0\0' + b'\0' * 60):
        assert coordinator.handle(junk, A) == []
    init = a.initiation(b.public)
    sent = 0
    for _ in range(Coordinator.PACKETS_PER_SECOND + 50):
        out = coordinator.handle(init, A)
        assert len(out) <= 1 and all(len(p) == len(init) for p, _ in out)
        sent += len(out)
    assert sent <= Coordinator.PACKETS_PER_SECOND


def test_control_plane_failure_fails_closed(world):
    coordinator, grants, clock, wall = world
    member = Member()

    def broken(key):
        raise RuntimeError('database unavailable')
    coordinator.authority = broken
    assert coordinator.handle(member.initiation(coordinator.responder.public), A) == []


def test_the_coordinator_replies_with_a_valid_wireguard_response(world):
    coordinator, grants, clock, wall = world
    member = Member()
    grants[member.key] = Grant('room-1', wall[0] + 45)
    prove(coordinator, member, A)                                       # consume() verifies the Noise response
    assert member.send_key is not None and coordinator.handle(member.keepalive(), A) == []
    assert wg.shape(member.keepalive()) == wg.TRANSPORT
