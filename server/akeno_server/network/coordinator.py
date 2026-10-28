"""AKENO LAN coordinator: proven WireGuard endpoints and a WireGuard-aware opaque relay on one UDP port.

Endpoint proof. Every room member's WireGuard (WireGuardNT on Windows) has the coordinator as one extra peer whose
AllowedIPs is the unused 10.203.0.1/32. Its ordinary handshake to the coordinator proves possession of the member's
registered private key (Noise IK authenticates the static key; the TAI64N timestamp stops replays), and the source
of that packet is the NAT mapping of the member's own WireGuard socket. Authenticated keepalives keep the proof
fresh and follow roaming. No request body ever names an endpoint.

Relay. The same socket forwards datagrams between proven endpoints of members of the same room. The destination
is derived from the WireGuard header only: an initiation's mac1 is keyed with its responder's public key, responses,
cookie replies and data carry a receiver index learned from forwarded handshakes. Member traffic stays end-to-end
encrypted; the coordinator never answers for another key, sends at most the one datagram it received, checks
format, size and rate, and drops a member the moment its membership or lease ends.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from akeno_server.network import wireguard as wg

COORDINATOR_ADDRESS = '10.203.0.1'


@dataclass(frozen=True)
class Grant:
    """A live membership as the control plane sees it: the room and the end of the member's lease (wall clock)."""
    room: str
    expires: float


