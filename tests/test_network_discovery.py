"""LAN discovery forwarding: narrow policy, room-only targets, rate limits, real UDP delivery."""
import socket

import pytest

from akeno.network import discovery as D


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def test_not_released_until_windows_capture_is_verified():
    assert D.AVAILABLE is False
    assert not any(p.tested for p in D.PROFILES.values())      # no compatibility is claimed without a real test


@pytest.mark.parametrize('profile', [
    D.Profile('smb', 'x', (445,)), D.Profile('netbios', 'x', (137,)), D.Profile('low', 'x', (80,)),
    D.Profile('ssdp', 'x', (40000,), group='239.255.255.250'), D.Profile('local', 'x', (40000,), group='224.0.0.1'),
    D.Profile('unicast', 'x', (40000,), group='10.0.0.1'), D.Profile('fast', 'x', (40000,), rate=500)])
def test_policy_refuses_service_ports_and_groups(profile):
    with pytest.raises((D.PolicyError, ValueError)):
        D.validate(profile)


def test_shipped_profiles_are_valid():
    for p in D.PROFILES.values():
        D.validate(p)


def test_copies_go_only_to_room_members_never_outside_or_back():
    sent = []
    peers = ['10.203.5.3', '10.203.5.2', '10.203.6.9', '192.168.1.20', '10.203.5.4']
    f = D.Forwarder('10.203.5.2', lambda: peers, lambda data, to: sent.append(to), [D.PROFILES['openttd']])
    assert f.handle(b'query', '255.255.255.255', 3979) == 2
    assert sent == [('10.203.5.3', 3979), ('10.203.5.4', 3979)]
    assert f.handle(b'query', '255.255.255.255', 445) == 0           # not a profile port
    assert f.handle(b'query', '192.168.1.255', 3979) == 0            # local LAN broadcast is never taken
    assert f.handle(b'x' * 2000, '255.255.255.255', 3979) == 0       # too large
    assert f.handle(b'query', '10.203.5.255', 3979) == 2              # the room's own broadcast address


def test_multicast_profile_and_rate_limit():
    clock, sent = Clock(), []
    profile = D.Profile('mc', 'Game', (4445,), group='224.0.2.60', rate=2, burst=3)
    f = D.Forwarder('10.203.9.2', lambda: ['10.203.9.3'], lambda d, to: sent.append(to), [profile], clock=clock)
    results = [f.handle(b'announce', '224.0.2.60', 4445) for _ in range(5)]
    assert results == [1, 1, 1, 0, 0] and f.stats['limited'] == 2
    clock.now = 1.0                                                  # 2 tokens later
    assert [f.handle(b'announce', '224.0.2.60', 4445) for _ in range(3)] == [1, 1, 0]
    assert f.handle(b'announce', '224.0.2.61', 4445) == 0


def test_only_on_a_room_address():
    with pytest.raises(D.PolicyError):
        D.Forwarder('192.168.1.2', lambda: [], lambda *a: None)


def test_real_udp_delivery_and_unicast_answer():
    """Loopback stand-in for the tunnel: a 'peer' game socket receives the copy and answers the sender by unicast."""
    game = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    game.bind(('127.0.0.1', 0))
    game.settimeout(2)
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    out.bind(('127.0.0.1', 0))
    out.settimeout(2)
    port = game.getsockname()[1]
    profile = D.Profile('t', 'Test', (port,))

    def send(data, to):                     # the session maps the peer's virtual IP to the tunnel; here: loopback
        out.sendto(data, ('127.0.0.1', to[1]))
    try:
        f = D.Forwarder('10.203.7.2', lambda: ['10.203.7.3'], send, [profile])
        assert f.handle(b'WHO-HAS-SERVER', '255.255.255.255', port) == 1
        data, sender = game.recvfrom(2048)
        assert data == b'WHO-HAS-SERVER'
        game.sendto(b'SERVER-HERE', sender)
        assert out.recvfrom(2048)[0] == b'SERVER-HERE'
    finally:
        game.close()
        out.close()
