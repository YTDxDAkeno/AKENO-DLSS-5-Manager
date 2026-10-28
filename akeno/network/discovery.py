"""Room-scoped forwarding of LAN game discovery (broadcast/multicast) — policy and forwarder core.

Many games find LAN servers by sending a UDP broadcast (255.255.255.255) or a multicast datagram and listening
for answers. Inside an AKENO room those packets would never reach the other members, because the room is a routed
WireGuard network, not a shared Ethernet segment. This module turns such a datagram into unicast copies, one per
room member, sent from this PC's virtual IP through the tunnel. Answers come back as ordinary unicast UDP.

It is deliberately narrow (no bridging of local networks):

* only UDP, only destination ports of profiles the user enabled for the room, only to the broadcast address or the
  profile's multicast group;
* only to the room's own virtual addresses (10.203.<room>.0/24), never to the local network or the internet;
* size limit per datagram and a token bucket per profile (packets and bytes per second);
* never ports of Windows services (NetBIOS, SMB, SSDP, LLMNR, mDNS, RDP ...), whatever a profile says.

How datagrams are captured on Windows (a socket on the profile port, or WinIPBroadcast-style re-sending) is not part
of this module and **not released**: ``AVAILABLE`` stays False until the capture has been verified on real Windows
PCs with real games. The policy and forwarder below are tested with real UDP sockets.
"""
from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass

AVAILABLE = False            # Windows capture is not verified yet; the UI must not offer automatic discovery

BLOCKED_PORTS = frozenset({137, 138, 139, 445, 1900, 5355, 5353, 3389, 135, 53, 67, 68, 123, 161, 162, 500, 4500})
MAX_DATAGRAM = 1400          # fits the WireGuard MTU (1420) with room to spare
ROOM_NETWORK = ipaddress.ip_network('10.203.0.0/16')


@dataclass(frozen=True)
class Profile:
    """Discovery of one game: its UDP ports and, if it uses one, its multicast group."""
    id: str
    name: str
    ports: tuple[int, ...]
    group: str | None = None
    rate: float = 10.0       # datagrams per second
    burst: int = 20
    bytes_per_second: int = 32_000
    tested: bool = False     # True only with a recorded real test (docs/AKENO_LAN_COMPATIBILITY.md)


# Known discovery ports. "tested" means a recorded real test over AKENO LAN; the others are documented by the game
# and still need a real test before they may be called compatible.
PROFILES = {
    'openttd': Profile('openttd', 'OpenTTD', (3979,), tested=False),
    'minecraft-java': Profile('minecraft-java', 'Minecraft: Java Edition (Open to LAN)', (4445,), group='224.0.2.60'),
    'factorio': Profile('factorio', 'Factorio', (34197,)),
    'terraria': Profile('terraria', 'Terraria', (8888,)),
}


class PolicyError(ValueError):
    pass


def validate(profile: Profile) -> Profile:
    if not profile.ports or any(not 1024 <= p <= 65535 or p in BLOCKED_PORTS for p in profile.ports):
        raise PolicyError('Discovery ports must be game ports (1024-65535), never Windows service ports.')
    if profile.group is not None:
        group = ipaddress.ip_address(profile.group)
        if not group.is_multicast or group in ipaddress.ip_network('224.0.0.0/24') \
                or str(group) in ('239.255.255.250', '224.0.0.251', '224.0.0.252'):
            raise PolicyError('Only a game multicast group can be forwarded (no SSDP, mDNS or LLMNR).')
    if not 0 < profile.rate <= 50 or not 0 < profile.burst <= 100 or not 0 < profile.bytes_per_second <= 256_000:
        raise PolicyError('Discovery rate limits are out of range.')
    return profile


class Bucket:
    def __init__(self, rate, burst, byte_rate, clock=time.monotonic):
        self.rate, self.burst, self.byte_rate, self.clock = rate, burst, byte_rate, clock
        self.tokens, self.bytes, self.at = float(burst), float(byte_rate), clock()

    def take(self, size):
        now = self.clock()
        elapsed, self.at = now - self.at, now
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.bytes = min(self.byte_rate, self.bytes + elapsed * self.byte_rate)
        if self.tokens < 1 or self.bytes < size:
            return False
        self.tokens -= 1
        self.bytes -= size
        return True


class Forwarder:
    """Decides for each captured datagram whether and to whom it is copied.

    ``send(payload, (virtual_ip, port))`` is the injected unicast sender (a UDP socket bound to this PC's virtual IP
    in the real session). ``peers`` returns the virtual IPs of the room's *verified* members (signed credentials)."""

    def __init__(self, own_ip, peers, send, profiles=(), clock=time.monotonic):
        self.own = ipaddress.ip_address(own_ip)
        if self.own not in ROOM_NETWORK:
            raise PolicyError('The forwarder only runs on an AKENO room address.')
        self.room = ipaddress.ip_network(f'{self.own}/24', strict=False)
        self.peers, self.send, self.clock = peers, send, clock
        self.profiles, self.buckets = {}, {}
        self.stats = {'forwarded': 0, 'dropped': 0, 'limited': 0}
        for p in profiles:
            self.enable(p)

    def enable(self, profile):
        validate(profile)
        self.profiles[profile.id] = profile
        self.buckets[profile.id] = Bucket(profile.rate, profile.burst, profile.bytes_per_second, self.clock)

    def disable(self, profile_id):
        self.profiles.pop(profile_id, None)
        self.buckets.pop(profile_id, None)

    def _profile(self, destination, port):
        for p in self.profiles.values():
            if port in p.ports and (destination == '255.255.255.255' or destination == p.group
                                    or destination == str(self.room.broadcast_address)):
                return p
        return None

    def handle(self, payload: bytes, destination: str, port: int) -> int:
        """Forwards one locally captured datagram; returns the number of copies sent."""
        profile = self._profile(destination, port)
        if profile is None or not payload or len(payload) > MAX_DATAGRAM:
            self.stats['dropped'] += 1
            return 0
        if not self.buckets[profile.id].take(len(payload)):
            self.stats['limited'] += 1
            return 0
        sent = 0
        for peer in self.peers():
            address = ipaddress.ip_address(peer)
            if address == self.own or address not in self.room:
                continue                  # never itself, never outside this room's /24
            self.send(payload, (str(address), port))
            sent += 1
        self.stats['forwarded'] += sent
        return sent
