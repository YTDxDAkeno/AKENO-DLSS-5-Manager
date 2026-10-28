"""VPN exit inventory and short leases. Only operator-verified, recently healthy exits are offered.

The store holds public WireGuard keys and hashes of random agent credentials. User private keys never enter it.
The exit reconciler must acknowledge every desired generation before a lease becomes ready.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class VPNError(ValueError):
    pass


def wireguard_key(value):
    try:
        if not isinstance(value, str) or len(value) != 44:
            raise ValueError()
        raw = base64.b64decode(value, validate=True)
        if len(raw) != 32 or not any(raw) or base64.b64encode(raw).decode() != value:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise VPNError('Invalid public WireGuard key.') from None


def country_code(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Z]{2}', value):
        raise VPNError('Use an uppercase ISO country code.')
    return value


def global_ip(value, version=None):
    try:
        ip = ipaddress.ip_address(value)
        if not ip.is_global or (version is not None and ip.version != version):
            raise ValueError()
        return str(ip)
    except (ValueError, TypeError):
        raise VPNError('A verified global IP address is required.') from None


class VPNStore:
    HEALTH_TTL = 30
    VERIFICATION_TTL = 86400
    LEASE_SECONDS = 45

    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS vpn_servers (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, country TEXT NOT NULL,
                public_key TEXT NOT NULL UNIQUE, endpoint TEXT NOT NULL,
                subnet4 TEXT NOT NULL UNIQUE, subnet6 TEXT NOT NULL UNIQUE, dns TEXT NOT NULL,
                capacity INTEGER NOT NULL, token_hash TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
                exit4 TEXT, exit6 TEXT, verified_country TEXT, verified_at REAL, evidence TEXT,
                health_at REAL, generation INTEGER NOT NULL DEFAULT 0, acknowledged INTEGER NOT NULL DEFAULT -1
            );
            CREATE TABLE IF NOT EXISTS vpn_leases (
                id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, server_id TEXT NOT NULL,
                actor TEXT NOT NULL, device TEXT NOT NULL, public_key TEXT NOT NULL,
                address4 TEXT NOT NULL, address6 TEXT NOT NULL, expires REAL NOT NULL,
                generation INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS vpn_leases_server ON vpn_leases(server_id, revoked, expires);
            CREATE TABLE IF NOT EXISTS vpn_probe_replays (nonce TEXT PRIMARY KEY, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS vpn_probe_leases (
                server_id TEXT PRIMARY KEY, public_key TEXT NOT NULL UNIQUE, expires REAL NOT NULL
            );
            ''')

    @contextmanager
    def _db(self, write=False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def register(self, *, name, country, public_key, endpoint, subnet4, subnet6, dns, capacity=100):
        """Trusted operator action; registration alone never makes a server available."""
        country_code(country); wireguard_key(public_key)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64 or any(ord(c) < 32 for c in name):
            raise VPNError('Invalid server name.')
        # Endpoint is an operator-controlled public IP literal, never a user-selected or DNS-rebinding target.
        match = re.fullmatch(r'(\[[0-9a-fA-F:]+\]|[0-9.]+):([0-9]{1,5})', endpoint or '')
        if not match or not 1 <= int(match[2]) <= 65535:
            raise VPNError('Invalid public exit endpoint.')
        global_ip(match[1].strip('[]'))
        try:
            v4, v6 = ipaddress.ip_network(subnet4), ipaddress.ip_network(subnet6)
            if v4.version != 4 or v4.prefixlen != 24 or not v4.subnet_of(ipaddress.ip_network('10.204.0.0/16')):
                raise ValueError()
            if v6.version != 6 or v6.prefixlen != 64 or not v6.subnet_of(ipaddress.ip_network('fd42:204::/48')):
                raise ValueError()
            if isinstance(capacity, bool) or not 1 <= capacity <= 250:
                raise ValueError()
        except (ValueError, TypeError):
            raise VPNError('Use an isolated private IPv4 /24 and IPv6 ULA /64, capacity 1..250.') from None
        if not isinstance(dns, list) or not 1 <= len(dns) <= 4:
            raise VPNError('Explicit trusted DNS resolvers are required.')
        dns = [global_ip(x) for x in dns]
        ident, token = uuid.uuid4().hex, secrets.token_urlsafe(48)
        with self._db(True) as db:
            # An overlap with another registered exit would introduce ambiguous cross-exit address ownership.
            for row in db.execute('SELECT subnet4,subnet6 FROM vpn_servers'):
                if v4.overlaps(ipaddress.ip_network(row['subnet4'])) or v6.overlaps(ipaddress.ip_network(row['subnet6'])):
                    raise VPNError('Exit subnets overlap another registered exit.')
            try:
                db.execute('''INSERT INTO vpn_servers
                    (id,name,country,public_key,endpoint,subnet4,subnet6,dns,capacity,token_hash)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''', (ident, name.strip(), country, public_key, endpoint,
                    str(v4), str(v6), json.dumps(dns), capacity, self._digest(token)))
            except sqlite3.IntegrityError:
                raise VPNError('An exit key or subnet is already registered.') from None
        return {'server_id': ident, 'agent_token': token, 'enabled': False}

    @staticmethod
    def _digest(token):
        return hashlib.sha256(token.encode('ascii')).hexdigest()

    def authenticate_agent(self, ident, token):
        if not isinstance(token, str) or not 48 <= len(token) <= 128:
            return False
        try:
            digest = self._digest(token)
        except UnicodeError:
            return False
        with self._db() as db:
            row = db.execute('SELECT token_hash FROM vpn_servers WHERE id=?', (ident,)).fetchone()
        return row is not None and hmac.compare_digest(digest, row['token_hash'])

    def provision_probe(self, ident, public_key):
        """Explicit operator-only bootstrap: one 45-second measurement peer, never a customer/country listing.

        Probe private keys and the independent Ed25519 measurement key remain outside this server.
        Address .252/::fc is reserved and is not part of the customer allocation pool .2..251.
        """
        wireguard_key(public_key)
        with self._db(True) as db:
            server = self._server(db, ident)
            if db.execute('SELECT 1 FROM vpn_leases WHERE public_key=? AND revoked=0 AND expires>?',
                          (public_key, self.clock())).fetchone():
                raise VPNError('A customer VPN key cannot be reused for an independent probe.')
            other = db.execute('SELECT server_id FROM vpn_probe_leases WHERE public_key=? AND expires>?',
                               (public_key, self.clock())).fetchone()
            if other is not None and other['server_id'] != ident:
                raise VPNError('An independent probe key is already assigned to another exit.')
            db.execute('DELETE FROM vpn_probe_leases WHERE expires<=?', (self.clock(),))
            db.execute('INSERT OR REPLACE INTO vpn_probe_leases VALUES (?,?,?)',
                       (ident, public_key, self.clock() + self.LEASE_SECONDS))
            db.execute('UPDATE vpn_servers SET generation=generation+1 WHERE id=?', (ident,))
            return {'server_id': ident, 'country': server['country'], 'server_public_key': server['public_key'],
                    'endpoint': server['endpoint'], 'dns': json.loads(server['dns']),
                    'addresses': [str(ipaddress.ip_network(server['subnet4']).network_address + 252) + '/32',
                                  str(ipaddress.ip_network(server['subnet6']).network_address + 252) + '/128'],
                    'expires_at': int(self.clock()) + self.LEASE_SECONDS, 'measurement_only': True}

    def verify_exit(self, ident, *, country, exit_ipv4, exit_ipv6, evidence, observed_at):
        """Operator submits measurements from an independent trusted probe, never the untrusted exit agent.

        This attestation is an operational trust boundary: it does not invent a GeoIP API or geolocate an IP itself.
        A measured country mismatch disables the exit immediately.
        """
        country_code(country)
        exit4, exit6 = global_ip(exit_ipv4, 4), global_ip(exit_ipv6, 6)
        if not isinstance(evidence, str) or not 8 <= len(evidence) <= 512 or any(ord(c) < 32 for c in evidence):
            raise VPNError('Record a bounded independent measurement reference.')
        now = self.clock()
        if isinstance(observed_at, bool) or not now - 300 <= observed_at <= now + 5:
            raise VPNError('A fresh independent measurement is required.')
        with self._db(True) as db:
            row = self._server(db, ident)
            matches = country == row['country']
            db.execute('''UPDATE vpn_servers SET exit4=?,exit6=?,verified_country=?,verified_at=?,evidence=?,enabled=?
                WHERE id=?''', (exit4, exit6, country, observed_at, evidence, int(matches), ident))
            if not matches:
                self._revoke_server(db, ident)
        return {'server_id': ident, 'country_matches': matches, 'enabled': matches}

    def accept_proof(self, ident, claims):
        """Consume a cryptographically verified one-use independent measurement before enabling this exit."""
        with self._db(True) as db:
            db.execute('DELETE FROM vpn_probe_replays WHERE expires<=?', (self.clock(),))
            try:
                db.execute('INSERT INTO vpn_probe_replays VALUES (?,?)', (claims['nonce'], claims['exp']))
            except sqlite3.IntegrityError:
                raise VPNError('Independent VPN exit proof was already consumed.') from None
        return self.verify_exit(ident, country=claims['country'], exit_ipv4=claims['exit_ipv4'],
                                exit_ipv6=claims['exit_ipv6'], evidence=claims['evidence'],
                                observed_at=claims['observed_at'])

    def disable(self, ident):
        with self._db(True) as db:
            self._server(db, ident)
            db.execute('UPDATE vpn_servers SET enabled=0 WHERE id=?', (ident,))
            self._revoke_server(db, ident)

    def _revoke_server(self, db, ident):
        db.execute('UPDATE vpn_leases SET revoked=1 WHERE server_id=?', (ident,))
        db.execute('DELETE FROM vpn_probe_leases WHERE server_id=?', (ident,))
        db.execute('UPDATE vpn_servers SET generation=generation+1 WHERE id=?', (ident,))

    def _server(self, db, ident):
        row = db.execute('SELECT * FROM vpn_servers WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise VPNError('Unknown VPN exit.')
        return row

    def _healthy(self, row):
        now = self.clock()
        return bool(row['enabled'] and row['verified_at'] is not None and
                    now - self.VERIFICATION_TTL <= row['verified_at'] <= now + 5 and
                    row['verified_country'] == row['country'] and row['health_at'] is not None and
                    now - self.HEALTH_TTL <= row['health_at'] <= now + 5)

    def _in_use(self, db, ident):
        return db.execute('SELECT COUNT(*) FROM vpn_leases WHERE server_id=? AND revoked=0 AND expires>?',
                          (ident, self.clock())).fetchone()[0]

    def catalog(self):
        out = []
        with self._db() as db:
            for row in db.execute('SELECT * FROM vpn_servers ORDER BY country,name,id'):
                used = self._in_use(db, row['id'])
                if self._healthy(row) and used < row['capacity']:
                    out.append({'id': row['id'], 'name': row['name'], 'country': row['country'],
                                'capacity': row['capacity'], 'in_use': used, 'protocol': 'wireguard',
                                'verified_at': int(row['verified_at'])})
        return {'servers': out, 'countries': sorted({r['country'] for r in out}), 'lease_seconds': self.LEASE_SECONDS}

    def provision(self, actor, device, public_key, country, request_id):
        wireguard_key(public_key); country_code(country)
        try:
            request_id = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            raise VPNError('A request UUID is required.') from None
        with self._db(True) as db:
            old = db.execute('SELECT * FROM vpn_leases WHERE request_id=?', (request_id,)).fetchone()
            if old is not None:
                old_server = self._server(db, old['server_id'])
                if old['actor'] != actor or old['device'] != device or old['public_key'] != public_key or old_server['country'] != country:
                    raise VPNError('Request identity does not match this device.')
                return self._lease(db, old['id'], actor, device)
            if db.execute('SELECT 1 FROM vpn_leases WHERE device=? AND revoked=0 AND expires>?',
                          (device, self.clock())).fetchone():
                raise VPNError('Disconnect the existing VPN lease first.')
            if db.execute('SELECT 1 FROM vpn_leases WHERE public_key=? AND revoked=0 AND expires>?',
                          (public_key, self.clock())).fetchone():
                raise VPNError('A VPN key is already assigned to an active device.')
            if db.execute('SELECT 1 FROM vpn_probe_leases WHERE public_key=? AND expires>?',
                          (public_key, self.clock())).fetchone():
                raise VPNError('An independent probe key cannot be reused by a customer.')
            choices = [(self._in_use(db, s['id']), s) for s in db.execute('SELECT * FROM vpn_servers WHERE country=?', (country,))
                       if self._healthy(s)]
            choices = [(used, s) for used, s in choices if used < s['capacity']]
            if not choices:
                raise VPNError('No healthy authorised VPN exit has capacity in this country.')
            _, server = min(choices, key=lambda pair: (pair[0] / pair[1]['capacity'], pair[1]['id']))
            taken = {r[0] for r in db.execute('SELECT address4 FROM vpn_leases WHERE server_id=? AND revoked=0 AND expires>?',
                                             (server['id'], self.clock()))}
            v4, v6 = ipaddress.ip_network(server['subnet4']), ipaddress.ip_network(server['subnet6'])
            offset = next((n for n in range(2, 252) if str(v4.network_address + n) + '/32' not in taken), None)
            if offset is None:
                raise VPNError('VPN address pool is exhausted.')
            ident, generation = uuid.uuid4().hex, server['generation'] + 1
            db.execute('UPDATE vpn_servers SET generation=? WHERE id=?', (generation, server['id']))
            db.execute('INSERT INTO vpn_leases VALUES (?,?,?,?,?,?,?,?,?,?,0)',
                       (ident, request_id, server['id'], actor, device, public_key, str(v4.network_address + offset) + '/32',
                        str(v6.network_address + offset) + '/128', self.clock() + self.LEASE_SECONDS, generation))
            return self._lease(db, ident, actor, device)

    def _lease(self, db, ident, actor, device):
        row = db.execute('SELECT * FROM vpn_leases WHERE id=? AND actor=? AND device=?', (ident, actor, device)).fetchone()
        if row is None or row['revoked'] or row['expires'] <= self.clock():
            raise VPNError('VPN lease expired or was revoked.')
        server = self._server(db, row['server_id'])
        if not self._healthy(server):
            raise VPNError('VPN exit is unavailable or its country verification expired.')
        return {'lease_id': row['id'], 'server_id': server['id'], 'country': server['country'],
                'device_id': row['device'], 'actor': row['actor'], 'client_public_key': row['public_key'],
                'server_public_key': server['public_key'], 'endpoint': server['endpoint'],
                'addresses': [row['address4'], row['address6']], 'dns': json.loads(server['dns']),
                'routes': ['0.0.0.0/0', '::/0'], 'expected_exit_ipv4': server['exit4'],
                'expected_exit_ipv6': server['exit6'], 'verified_at': int(server['verified_at']),
                'expires_at': int(row['expires']),
                'state': 'ready' if server['acknowledged'] >= row['generation'] else 'awaiting_exit'}

    def lease(self, ident, actor, device):
        with self._db() as db:
            return self._lease(db, ident, actor, device)

    def renew(self, ident, actor, device):
        with self._db(True) as db:
            lease = self._lease(db, ident, actor, device)
            db.execute('UPDATE vpn_leases SET expires=? WHERE id=?', (self.clock() + self.LEASE_SECONDS, ident))
            return self._lease(db, ident, actor, device)

    def revoke(self, ident, actor=None, device=None):
        with self._db(True) as db:
            row = db.execute('SELECT * FROM vpn_leases WHERE id=?', (ident,)).fetchone()
            if row is None or actor is not None and (row['actor'] != actor or row['device'] != device):
                raise VPNError('Unknown VPN lease.')
            if not row['revoked']:
                db.execute('UPDATE vpn_leases SET revoked=1 WHERE id=?', (ident,))
                db.execute('UPDATE vpn_servers SET generation=generation+1 WHERE id=?', (row['server_id'],))

    def active_leases(self, server_id):
        with self._db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM vpn_leases WHERE server_id=? AND revoked=0 AND expires>?',
                                                    (server_id, self.clock()))]

    def manifest(self, ident):
        with self._db(True) as db:
            server = self._server(db, ident)
            expired = db.execute('UPDATE vpn_leases SET revoked=1 WHERE server_id=? AND revoked=0 AND expires<=?',
                                 (ident, self.clock())).rowcount
            expired += db.execute('DELETE FROM vpn_probe_leases WHERE server_id=? AND expires<=?',
                                  (ident, self.clock())).rowcount
            if expired:
                db.execute('UPDATE vpn_servers SET generation=generation+1 WHERE id=?', (ident,))
                server = self._server(db, ident)
            usable = bool(server['enabled'] and server['verified_at'] is not None and
                          self.clock() - self.VERIFICATION_TTL <= server['verified_at'] <= self.clock() + 5 and
                          server['verified_country'] == server['country'])
            peers = [{'public_key': row['public_key'], 'addresses': [row['address4'], row['address6']],
                      'expires_at': int(row['expires'])}
                     for row in db.execute('SELECT * FROM vpn_leases WHERE server_id=? AND revoked=0 AND expires>?',
                                           (ident, self.clock()))] if usable else []
            for probe in db.execute('SELECT * FROM vpn_probe_leases WHERE server_id=? AND expires>?', (ident, self.clock())):
                peers.append({'public_key': probe['public_key'],
                              'addresses': [str(ipaddress.ip_network(server['subnet4']).network_address + 252) + '/32',
                                            str(ipaddress.ip_network(server['subnet6']).network_address + 252) + '/128'],
                              'expires_at': int(probe['expires'])})
            return {'generation': server['generation'], 'expires_at': int(self.clock()) + self.HEALTH_TTL,
                    'server_public_key': server['public_key'], 'peers': peers}

    def heartbeat(self, ident, public_key, generation):
        with self._db(True) as db:
            server = self._server(db, ident)
            if public_key != server['public_key'] or isinstance(generation, bool) or generation != server['generation']:
                raise VPNError('Exit key or applied generation does not match.')
            db.execute('UPDATE vpn_servers SET health_at=?,acknowledged=? WHERE id=?',
                       (self.clock(), generation, ident))
        return {'accepted': True, 'next_poll_seconds': 2}
