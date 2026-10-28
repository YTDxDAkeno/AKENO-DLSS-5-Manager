"""WireGuard message primitives for the LAN coordinator (whitepaper section 5.4; responder side only).

The coordinator is an ordinary WireGuard peer of every room member. Completing the member's handshake proves that
the sender holds the member's registered private key, and authenticated transport packets show the current NAT
mapping of the member's own WireGuard socket. Nothing here decrypts traffic between members.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

CONSTRUCTION = b'Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s'
IDENTIFIER = b'WireGuard v1 zx2c4 Jason@zx2c4.com'
LABEL_MAC1 = b'mac1----'
INITIATION, RESPONSE, COOKIE, TRANSPORT = 1, 2, 3, 4
SIZES = {INITIATION: 148, RESPONSE: 92, COOKIE: 64}


class Rejected(ValueError):
    pass


def blake(*parts: bytes) -> bytes:
    h = hashlib.blake2s(digest_size=32)
    for p in parts:
        h.update(p)
    return h.digest()


def mac(key: bytes, data: bytes) -> bytes:
    return hashlib.blake2s(data, digest_size=16, key=key).digest()


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.blake2s).digest()


def kdf(n: int, key: bytes, data: bytes) -> list[bytes]:
    t0, out, prev = _hmac(key, data), [], b''
    for i in range(1, n + 1):
        prev = _hmac(t0, prev + bytes([i]))
        out.append(prev)
    return out


def nonce(counter: int) -> bytes:
    return b'\0' * 4 + struct.pack('<Q', counter)


def raw_public(key: X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def key_bytes(value: str) -> bytes:
    data = base64.b64decode(value, validate=True)
    if len(data) != 32 or b64(data) != value:
        raise ValueError('invalid WireGuard key')
    return data


def mac1_key(public: bytes) -> bytes:
    return blake(LABEL_MAC1, public)


def mac1_for(message: bytes, key: bytes) -> bool:
    """True when ``message`` (initiation or response) carries a valid mac1 for the receiver with mac1 key ``key``."""
    size = SIZES.get(message[0])
    if size not in (148, 92) or len(message) != size:
        return False
    return hmac.compare_digest(mac(key, message[:size - 32]), message[size - 32:size - 16])


def shape(message: bytes) -> int | None:
    """The WireGuard message type if ``message`` is well formed, else None (no other datagrams are relayed)."""
    if len(message) < 32 or message[1:4] != b'\0\0\0':
        return None
    kind = message[0]
    if kind in SIZES:
        return kind if len(message) == SIZES[kind] else None
    if kind == TRANSPORT and len(message) <= 1500 + 32 and (len(message) - 32) % 16 == 0:
        return kind
    return None


class Session:
    __slots__ = ('local', 'remote', 'peer', 'recv', 'send', 'counter', 'seen', 'created')

    def __init__(self, local, remote, peer, recv, send, created):
        self.local, self.remote, self.peer, self.created = local, remote, peer, created
        self.recv, self.send = ChaCha20Poly1305(recv), ChaCha20Poly1305(send)
        self.counter, self.seen = 0, set()


class Responder:
    """Answers initiations from accepted static keys. ``accept(public) -> bool`` is asked on every handshake."""

    def __init__(self, private: X25519PrivateKey, accept, clock):
        self.private, self.public = private, raw_public(private)
        self.mac1 = mac1_key(self.public)
        self.accept, self.clock = accept, clock
        self.timestamps: dict[bytes, bytes] = {}
        self.sessions: dict[bytes, Session] = {}

    def initiation(self, message: bytes) -> tuple[bytes, bytes]:
        """Returns (reply, initiator static key). Raises Rejected for anything not a fresh valid handshake."""
        if len(message) != 148 or not mac1_for(message, self.mac1):
            raise Rejected('not for the coordinator')
        sender, e_i, enc_static, enc_time = message[4:8], message[8:40], message[40:88], message[88:116]
        try:
            c = blake(CONSTRUCTION)
            h = blake(blake(c, IDENTIFIER), self.public)
            h = blake(h, e_i)
            c, = kdf(1, c, e_i)
            c, k = kdf(2, c, self.private.exchange(X25519PublicKey.from_public_bytes(e_i)))
            s_i = ChaCha20Poly1305(k).decrypt(nonce(0), enc_static, h)
            h = blake(h, enc_static)
            if not self.accept(s_i):
                raise Rejected('unknown or inactive member key')
            c, k = kdf(2, c, self.private.exchange(X25519PublicKey.from_public_bytes(s_i)))
            stamp = ChaCha20Poly1305(k).decrypt(nonce(0), enc_time, h)
        except Rejected:
            raise
        except Exception:
            raise Rejected('handshake does not verify') from None
        h = blake(h, enc_time)
        # TAI64N must increase per key, exactly as WireGuard requires: a captured initiation cannot be replayed to
        # move someone's proven endpoint.
        if s_i in self.timestamps and stamp <= self.timestamps[s_i]:
            raise Rejected('replayed handshake')
        self.timestamps[s_i] = stamp
        e_r = X25519PrivateKey.generate()
        e_r_pub = raw_public(e_r)
        h = blake(h, e_r_pub)
        c, = kdf(1, c, e_r_pub)
        c, = kdf(1, c, e_r.exchange(X25519PublicKey.from_public_bytes(e_i)))
        c, = kdf(1, c, e_r.exchange(X25519PublicKey.from_public_bytes(s_i)))
        c, tau, k = kdf(3, c, b'\0' * 32)
        h = blake(h, tau)
        empty = ChaCha20Poly1305(k).encrypt(nonce(0), b'', h)
        local = os.urandom(4)
        reply = bytes([RESPONSE, 0, 0, 0]) + local + sender + e_r_pub + empty
        reply += mac(mac1_key(s_i), reply) + b'\0' * 16
        recv, send = kdf(2, c, b'')
        for index in [i for i, s in self.sessions.items() if s.peer == s_i][:-1]:
            del self.sessions[index]                      # current and previous session per key, like WireGuard
        self.sessions[local] = Session(local, sender, s_i, recv, send, self.clock())
        return reply, s_i

    def transport(self, message: bytes) -> bytes | None:
        """The static key of the authenticated sender of a transport message to the coordinator, else None."""
        session = self.sessions.get(message[4:8])
        if session is None or self.clock() - session.created > 180:
            return None
        counter, = struct.unpack('<Q', message[8:16])
        if counter in session.seen or (session.seen and counter + 2048 < max(session.seen)):
            return None
        try:
            session.recv.decrypt(nonce(counter), message[16:], None)
        except Exception:
            return None
        session.seen.add(counter)
        if len(session.seen) > 4096:
            session.seen = set(sorted(session.seen)[-2048:])
        return session.peer

    def owns(self, index: bytes) -> bool:
        return index in self.sessions

    def forget(self, public: bytes):
        # The last timestamp is kept: an old captured initiation stays unusable after a member is re-admitted.
        for index in [i for i, s in self.sessions.items() if s.peer == public]:
            del self.sessions[index]
