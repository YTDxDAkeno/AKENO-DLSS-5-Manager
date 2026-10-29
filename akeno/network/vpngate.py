"""VPN Gate (University of Tsukuba) as the free VPN provider: the official server list and server selection.

VPN Gate publishes its volunteer relay servers as a CSV list and states: "you can use CSV List to make your own VPN
Gate client app". ``VPNGateProvider`` reads that list over HTTPS (no redirects, bounded size), keeps a short,
non-secret cache of the server metadata (no profiles), and ranks the servers of one country for a connection.

A listed server is a claim, not proof that it works: the session connects, checks the exit address and its country
independently, and moves on to the next candidate when a server fails. Every OpenVPN profile from the list is
untrusted input and goes through ``akeno.network.openvpn_profile`` before anything uses it.
"""
from __future__ import annotations

import base64
import csv
import io
import ipaddress
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

URL = 'https://www.vpngate.net/api/iphone/'
HEADER = ['HostName', 'IP', 'Score', 'Ping', 'Speed', 'CountryLong', 'CountryShort', 'NumVpnSessions', 'Uptime',
          'TotalUsers', 'TotalTraffic', 'LogType', 'Operator', 'Message', 'OpenVPN_ConfigData_Base64']
MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 5000
CERT_DOMAIN = 'opengw.net'          # VPN Gate server certificates are issued for <HostName>.opengw.net
MIN_UPTIME_MS = 10 * 60 * 1000      # a server that came up minutes ago is too likely to vanish again


class CatalogError(RuntimeError):
    """The server list could not be read; the message is shown to the user."""


@dataclass(frozen=True)
class Server:
    host: str
    ip: str
    country: str
    country_name: str
    ping_ms: int | None
    speed_bps: int
    sessions: int
    logging: str
    profile: str = field(repr=False)
    connectable: bool = False
    score: int = 0
    uptime_ms: int = 0
    operator: str = ''
    proto: str | None = None
    port: int | None = None

    @property
    def certificate_name(self) -> str | None:
        return f'{self.host.lower()}.{CERT_DOMAIN}' if re.fullmatch(r'[A-Za-z0-9-]{1,63}', self.host) else None

    def quality(self) -> float:
        """Higher is better: measured bandwidth per session, short ping, stable uptime (catalog values)."""
        ping = self.ping_ms if self.ping_ms is not None else 300
        mbps = self.speed_bps / 1e6
        per_session = mbps / (1 + self.sessions / 25)
        return per_session - ping / 10 + min(self.uptime_ms / 3.6e6 / 24, 7)


def _transport(profile: str):
    proto, remote = None, None
    for line in profile.splitlines():
        parts = line.strip().split()
        if len(parts) > 1 and parts[0] == 'proto':
            proto = 'tcp' if parts[1].lower().startswith('tcp') else 'udp' if parts[1].lower().startswith('udp') else None
        elif len(parts) > 2 and parts[0] == 'remote' and remote is None and parts[2].isdecimal():
            remote = (parts[1], int(parts[2]))
    return proto, remote


