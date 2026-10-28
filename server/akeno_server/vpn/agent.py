"""Explicitly provisioned VPN exit peer reconciler. Importing this module changes no network state.

The operator creates a dedicated interface and firewall first. The agent can only edit peers on that interface;
it cannot enable forwarding, run scripts, select interfaces supplied by clients, or install a default route.
The API's short manifest is an allow-list, not an instruction stream. Failed refreshes remove managed peers.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import re
import signal
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class AgentError(RuntimeError):
    """Deliberately contains no control response, credential, or subprocess output."""


def public_key(value):
    try:
        if not isinstance(value, str) or len(value) != 44:
            raise ValueError()
        raw = base64.b64decode(value, validate=True)
        if len(raw) != 32 or raw == b'\0' * 32 or base64.b64encode(raw).decode() != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise AgentError('Invalid WireGuard public key.') from None
    return value


class WireGuard:
    """No shell, no stdin configuration, no private keys, and one operator-selected dedicated interface."""
    def __init__(self, interface='akenoexit0', *, run=subprocess.run, executable='/usr/bin/wg'):
        if not re.fullmatch(r'akenoexit[0-9]{1,2}', interface):
            raise AgentError('A dedicated AKENO exit interface is required.')
        if executable != '/usr/bin/wg':
            raise AgentError('Use the distribution WireGuard binary.')
        self.interface, self.run, self.executable = interface, run, executable

    def _call(self, *args):
        try:
            result = self.run([self.executable, *args], stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, check=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            raise AgentError('The dedicated WireGuard interface could not be reconciled.') from None
        if not isinstance(result.stdout, str) or len(result.stdout) > 128 * 1024:
            raise AgentError('Invalid WireGuard status.')
        return result.stdout

    def key(self):
        return public_key(self._call('show', self.interface, 'public-key').strip())

    def peers(self):
        result = {}
        for line in self._call('show', self.interface, 'allowed-ips').splitlines():
            key, _, raw = line.partition('\t')
            public_key(key)
            if key in result:
                raise AgentError('Invalid WireGuard peer inventory.')
            try:
                addresses = () if raw.strip() == '(none)' else tuple(
                    str(ipaddress.ip_network(x.strip(), strict=True)) for x in raw.replace(',', ' ').split())
            except ValueError:
                raise AgentError('Invalid WireGuard peer inventory.') from None
            result[key] = addresses
        return result

    def add(self, key, addresses):
        public_key(key)
        checked = []
        for value in addresses:
            try:
                net = ipaddress.ip_network(value, strict=True)
                if net.prefixlen != net.max_prefixlen:
                    raise ValueError()
            except ValueError:
                raise AgentError('Only one host route per address is permitted.') from None
            checked.append(str(net))
        if not checked or len(checked) > 2:
            raise AgentError('Invalid peer address count.')
        self._call('set', self.interface, 'peer', key, 'allowed-ips', ','.join(checked))

    def remove(self, key):
        self._call('set', self.interface, 'peer', public_key(key), 'remove')


def private_read(path, limit):
    """Secrets/journal are local owner-only regular files, never symlinks or shared-writable files."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as fh:
            info = os.fstat(fh.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise AgentError('The agent file must be owner-only and owned by its runtime user.')
            data = fh.read(limit + 1)
    except OSError:
        raise AgentError('The protected agent file could not be read.') from None
    if len(data) > limit:
        raise AgentError('The protected agent file is too large.')
    return data


class Journal:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return None
        try:
            return json.loads(private_read(self.path, 64 * 1024))
        except (ValueError, TypeError):
            raise AgentError('The protected exit journal is damaged.') from None

    def save(self, data):
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise AgentError('The exit journal needs an owner-controlled directory.')
        temporary = self.path.with_name(self.path.name + '.tmp')
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, separators=(',', ':'))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise AgentError('The protected exit journal could not be saved.') from None
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AgentError('The control endpoint must not redirect.')