class Coordinator:
    ENDPOINT_TTL = 60          # members refresh every 25 s (PersistentKeepalive); stale proofs are not handed out
    INDEX_TTL = 200            # WireGuard rekeys every 120 s and drops sessions after 180 s
    GRANT_CACHE = 1.0          # the control plane is asked at least once a second per key
    PACKETS_PER_SECOND = 400   # per source endpoint
    BYTES_PER_SECOND = 512_000 # per proven or unproven source; limits do not multiply across destination members
    GLOBAL_PACKETS_PER_SECOND = 8_000
    GLOBAL_BYTES_PER_SECOND = 8_000_000
    MAX_SOURCES = 16_384
    MAX_GRANTS = 8_192
    MAX_ENDPOINTS = 4_096
    MAX_INDICES = 8192

    def __init__(self, private_key: X25519PrivateKey, authority, bind=('127.0.0.1', 0), clock=time.monotonic,
                 wall=time.time):
        if not callable(authority):
            raise ValueError('A live membership authority is required.')
        self.authority, self.clock, self.wall = authority, clock, wall
        self.responder = wg.Responder(private_key, self._accept, clock)
        self.endpoints: dict[bytes, tuple[tuple, float]] = {}
        self.by_endpoint: dict[tuple, bytes] = {}
        self.indices: dict[tuple[str, bytes], tuple[bytes, float]] = {}
        self._grants: dict[bytes, tuple[Grant | None, float]] = {}
        self._mac1: dict[bytes, bytes] = {}
        self._rate: dict[tuple, tuple[int, int, int]] = {}
        self._global_rate = (int(clock()), 0, 0)
        self.stats = {'proofs': 0, 'relayed': 0, 'dropped': 0}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        family = socket.AF_INET6 if ':' in bind[0] else socket.AF_INET
        self.sock = socket.socket(family, socket.SOCK_DGRAM)
        try:
            self.sock.bind(bind)
            self.sock.settimeout(0.2)
        except Exception:
            self.sock.close()
            raise

    # -- control plane --------------------------------------------------------------------------------------------
    @property
    def public_key(self) -> str:
        return wg.b64(self.responder.public)

    @property
    def address(self) -> tuple:
        return self.sock.getsockname()[:2]

    def endpoint(self, public_key: str) -> tuple[str, int] | None:
        """The proven, fresh public endpoint of a member with a live grant, or None."""
        try:
            public = wg.key_bytes(public_key)
        except ValueError:
            return None
        with self._lock:
            found = self._fresh(public)
            return (found[0], found[1]) if found else None

    def revoke(self, public_key: str):
        """Immediate data-plane effect of a removal, ban, leave or room deletion."""
        try:
            public = wg.key_bytes(public_key)
        except ValueError:
            return
        with self._lock:
            self._grants.pop(public, None)
            self._drop_member(public)
            self.responder.forget(public)

    def metrics(self) -> dict:
        """Aggregate operator metrics; no member keys, home addresses or per-user traffic history."""
        with self._lock:
            return {**self.stats, 'members': len(self.endpoints), 'indices': len(self.indices),
                    'source_buckets': len(self._rate), 'grant_cache': len(self._grants),
                    'packet_limit': self.GLOBAL_PACKETS_PER_SECOND, 'byte_limit': self.GLOBAL_BYTES_PER_SECOND}

    # -- packet path ----------------------------------------------------------------------------------------------
    def handle(self, data: bytes, source: tuple) -> list[tuple[bytes, tuple]]:
        """Datagrams to send in reaction to one received datagram: never more than one."""
        source = (str(ipaddress.ip_address(source[0])), int(source[1]))
        with self._lock:
            kind = wg.shape(data)
            if kind is None or not self._allow(source, len(data)):
                return self._drop()
            if kind == wg.INITIATION and wg.mac1_for(data, self.responder.mac1):
                try:
                    reply, public = self.responder.initiation(data)
                except wg.Rejected:
                    return self._drop()
                if not self._prove(public, source):
                    return self._drop()
                self.stats['proofs'] += 1
                return [(reply, source)]
            if kind == wg.TRANSPORT and self.responder.owns(data[4:8]):
                public = self.responder.transport(data)
                if public is not None and self._grant(public):
                    if not self._prove(public, source):
                        return self._drop()
                    return []
                return self._drop()
            sender = self._sender(source)
            if sender is None:
                return self._drop()
            public, room = sender
            if kind == wg.INITIATION:
                target = self._initiation_target(data, public, room)
                if target is not None:
                    if not self._learn(room, data[4:8], public):
                        return self._drop()
            elif kind == wg.RESPONSE:
                target = self._index(room, data[8:12])
                if target is None or not wg.mac1_for(data, self._mac1_key(target)):
                    return self._drop()
                if not self._learn(room, data[4:8], public):
                    return self._drop()
            else:
                target = self._index(room, data[4:8])
            if target is None or target == public:
                return self._drop()
            destination = self._fresh(target)
            if destination is None:
                return self._drop()
            self.stats['relayed'] += 1
            return [(data, destination)]

    def _drop(self):
        self.stats['dropped'] += 1
        return []

    def _allow(self, source, size) -> bool:
        window = int(self.clock())
        global_start, global_count, global_bytes = self._global_rate
        if global_start != window:
            global_count, global_bytes = 0, 0
        if global_count >= self.GLOBAL_PACKETS_PER_SECOND or global_bytes + size > self.GLOBAL_BYTES_PER_SECOND:
            return False
        self._global_rate = (window, global_count + 1, global_bytes + size)
        if source not in self._rate and len(self._rate) >= self.MAX_SOURCES:
            self._rate = {k: v for k, v in self._rate.items() if v[0] == window}
            if len(self._rate) >= self.MAX_SOURCES:
                return False
        start, count, byte_count = self._rate.get(source, (window, 0, 0))
        if start != window:
            start, count, byte_count = window, 0, 0
        if count >= self.PACKETS_PER_SECOND or byte_count + size > self.BYTES_PER_SECOND:
            return False
        self._rate[source] = (start, count + 1, byte_count + size)
        return True

    def _accept(self, public: bytes) -> bool:
        return self._grant(public) is not None

    def _grant(self, public: bytes) -> Grant | None:
        now = self.clock()
        cached = self._grants.get(public)
        if cached is None or now - cached[1] >= self.GRANT_CACHE:
            if cached is None and len(self._grants) >= self.MAX_GRANTS:
                self._grants = {k:v for k,v in self._grants.items() if now-v[1] < self.GRANT_CACHE}
                if len(self._grants) >= self.MAX_GRANTS:
                    return None
            try:
                grant = self.authority(wg.b64(public))
            except Exception:
                grant = None                             # fail closed when the control plane cannot answer
            if grant is not None and (not isinstance(grant, Grant) or grant.expires <= self.wall()):
                grant = None
            cached = (grant, now)
            self._grants[public] = cached
            if grant is None:
                self._drop_member(public)
        grant = cached[0]
        if grant is not None and grant.expires <= self.wall():
            self._drop_member(public)
            return None
        return grant

    def _prove(self, public: bytes, source: tuple):
        old = self.endpoints.get(public)
        if old is None and len(self.endpoints) >= self.MAX_ENDPOINTS:
            for expired, (_, at) in list(self.endpoints.items()):
                if self.clock()-at > self.ENDPOINT_TTL:
                    self._drop_member(expired)
                    self.responder.forget(expired)
            if len(self.endpoints) >= self.MAX_ENDPOINTS:
                self.responder.forget(public)
                return False
        if old and old[0] != source:
            self.by_endpoint.pop(old[0], None)
        other = self.by_endpoint.get(source)
        if other is not None and other != public:
            self.endpoints.pop(other, None)              # the tuple now provably belongs to this key
        self.endpoints[public] = (source, self.clock())
        self.by_endpoint[source] = public
        return True

    def _fresh(self, public: bytes):
        found = self.endpoints.get(public)
        if found is None or self.clock() - found[1] > self.ENDPOINT_TTL or self._grant(public) is None:
            return None
        return found[0]

    def _sender(self, source):
        public = self.by_endpoint.get(source)
        if public is None or self._fresh(public) != source:
            return None
        grant = self._grant(public)
        return (public, grant.room) if grant else None

    def _mac1_key(self, public: bytes) -> bytes:
        key = self._mac1.get(public)
        if key is None:
            key = self._mac1[public] = wg.mac1_key(public)
        return key

    def _initiation_target(self, data, sender, room):
        for public in list(self.endpoints):
            if public != sender and wg.mac1_for(data, self._mac1_key(public)):
                grant = self._grant(public)
                return public if grant and grant.room == room else None
        return None

    def _learn(self, room, index, public):
        now = self.clock()
        previous = self.indices.get((room, index))
        if previous is not None and previous[0] != public and now-previous[1] <= self.INDEX_TTL:
            return False  # an active receiver index cannot be captured by another member in the same room
        if len(self.indices) >= self.MAX_INDICES:
            self.indices = {k: v for k, v in self.indices.items() if now - v[1] <= self.INDEX_TTL}
            if len(self.indices) >= self.MAX_INDICES:
                return False
        self.indices[(room, index)] = (public, now)
        return True

    def _index(self, room, index):
        found = self.indices.get((room, index))
        if found is None or self.clock() - found[1] > self.INDEX_TTL:
            return None
        return found[0]

    def _drop_member(self, public: bytes):
        found = self.endpoints.pop(public, None)
        if found:
            self.by_endpoint.pop(found[0], None)
        self.indices = {k: v for k, v in self.indices.items() if v[0] != public}
        self._mac1.pop(public, None)

    # -- socket loop ------------------------------------------------------------------------------------------------
    def serve(self):
        while not self._stop.is_set():
            try:
                data, source = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                out = self.handle(data, source)
            except Exception:
                self.stats['dropped'] += 1
                continue
            for payload, destination in out:
                try:
                    self.sock.sendto(payload, destination)
                except OSError:
                    pass

    def start(self):
        self._thread = threading.Thread(target=self.serve, name='akeno-lan-coordinator', daemon=True)
        self._thread.start()
        return self

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(2)
        self.sock.close()