def parse_catalog(raw: bytes) -> list[Server]:
    """Every well-formed row of the official CSV; malformed rows are dropped, duplicates (same address) keep the
    better entry. Only rows whose profile passes AKENO's validation are connectable."""
    from akeno.network import openvpn_profile
    if len(raw) > MAX_BYTES:
        raise ValueError('VPN Gate catalog is too large.')
    text = raw.decode('utf-8-sig')
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or rows[0] != ['*vpn_servers']:
        raise ValueError('Invalid VPN Gate catalog marker.')
    if len(rows) < 2 or [v.lstrip('#') for v in rows[1]] != HEADER:
        raise ValueError('Unsupported VPN Gate catalog schema.')
    if len(rows) > MAX_ROWS + 3:
        raise ValueError('VPN Gate catalog has too many servers.')
    best: dict[str, Server] = {}
    for row in rows[2:]:
        if row == ['*'] or not row:
            continue
        try:
            if len(row) != len(HEADER):
                raise ValueError()
            p = dict(zip(HEADER, row))
            addr = ipaddress.ip_address(p['IP'])
            if not addr.is_global or not re.fullmatch('[A-Z]{2}', p['CountryShort']):
                raise ValueError()
            if not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', p['HostName']) or len(p['CountryLong']) > 80 \
                    or len(p['LogType']) > 200 or len(p['Operator']) > 300:
                raise ValueError()
            ping = None if p['Ping'] in ('-', '') else int(p['Ping'])
            speed, sessions = int(p['Speed']), int(p['NumVpnSessions'])
            score, uptime = int(p['Score'] or 0), int(p['Uptime'] or 0)
            if not 0 <= speed < 2 ** 63 or not 0 <= sessions < 1_000_000 or \
                    ping is not None and not 0 <= ping < 600_000 or not 0 <= uptime < 2 ** 63 or score < 0:
                raise ValueError()
            blob = base64.b64decode(p['OpenVPN_ConfigData_Base64'], validate=True)
            if len(blob) > openvpn_profile.MAX_PROFILE:
                raise ValueError()
            profile = blob.decode('utf-8')
            try:
                safe = openvpn_profile.parse(profile, str(addr))
                connectable, proto, port = True, safe.proto, safe.port
            except openvpn_profile.ProfileRejected:
                connectable = False
                proto, remote = _transport(profile)
                port = remote[1] if remote else None
            server = Server(p['HostName'], str(addr), p['CountryShort'], p['CountryLong'], ping, speed, sessions,
                            p['LogType'], profile, connectable, score, uptime, p['Operator'], proto, port)
        except (ValueError, UnicodeError, KeyError):
            continue
        current = best.get(server.ip)
        if current is None or (server.connectable, server.quality()) > (current.connectable, current.quality()):
            best[server.ip] = server
    return list(best.values())


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_raw(url: str = URL, timeout: float = 20.0, attempts: int = 2, pause=time.sleep) -> bytes:
    """The official list over HTTPS; one quiet second attempt after a timeout or network error (CI saw a 30 s
    read timeout once). An HTTP error answer is not retried."""
    if url != URL:
        raise CatalogError('Only the official VPN Gate server list is used.')
    import ssl
    opener = urllib.request.build_opener(NoRedirects(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, headers={'User-Agent': 'AKENO-Gaming-Suite-VPNGate-client/1',
                                                   'Accept-Encoding': 'identity'})
    for attempt in range(attempts):
        try:
            with opener.open(request, timeout=timeout) as response:
                if response.status != 200:
                    raise CatalogError('The VPN Gate server list returned an unexpected answer.')
                raw = response.read(MAX_BYTES + 1)
            break
        except CatalogError:
            raise
        except urllib.error.HTTPError as exc:
            raise CatalogError('The VPN Gate server list returned an unexpected answer.') from exc
        except (OSError, ValueError) as exc:
            if attempt + 1 >= attempts:
                raise CatalogError('The VPN Gate server list is not reachable right now. Check the Internet '
                                   'connection and try again.') from exc
            pause(3)
    if len(raw) > MAX_BYTES:
        raise CatalogError('The VPN Gate server list is larger than expected.')
    return raw


def fetch_catalog():
    return parse_catalog(fetch_raw())


class VPNGateProvider:
    """The free VPN provider: list, cache, availability and ranked candidates per country."""

    id = 'vpngate'
    name = 'VPN Gate'
    TTL = 10 * 60               # a fresh list is used for this long
    MIN_INTERVAL = 60           # never ask VPN Gate more often than this, even when the user presses refresh
    STALE_LIMIT = 60 * 60       # a cached list older than this is not shown as current availability

    def __init__(self, cache: Path | None = None, fetch=fetch_raw, clock=time.time):
        self.cache = Path(cache) if cache else None
        self.fetch, self.clock = fetch, clock
        self._servers: list[Server] | None = None
        self._fetched = 0.0
        self._attempted = 0.0

    def servers(self, refresh: bool = False) -> list[Server]:
        """Servers with profiles from VPN Gate (fresh within TTL). Raises ``CatalogError`` when unavailable."""
        now = self.clock()
        if self._servers is not None and not refresh and now - self._fetched < self.TTL:
            return self._servers
        if self._servers is not None and now - self._attempted < self.MIN_INTERVAL:
            return self._servers
        self._attempted = now
        try:
            servers = parse_catalog(self.fetch())
        except ValueError as exc:
            raise CatalogError('The VPN Gate server list has an unexpected format.') from exc
        self._servers, self._fetched = servers, now
        self._save(servers, now)
        return servers

    def _save(self, servers, now):
        if not self.cache:
            return
        rows = [{k: v for k, v in asdict(s).items() if k != 'profile'} for s in servers]
        try:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache.with_suffix('.tmp')
            tmp.write_text(json.dumps({'fetched': now, 'servers': rows}), encoding='utf-8')
            tmp.replace(self.cache)
        except OSError:
            pass

    def cached(self) -> tuple[list[Server], float] | None:
        """The last list without profiles, for showing availability without a network request (None if absent)."""
        if not self.cache:
            return None
        try:
            data = json.loads(self.cache.read_text(encoding='utf-8'))
            fetched = float(data['fetched'])
            servers = [Server(**{**{k: row[k] for k in row if k in Server.__dataclass_fields__}, 'profile': ''})
                       for row in data['servers'][:MAX_ROWS]]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if self.clock() - fetched > self.STALE_LIMIT:
            return None
        return servers, fetched

    @staticmethod
    def usable(server: Server) -> bool:
        return (server.connectable and server.certificate_name is not None and server.speed_bps > 0
                and server.uptime_ms >= MIN_UPTIME_MS and server.proto in ('tcp', 'udp'))

    @classmethod
    def availability(cls, servers: list[Server]) -> dict[str, dict]:
        """Per country: usable servers, best catalog ping and name. Countries without a usable server are absent."""
        out: dict[str, dict] = {}
        for server in servers:
            if not cls.usable(server):
                continue
            entry = out.setdefault(server.country, {'name': server.country_name, 'servers': 0, 'ping_ms': None})
            entry['servers'] += 1
            if server.ping_ms is not None and (entry['ping_ms'] is None or server.ping_ms < entry['ping_ms']):
                entry['ping_ms'] = server.ping_ms
        return out

    @classmethod
    def candidates(cls, servers: list[Server], country: str, limit: int = 6, exclude=()) -> list[Server]:
        """The best usable servers of exactly ``country`` (never another one), best first; UDP before TCP at
        similar quality because games prefer it."""
        if not re.fullmatch('[A-Z]{2}', country or ''):
            return []
        pool = [s for s in servers if s.country == country and cls.usable(s) and s.ip not in set(exclude)]
        pool.sort(key=lambda s: (-(s.quality() + (2 if s.proto == 'udp' else 0)), s.ip))
        return pool[:limit]


def reachable(server: Server, timeout: float = 3.0) -> str:
    """Whether this PC reaches the server's OpenVPN TCP port: 'reachable', 'unreachable' or 'unknown' (UDP is not
    probed). A plain TCP connection, closed at once: no VPN data."""
    import socket
    proto, remote = _transport(server.profile) if server.profile else (server.proto, (server.ip, server.port))
    if not proto or not proto.startswith('tcp') or not remote or not remote[1]:
        return 'unknown'
    try:
        if ipaddress.ip_address(remote[0]) != ipaddress.ip_address(server.ip):
            return 'unknown'
    except ValueError:
        return 'unknown'
    try:
        with socket.create_connection((server.ip, remote[1]), timeout=timeout):
            return 'reachable'
    except OSError:
        return 'unreachable'


def transport(server: Server):
    proto, remote = _transport(server.profile)
    return (proto,) + (remote or (None, None))


def countries(servers):
    return sorted({(s.country, s.country_name) for s in servers})