class Control:
    MAX_RESPONSE = 256 * 1024

    def __init__(self, origin, server_id, secret_file, *, allow_loopback_http=False):
        url = urllib.parse.urlsplit(origin)
        if not re.fullmatch('[a-f0-9]{32}', server_id) or url.username or url.password or url.query or url.fragment \
                or url.path not in ('', '/') or not url.hostname:
            raise AgentError('Invalid control origin or server identity.')
        local = url.hostname in ('127.0.0.1', '::1', 'localhost')
        if url.scheme != 'https' and not (allow_loopback_http and local and url.scheme == 'http'):
            raise AgentError('Verified HTTPS is required for the control endpoint.')
        self.origin = origin.rstrip('/') + '/api/v1/network/vpn/agent/' + server_id
        try:
            self.token = private_read(secret_file, 256).decode('ascii').strip()
        except UnicodeError:
            raise AgentError('Invalid agent credential file.') from None
        if not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', self.token):
            raise AgentError('Invalid agent credential file.')
        self.opener = urllib.request.build_opener(_NoRedirect())

    def _call(self, suffix, payload=None):
        request = urllib.request.Request(self.origin + suffix,
            data=None if payload is None else json.dumps(payload, separators=(',', ':')).encode(),
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'},
            method='GET' if payload is None else 'POST')
        try:
            with self.opener.open(request, timeout=10) as response:
                raw = response.read(self.MAX_RESPONSE + 1)
                if len(raw) > self.MAX_RESPONSE:
                    raise AgentError('The control response is too large.')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (OSError, urllib.error.URLError, ValueError):
            raise AgentError('The authenticated control endpoint is unavailable.') from None

    def fetch(self):
        return self._call('/peers')

    def heartbeat(self, generation, key):
        return self._call('/heartbeat', {'generation': generation, 'public_key': key})


