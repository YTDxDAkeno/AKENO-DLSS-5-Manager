"""Test-only WireGuard initiator (whitepaper 5.4.2-5.4.6) to drive the coordinator like a real member would."""
import os
import struct
import time

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from akeno_server.network import wireguard as wg


def tai64n(offset=0.0):
    now = time.time() + offset
    return struct.pack('>QI', 0x400000000000000a + int(now), int((now % 1) * 1e9))


class Member:
    def __init__(self):
        self.private = X25519PrivateKey.generate()
        self.public = wg.raw_public(self.private)
        self.key = wg.b64(self.public)
        self.send_key = self.recv_key = None
        self.remote = None
        self.counter = 0

    def initiation(self, responder_public: bytes, stamp=None):
        """A handshake initiation to ``responder_public``; keeps the state needed to consume the response."""
        e = X25519PrivateKey.generate()
        e_pub = wg.raw_public(e)
        c = wg.blake(wg.CONSTRUCTION)
        h = wg.blake(wg.blake(c, wg.IDENTIFIER), responder_public)
        h = wg.blake(h, e_pub)
        c, = wg.kdf(1, c, e_pub)
        c, k = wg.kdf(2, c, e.exchange(X25519PublicKey.from_public_bytes(responder_public)))
        enc_static = ChaCha20Poly1305(k).encrypt(wg.nonce(0), self.public, h)
        h = wg.blake(h, enc_static)
        c, k = wg.kdf(2, c, self.private.exchange(X25519PublicKey.from_public_bytes(responder_public)))
        enc_time = ChaCha20Poly1305(k).encrypt(wg.nonce(0), stamp or tai64n(), h)
        h = wg.blake(h, enc_time)
        self.index = os.urandom(4)
        msg = bytes([1, 0, 0, 0]) + self.index + e_pub + enc_static + enc_time
        msg += wg.mac(wg.mac1_key(responder_public), msg) + b'\0' * 16
        self._pending = (e, c, h)
        return msg

    def consume(self, reply: bytes):
        e, c, h = self._pending
        assert reply[0] == 2 and len(reply) == 92 and reply[8:12] == self.index
        assert wg.mac1_for(reply, wg.mac1_key(self.public))
        e_r = reply[12:44]
        h = wg.blake(h, e_r)
        c, = wg.kdf(1, c, e_r)
        c, = wg.kdf(1, c, e.exchange(X25519PublicKey.from_public_bytes(e_r)))
        c, = wg.kdf(1, c, self.private.exchange(X25519PublicKey.from_public_bytes(e_r)))
        c, tau, k = wg.kdf(3, c, b'\0' * 32)
        h = wg.blake(h, tau)
        ChaCha20Poly1305(k).decrypt(wg.nonce(0), reply[44:60], h)
        send, recv = wg.kdf(2, c, b'')
        self.send_key, self.recv_key, self.remote = ChaCha20Poly1305(send), ChaCha20Poly1305(recv), reply[4:8]

    def keepalive(self):
        body = self.send_key.encrypt(wg.nonce(self.counter), b'', None)
        msg = bytes([4, 0, 0, 0]) + self.remote + struct.pack('<Q', self.counter) + body
        self.counter += 1
        return msg


def opaque_transport(receiver_index: bytes, size=64):
    """A well-formed data message to another member: the coordinator cannot and must not decrypt it."""
    return bytes([4, 0, 0, 0]) + receiver_index + struct.pack('<Q', 7) + os.urandom(size + 16)


def response_for(initiator: Member, responder: Member, receiver_index: bytes):
    """A response-shaped message from ``responder`` to ``initiator`` with a valid mac1 (content is opaque here)."""
    msg = bytes([2, 0, 0, 0]) + os.urandom(4) + receiver_index + os.urandom(48)
    return msg + wg.mac(wg.mac1_key(initiator.public), msg) + b'\0' * 16
