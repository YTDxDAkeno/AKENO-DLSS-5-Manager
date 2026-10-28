"""Independent operator measurement tool for a separately authorised VPN exit, never a customer client.

Run in a dedicated probe namespace/host with the temporary operator-only WireGuard peer configured. Egress HTTP
is explicitly source-bound to that tunnel's addresses; both families must match a fresh, checksum-pinned licensed
MaxMind country database. No external country API or invented provider endpoint is used. This tool is not run in
the restricted cloud executor to bypass its egress proxy, and never changes any route, interface or firewall.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import stat
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from akeno_server.licensing.signing import sign, kid_for, raw_public
from akeno_server.vpn.store import VPNError, country_code, wireguard_key, global_ip


class BoundHTTPS:
    """Standard verified TLS to the official IPify v4/v6 endpoints from this probe's tunnel source addresses.

    Explicit source binding is required: a system HTTP proxy would measure the proxy instead of the VPN exit.
    Deployment operators must authorise these direct probe connections in their dedicated probe environment.
    """
    def observe(self, source):
        address = ipaddress.ip_address(source)
        pool = ipaddress.ip_network('10.204.0.0/16' if address.version == 4 else 'fd42:204::/48')
        if address not in pool:
            raise VPNError('Probe source must be the assigned temporary VPN address.')
        host = 'api.ipify.org' if address.version == 4 else 'api6.ipify.org'
        family = socket.AF_INET if address.version == 4 else socket.AF_INET6
        endpoints = socket.getaddrinfo(host, 443, family, socket.SOCK_STREAM)
        if not endpoints:
            raise VPNError('The independent IP observation service is unavailable.')
        last_error = None
        for af, kind, protocol, _, remote in endpoints[:4]:
            if not ipaddress.ip_address(remote[0]).is_global:
                raise VPNError('The observation service did not resolve to a global address.')
            raw = socket.socket(af, kind, protocol)
            try:
                raw.settimeout(5)
                raw.bind((str(address), 0))
                raw.connect(remote)
                with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as secure:
                    request = ('GET /?format=json HTTP/1.1\r\nHost: ' + host +
                               '\r\nAccept: application/json\r\nConnection: close\r\nUser-Agent: AKENO-exit-probe/1\r\n\r\n')
                    secure.sendall(request.encode('ascii'))
                    response = http.client.HTTPResponse(secure)
                    response.begin()
                    if response.status != 200:
                        raise VPNError('The independent IP observation service rejected the measurement.')
                    payload = response.read(4097)
                    if len(payload) > 4096:
                        raise VPNError('The independent IP observation response is too large.')
                    result = json.loads(payload)
                    if not isinstance(result, dict) or set(result) != {'ip'}:
                        raise VPNError('The independent IP observation response is invalid.')
                    return global_ip(result['ip'], address.version)
            except (OSError, ValueError, http.client.HTTPException) as exc:
                last_error = exc
            finally:
                raw.close()
        raise VPNError('Independent source-bound exit measurement failed.') from last_error


class PinnedCountryDatabase:
    """Read an operator-licensed GeoLite2/GeoIP2 Country database whose SHA-256 was verified out of band.

    The optional MaxMind reader is isolated to the probe tool; no customer dependency or database download occurs.
    Country accuracy is limited by the measured IP and database; a mismatch always blocks the selected country.
    """
    def __init__(self, path, sha256, *, now=None):
        self.path = Path(path)
        if not re.fullmatch('[0-9a-f]{64}', sha256 or ''):
            raise VPNError('Pin the licensed country database SHA-256 before probing.')
        if not self.path.is_file() or not 0 < self.path.stat().st_size <= 1024 * 1024 * 1024:
            raise VPNError('The licensed country database is unavailable.')
        digest = hashlib.sha256()
        with self.path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != sha256:
            raise VPNError('The country database checksum does not match the trusted pin.')
        try:
            import maxminddb
        except ImportError:
            raise VPNError('Install the separately pinned independent-probe reader first.') from None
        self.reader = maxminddb.open_database(str(self.path))
        metadata = self.reader.metadata()
        now = time.time() if now is None else now
        if metadata.database_type not in ('GeoLite2-Country', 'GeoIP2-Country') or \
                not now - 30 * 86400 <= metadata.build_epoch <= now + 86400:
            self.reader.close()
            raise VPNError('Use a fresh, licensed country database no older than 30 days.')
        self.reference = 'maxmind-sha256:' + sha256 + ';build:' + str(metadata.build_epoch)

    def country(self, address):
        global_ip(address)
        row = self.reader.get(address)
        try:
            return country_code(row['country']['iso_code'])
        except (KeyError, TypeError):
            raise VPNError('The measured IP has no authoritative country assignment in the pinned database.') from None

    def close(self):
        self.reader.close()


def private_probe_key(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or os.name != 'nt' and metadata.st_mode & 0o077:
            raise VPNError('The independent probe key must be an owner-only regular file.')
        raw = stream.read(33)
    if len(raw) != 32:
        raise VPNError('The independent probe key is damaged.')
    return Ed25519PrivateKey.from_private_bytes(raw)


def measure(server_id, server_public_key, country, source_ipv4, source_ipv6, private_key, database,
            observer=None, clock=time.time):
    if not re.fullmatch('[0-9a-f]{32}', server_id or ''):
        raise VPNError('Invalid registered exit identity.')
    wireguard_key(server_public_key); country_code(country)
    if ipaddress.ip_address(source_ipv4).version != 4 or ipaddress.ip_address(source_ipv6).version != 6:
        raise VPNError('Both assigned probe address families are required.')
    observer = observer or BoundHTTPS()
    exit4, exit6 = observer.observe(source_ipv4), observer.observe(source_ipv6)
    global_ip(exit4, 4); global_ip(exit6, 6)
    if database.country(exit4) != country or database.country(exit6) != country:
        raise VPNError('The measured dual-stack exit IPs do not match the selected country.')
    now = int(clock())
    claims = {'iss': 'akeno-vpn-probe', 'aud': server_id, 'server_id': server_id,
              'server_public_key': server_public_key, 'country': country, 'exit_ipv4': exit4, 'exit_ipv6': exit6,
              'observed_at': now, 'iat': now, 'exp': now + 120, 'nonce': secrets.token_urlsafe(24),
              'evidence': 'bound-ipify-dualstack;' + database.reference}
    return sign(private_key, kid_for(raw_public(private_key.public_key())), 'akeno-vpn-exit-proof', claims)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Independent, explicitly authorised dual-stack AKENO exit probe.')
    for name in ('server-id', 'server-public-key', 'country', 'source-ipv4', 'source-ipv6',
                 'key-file', 'country-database', 'database-sha256', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    database = PinnedCountryDatabase(args.country_database, args.database_sha256)
    try:
        token = measure(args.server_id, args.server_public_key, args.country, args.source_ipv4, args.source_ipv6,
                        private_probe_key(args.key_file), database)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'proof': token}, stream)
        return 0
    finally:
        database.close()


if __name__ == '__main__':
    raise SystemExit(main())