class ExitAgent:
    MAX_PEERS = 250

    def __init__(self, native, control, journal, expected_key, *, ipv4_pool='10.204.0.0/16',
                 ipv6_pool='fd42:204::/48', clock=time.time):
        self.native, self.control, self.journal, self.clock = native, control, journal, clock
        self.expected_key = public_key(expected_key)
        self.pools = (ipaddress.IPv4Network(ipv4_pool), ipaddress.IPv6Network(ipv6_pool))
        # These pools must never overlap LAN Play (10.203/16), physical private networks, or default routes.
        if not self.pools[0].subnet_of(ipaddress.IPv4Network('10.204.0.0/16')) or \
                not self.pools[1].subnet_of(ipaddress.IPv6Network('fd42:204::/48')):
            raise AgentError('Use the dedicated AKENO VPN address pools.')
        self.managed = set()
        self.started = False

    def _record(self):
        self.journal.save({'interface': self.native.interface, 'public_key': self.expected_key,
                           'managed': sorted(self.managed)})

    def startup(self):
        if self.native.key() != self.expected_key:
            raise AgentError('The local WireGuard identity does not match this exit registration.')
        previous = self.journal.load()
        if previous is not None:
            if not isinstance(previous, dict) or set(previous) != {'interface', 'public_key', 'managed'} or \
                    previous['interface'] != self.native.interface or previous['public_key'] != self.expected_key or \
                    not isinstance(previous['managed'], list) or len(previous['managed']) > self.MAX_PEERS:
                raise AgentError('The protected exit journal does not match this exit.')
            self.managed = {public_key(key) for key in previous['managed']}
        if set(self.native.peers()) - self.managed:
            raise AgentError('Refusing to take over an interface containing unowned peers.')
        self.fail_closed()
        self.started = True

    def _manifest(self, document):
        now = self.clock()
        if not isinstance(document, dict) or set(document) != {'generation', 'expires_at', 'peers', 'server_public_key'} \
                or type(document['generation']) is not int or not 0 <= document['generation'] < 2 ** 63 or \
                type(document['expires_at']) is not int or not now < document['expires_at'] <= now + 30 or \
                document['server_public_key'] != self.expected_key or not isinstance(document['peers'], list) or \
                len(document['peers']) > self.MAX_PEERS:
            raise AgentError('Invalid or expired exit peer manifest.')
        desired, seen = {}, set()
        for peer in document['peers']:
            if not isinstance(peer, dict) or set(peer) != {'public_key', 'addresses', 'expires_at'} or \
                    type(peer['expires_at']) is not int or not now < peer['expires_at'] <= now + 45 or \
                    not isinstance(peer['addresses'], list) or len(peer['addresses']) != 2:
                raise AgentError('Invalid or expired exit peer lease.')
            key = public_key(peer['public_key'])
            if key == self.expected_key or key in desired:
                raise AgentError('Duplicate exit peer identity.')
            addresses = []
            for index, text in enumerate(peer['addresses']):
                try:
                    net = ipaddress.ip_network(text, strict=True)
                    pool = self.pools[index]
                    if net.version != pool.version or net.prefixlen != net.max_prefixlen or \
                            not net.subnet_of(pool) or str(net.network_address) in seen or \
                            net.network_address == pool.network_address or net.network_address == pool.broadcast_address:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise AgentError('An exit peer is outside its dedicated host address pool.') from None
                seen.add(str(net.network_address))
                addresses.append(str(net))
            desired[key] = tuple(addresses)
        return document['generation'], desired

    def fail_closed(self):
        """Remove only identities recorded as ours. Persist first so retries/restarts retain cleanup authority."""
        self._record()
        actual = self.native.peers()
        failure = False
        for key in self.managed.intersection(actual):
            try:
                self.native.remove(key)
            except AgentError:
                failure = True
        if failure:
            raise AgentError('Exit cleanup is incomplete; peer reconciliation will retry.')
        self.managed.clear()
        self._record()

    def tick(self):
        if not self.started:
            self.startup()
        try:
            generation, desired = self._manifest(self.control.fetch())
            actual = self.native.peers()
            if set(actual) - self.managed:
                raise AgentError('An unowned peer was added to the dedicated exit interface.')
            self.managed.update(desired)
            self._record()                 # cleanup rights survive a crash during wg set
            for key in set(actual) - set(desired):
                self.native.remove(key)
            for key, addresses in desired.items():
                if actual.get(key) != addresses:
                    self.native.add(key, addresses)
            if self.native.peers() != desired:
                raise AgentError('WireGuard did not apply the requested peer allow-list.')
            self.control.heartbeat(generation, self.expected_key)
            self.managed = set(desired)
            self._record()
            return {'generation': generation, 'active_peers': len(desired)}
        except Exception:
            self.fail_closed()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='Explicitly provisioned AKENO VPN exit peer agent')
    parser.add_argument('--control-origin', required=True)
    parser.add_argument('--server-id', required=True)
    parser.add_argument('--public-key', required=True)
    parser.add_argument('--secret-file', type=Path, required=True)
    parser.add_argument('--journal', type=Path, default=Path('/var/lib/akeno-exit/peers.json'))
    parser.add_argument('--interface', default='akenoexit0')
    parser.add_argument('--ipv4-pool', default='10.204.0.0/16')
    parser.add_argument('--ipv6-pool', default='fd42:204::/48')
    args = parser.parse_args(argv)
    agent = ExitAgent(WireGuard(args.interface), Control(args.control_origin, args.server_id, args.secret_file),
                      Journal(args.journal), args.public_key, ipv4_pool=args.ipv4_pool, ipv6_pool=args.ipv6_pool)
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            try:
                agent.tick()
            except AgentError as exc:
                print(str(exc), flush=True)       # only fixed messages; never command output or API content
            time.sleep(2)
    finally:
        if agent.started:
            agent.fail_closed()


if __name__ == '__main__':
    main()
