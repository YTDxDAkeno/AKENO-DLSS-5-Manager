"""Strict WireGuard profile import and Windows-only encrypted local storage.

This is an import/storage API, not a tunnel implementation. It never executes a command, changes routes,
contacts an endpoint or uploads a key. OpenVPN needs a separate reviewed directive/certificate policy and
is rejected rather than passing untrusted profiles to an executable.
"""
from __future__ import annotations

import base64
import configparser
import ipaddress
import json
import re
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

MAX_PROFILE_BYTES = 64 * 1024
DOMAIN = b'AKENO VPN profile v1\0'


class ProfileError(ValueError):
    pass


class StorageUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class WireGuardProfile:
    # PrivateKey and PresharedKey must not enter a repr, status, telemetry or exception message.
    text: str = field(repr=False)
    addresses: tuple[str, ...]
    dns: tuple[str, ...]
    routes: tuple[str, ...]
    endpoint: str
    routing: str


def _list(value, parser):
    parts = value.split(',')
    if not 1 <= len(parts) <= 32 or any(not p.strip() for p in parts):
        raise ProfileError('Invalid address list.')
    try:
        return tuple(str(parser(p.strip())) for p in parts)
    except ValueError:
        raise ProfileError('Invalid address list.') from None


def _key(value):
    try:
        data = base64.b64decode(value, validate=True)
        if len(data) != 32 or not any(data) or base64.b64encode(data).decode() != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise ProfileError('Invalid WireGuard key.') from None


def _endpoint(value):
    if value.startswith('['):
        match = re.fullmatch(r'\[([^]]+)\]:([0-9]{1,5})', value)
        if not match:
            raise ProfileError('Invalid endpoint.')
        try:
            ipaddress.IPv6Address(match[1])
        except ValueError:
            raise ProfileError('Invalid endpoint.') from None
    else:
        match = re.fullmatch(r'([A-Za-z0-9.-]{1,253}):([0-9]{1,5})', value)
        if not match or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', x)
                                for x in match[1].split('.')):
            raise ProfileError('Invalid endpoint.')
    if not 1 <= int(match[2]) <= 65535:
        raise ProfileError('Invalid endpoint port.')


def parse_wireguard(text: str) -> WireGuardProfile:
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_PROFILE_BYTES or '\0' in text:
        raise ProfileError('Profile is invalid or too large.')
    # Exactly one peer is supported for user-owned Internet VPN profiles in this first implementation.
    # Reject interpolation, repeated keys/sections, continuation lines, includes and every script directive.
    cfg = configparser.ConfigParser(interpolation=None, strict=True, inline_comment_prefixes=('#',),
                                    empty_lines_in_values=False)
    cfg.optionxform = str
    try:
        cfg.read_string(text)
        if cfg.defaults() or cfg.sections() != ['Interface', 'Peer']:
            raise ValueError()
        interface, peer = dict(cfg['Interface']), dict(cfg['Peer'])
        if not {'PrivateKey', 'Address'} <= interface.keys() or \
                not interface.keys() <= {'PrivateKey', 'Address', 'DNS', 'MTU'} or \
                not {'PublicKey', 'AllowedIPs', 'Endpoint'} <= peer.keys() or \
                not peer.keys() <= {'PublicKey', 'PresharedKey', 'AllowedIPs', 'Endpoint', 'PersistentKeepalive'}:
            raise ValueError()
        if any('\n' in value or '\r' in value for value in (*interface.values(), *peer.values())):
            raise ValueError()
    except (configparser.Error, ValueError, KeyError):
        raise ProfileError('Unsupported WireGuard sections or directives.') from None
    _key(interface['PrivateKey'])
    _key(peer['PublicKey'])
    if 'PresharedKey' in peer:
        _key(peer['PresharedKey'])
    addresses = _list(interface['Address'], ipaddress.ip_interface)
    routes = _list(peer['AllowedIPs'], lambda v: ipaddress.ip_network(v, strict=True))
    dns = _list(interface['DNS'], ipaddress.ip_address) if 'DNS' in interface else ()
    _endpoint(peer['Endpoint'])
    for data, name, low, high in ((interface, 'MTU', 576, 9000), (peer, 'PersistentKeepalive', 0, 65535)):
        if name in data and (not re.fullmatch('[0-9]{1,5}', data[name]) or not low <= int(data[name]) <= high):
            raise ProfileError('Unsupported numeric setting.')
    # Do not preserve comments: they may carry credentials, executable text or private operator notes.
    canonical = '[Interface]\n' + ''.join(f'{k} = {v}\n' for k,v in interface.items())
    canonical += '\n[Peer]\n' + ''.join(f'{k} = {v}\n' for k,v in peer.items())
    routing = 'full_ipv4_ipv6' if {'0.0.0.0/0', '::/0'} <= set(routes) else 'full_ipv4' \
        if '0.0.0.0/0' in routes else 'custom'
    return WireGuardProfile(canonical, addresses, dns, routes, peer['Endpoint'], routing)


