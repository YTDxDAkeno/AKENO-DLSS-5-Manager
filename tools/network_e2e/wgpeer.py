"""Minimal userspace WireGuard responder for disposable test machines. Never shipped in AKENO.

Implements the responder side of Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s and transport data messages exactly as the
WireGuard whitepaper specifies, so a real WireGuard implementation (WireGuardNT on a Windows runner, wireguard-go or
the Linux kernel module) can be tested against a peer on loopback: no Internet exit, third-party server or host
route is needed. On its virtual addresses it answers ICMP/ICMPv6 echo and UDP echo (port 7); every other packet that
arrives through the tunnel is only recorded (destination, protocol, port) so a test can prove where traffic went.

Test-only simplifications: no cookie reply under load, no initiation, no rekey timers of its own.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import socket
import struct
import threading
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

CONSTRUCTION = b'Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s'
IDENTIFIER = b'WireGuard v1 zx2c4 Jason@zx2c4.com'
LABEL_MAC1 = b'mac1----'
ECHO_PORT = 7


def _hash(*parts: bytes) -> bytes:
    h = hashlib.blake2s(digest_size=32)
    for p in parts:
        h.update(p)
    return h.digest()


def _mac(key: bytes, data: bytes) -> bytes:
    return hashlib.blake2s(data, digest_size=16, key=key).digest()


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.blake2s).digest()


def _kdf(n: int, key: bytes, data: bytes) -> list[bytes]:
    t0, out, prev = _hmac(key, data), [], b''
    for i in range(1, n + 1):
        prev = _hmac(t0, prev + bytes([i]))
        out.append(prev)
    return out


def _nonce(counter: int) -> bytes:
    return b'\0' * 4 + struct.pack('<Q', counter)


def _raw(public: X25519PublicKey) -> bytes:
    return public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _dh(private: X25519PrivateKey, public: bytes) -> bytes:
    return private.exchange(X25519PublicKey.from_public_bytes(public))


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b'\0'
    s = sum(struct.unpack(f'!{len(data) // 2}H', data))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


def keypair() -> tuple[str, str]:
    """(private, public) in WireGuard's base64 form."""
    k = X25519PrivateKey.generate()
    private = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                              serialization.NoEncryption())
    return base64.b64encode(private).decode(), base64.b64encode(_raw(k.public_key())).decode()


class _Session:
    def __init__(self, local, remote, recv, send, addr):
        self.local, self.remote, self.addr = local, remote, addr
        self.recv, self.send = ChaCha20Poly1305(recv), ChaCha20Poly1305(send)
        self.counter, self.seen, self.confirmed = 0, set(), False