class ProfileStore:
    """SQLite stores only opaque Windows DPAPI blobs. No plaintext fallback on Linux.

    protect/unprotect injection exists for deterministic tests; production callers use the Windows default.
    Callers must not put decrypted profiles or the original source files in logs or Cloud Sync folders.
    """
    def __init__(self, path: Path, protect=None, unprotect=None):
        self.path = Path(path)
        if (protect is None) != (unprotect is None):
            raise ValueError('Both storage transforms are required.')
        if protect is None:
            if sys.platform != 'win32':
                raise StorageUnavailable('VPN profile storage requires Windows DPAPI; no plaintext fallback is permitted.')
            from akeno.licensing.storage import _dpapi
            protect = lambda data: _dpapi(data, True)
            unprotect = lambda data: _dpapi(data, False)
        self._protect, self._unprotect = protect, unprotect
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, payload BLOB NOT NULL)')

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def add(self, name: str, text: str) -> str:
        name = self._name(name)
        profile = parse_wireguard(text)
        ident = uuid.uuid4().hex
        blob = self._encode({'name': name, 'text': profile.text, 'protocol': 'wireguard'})
        with self._db() as db:
            db.execute('INSERT INTO profiles VALUES (?, ?)', (ident, blob))
        return ident

    @staticmethod
    def _name(name):
        if not isinstance(name,str) or not 1 <= len(name.strip()) <= 64 or any(ord(c)<32 for c in name):
            raise ProfileError('Invalid profile name.')
        return name.strip()

    def _encode(self, data):
        return self._protect(DOMAIN + json.dumps(data, separators=(',', ':')).encode('utf-8'))

    def _load(self, ident):
        with self._db() as db:
            row = db.execute('SELECT payload FROM profiles WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise ProfileError('Unknown profile.')
        try:
            raw = self._unprotect(row[0])
            if not raw.startswith(DOMAIN):
                raise ValueError()
            data = json.loads(raw[len(DOMAIN):])
            if set(data) != {'name', 'text', 'protocol'} or data['protocol'] != 'wireguard':
                raise ValueError()
            self._name(data['name'])
            parse_wireguard(data['text'])
            return data
        except Exception:
            # Never include cryptographic/parser exception text that might expose a decrypted profile.
            raise ProfileError('Encrypted profile is damaged or belongs to another Windows user.') from None

    def metadata(self) -> list[dict]:
        with self._db() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM profiles ORDER BY id')]
        return [{'id': ident, 'name': self._load(ident)['name'], 'protocol': 'wireguard',
                 'state': 'imported', 'country': None, 'connectable': False} for ident in ids]

    def rename(self, ident: str, name: str):
        data = self._load(ident)
        data['name'] = self._name(name)
        with self._db() as db:
            db.execute('UPDATE profiles SET payload=? WHERE id=?', (self._encode(data), ident))

    def remove(self, ident: str):
        with self._db() as db:
            db.execute('DELETE FROM profiles WHERE id=?', (ident,))