class Responder:
    """Answers one known client key. ``drop=True`` simulates a dead peer (nothing is answered or decrypted)."""

    def __init__(self, private_key: str, client_public: str, addresses=('10.203.77.1',), host='127.0.0.1',
                 port=0, psk: bytes = b'\0' * 32):
        self._private = X25519PrivateKey.from_private_bytes(base64.b64decode(private_key))
        self._public = _raw(self._private.public_key())
        self._client = base64.b64decode(client_public)
        self._psk = psk
        self.addresses = {ipaddress.ip_address(a) for a in addresses}
        self.sock = socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.sessions: dict[bytes, _Session] = {}
        self.handshakes = 0
        self.transport_packets = 0
        self.echoed = 0
        self.observed: list[dict] = []
        self.drop = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, name='wgpeer', daemon=True)

    @property
    def public_key(self) -> str:
        return base64.b64encode(self._public).decode()

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(5)
        self.sock.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def observed_snapshot(self) -> list[dict]:
        with self._lock:
            return list(self.observed)

    def _loop(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            if self.drop or len(data) < 4 or data[1:4] != b'\0\0\0':
                continue
            try:
                if data[0] == 1:
                    self._initiation(data, addr)
                elif data[0] == 4:
                    self._transport(data, addr)
            except Exception:
                # Malformed or unauthenticated input is ignored, as a WireGuard peer does.
                continue

    def _initiation(self, msg: bytes, addr):
        if len(msg) != 148 or not hmac.compare_digest(_mac(_hash(LABEL_MAC1, self._public), msg[:116]), msg[116:132]):
            return
        sender, e_i, enc_static, enc_time = msg[4:8], msg[8:40], msg[40:88], msg[88:116]
        c = _hash(CONSTRUCTION)
        h = _hash(_hash(c, IDENTIFIER), self._public)
        h = _hash(h, e_i)
        c, = _kdf(1, c, e_i)
        c, k = _kdf(2, c, _dh(self._private, e_i))
        s_i = ChaCha20Poly1305(k).decrypt(_nonce(0), enc_static, h)
        h = _hash(h, enc_static)
        if not hmac.compare_digest(s_i, self._client):
            return
        c, k = _kdf(2, c, _dh(self._private, s_i))
        ChaCha20Poly1305(k).decrypt(_nonce(0), enc_time, h)
        h = _hash(h, enc_time)
        e_r = X25519PrivateKey.generate()
        e_r_pub = _raw(e_r.public_key())
        h = _hash(h, e_r_pub)
        c, = _kdf(1, c, e_r_pub)
        c, = _kdf(1, c, _dh(e_r, e_i))
        c, = _kdf(1, c, _dh(e_r, s_i))
        c, tau, k = _kdf(3, c, self._psk)
        h = _hash(h, tau)
        empty = ChaCha20Poly1305(k).encrypt(_nonce(0), b'', h)
        local = os.urandom(4)
        reply = bytes([2, 0, 0, 0]) + local + sender + e_r_pub + empty
        reply += _mac(_hash(LABEL_MAC1, s_i), reply) + b'\0' * 16
        recv, send = _kdf(2, c, b'')
        with self._lock:
            self.sessions[local] = _Session(local, sender, recv, send, addr)
            while len(self.sessions) > 4:
                self.sessions.pop(next(iter(self.sessions)))
            self.handshakes += 1
        self.sock.sendto(reply, addr)

    def _transport(self, msg: bytes, addr):
        if len(msg) < 32:
            return
        session = self.sessions.get(msg[4:8])
        if session is None:
            return
        counter, = struct.unpack('<Q', msg[8:16])
        if counter in session.seen:
            return
        packet = session.recv.decrypt(_nonce(counter), msg[16:], None)
        session.seen.add(counter)
        session.confirmed, session.addr = True, addr       # authenticated roaming, as WireGuard does
        with self._lock:
            self.transport_packets += 1
        if packet:
            reply = self._handle(packet)
            if reply:
                self._send(session, reply)

    def _send(self, session: _Session, packet: bytes):
        padded = packet + b'\0' * (-len(packet) % 16)
        counter, session.counter = session.counter, session.counter + 1
        body = session.send.encrypt(_nonce(counter), padded, None)
        self.sock.sendto(bytes([4, 0, 0, 0]) + session.remote + struct.pack('<Q', counter) + body, session.addr)

    def _record(self, version, proto, dst, port):
        with self._lock:
            if len(self.observed) < 5000:
                self.observed.append({'version': version, 'protocol': proto, 'destination': str(dst), 'port': port})

    def _handle(self, packet: bytes) -> bytes | None:
        version = packet[0] >> 4
        if version == 4:
            ihl = (packet[0] & 15) * 4
            total, = struct.unpack('!H', packet[2:4])
            packet = packet[:total]
            proto, src, dst = packet[9], packet[12:16], packet[16:20]
            payload = packet[ihl:]
        elif version == 6:
            length, = struct.unpack('!H', packet[4:6])
            packet = packet[:40 + length]
            proto, src, dst = packet[6], packet[8:24], packet[24:40]
            payload = packet[40:]
        else:
            return None
        address = ipaddress.ip_address(dst)
        port = struct.unpack('!H', payload[2:4])[0] if proto in (6, 17) and len(payload) >= 4 else None
        if address not in self.addresses:
            self._record(version, proto, address, port)
            return None
        if proto == 17 and port == ECHO_PORT and len(payload) >= 8:
            sport, = struct.unpack('!H', payload[:2])
            body = payload[8:]
            udp = struct.pack('!HHHH', ECHO_PORT, sport, 8 + len(body), 0) + body
            pseudo = (dst + src + struct.pack('!BBH', 0, 17, len(udp)) if version == 4 else
                      dst + src + struct.pack('!IxxxB', len(udp), 17))
            udp = udp[:6] + struct.pack('!H', _checksum(pseudo + udp) or 0xFFFF) + udp[8:]
            reply = udp
        elif version == 4 and proto == 1 and payload[:1] == b'\x08':
            icmp = b'\x00\x00\x00\x00' + payload[4:]
            reply = icmp[:2] + struct.pack('!H', _checksum(icmp)) + icmp[4:]
        elif version == 6 and proto == 58 and payload[:1] == b'\x80':
            icmp = b'\x81\x00\x00\x00' + payload[4:]
            pseudo = dst + src + struct.pack('!IxxxB', len(icmp), 58)
            reply = icmp[:2] + struct.pack('!H', _checksum(pseudo + icmp)) + icmp[4:]
        else:
            self._record(version, proto, address, port)
            return None
        with self._lock:
            self.echoed += 1
        if version == 4:
            header = bytearray(packet[:20])
            header[2:4] = struct.pack('!H', 20 + len(reply))
            header[0], header[8], header[10:12] = 0x45, 64, b'\0\0'
            header[12:16], header[16:20] = dst, src
            header[10:12] = struct.pack('!H', _checksum(bytes(header)))
            return bytes(header) + reply
        header = bytearray(packet[:40])
        header[4:6] = struct.pack('!H', len(reply))
        header[7] = 64
        header[8:24], header[24:40] = dst, src
        return bytes(header) + reply


def udp_echo(address: str, count=64, size=1000, timeout=3.0) -> int:
    """Send ``count`` random datagrams to the responder's echo port through the tunnel; returns verified replies."""
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        ok = 0
        for _ in range(count):
            payload = os.urandom(size)
            s.sendto(payload, (address, ECHO_PORT))
            try:
                while True:
                    data, _ = s.recvfrom(65535)
                    if data == payload:
                        ok += 1
                        break
            except socket.timeout:
                pass
        return ok


if __name__ == '__main__':
    import argparse
    import json
    p = argparse.ArgumentParser(description='Run a loopback WireGuard test responder until interrupted.')
    p.add_argument('--client-public', required=True)
    p.add_argument('--address', action='append', default=None)
    p.add_argument('--port', type=int, default=0)
    p.add_argument('--info', required=True, help='JSON file receiving the public key and port')
    a = p.parse_args()
    private, _ = keypair()
    with Responder(private, a.client_public, tuple(a.address or ('10.203.77.1',)), port=a.port) as r:
        with open(a.info, 'w', encoding='utf-8') as f:
            json.dump({'public_key': r.public_key, 'port': r.port}, f)
        try:
            while True:
                time.sleep(1)
                print(json.dumps({'handshakes': r.handshakes, 'transport': r.transport_packets, 'echoed': r.echoed}),
                      flush=True)
        except KeyboardInterrupt:
            pass
