"""LAN Play data plane: which WireGuard peers exist, and whether each one runs direct or through the relay.

Only members the server vouches for become peers: every peer needs its own fresh, signed, room-bound credential
(akeno.network.credentials), its AllowedIPs are exactly its own /32 inside 10.203.0.0/16, and it disappears when
its credential is not renewed (lease) or the server stops listing it (removal, ban, expiry). The coordinator is the
interface's only static peer; its handshakes prove this PC's endpoint and its relay is the fallback path.

The path is decided from what WireGuard itself reports, never assumed: a peer is "direct" when its last handshake
is fresh and its current endpoint is not the relay, "relay" when it is fresh through the relay. Direct is tried
first with the endpoint the coordinator proved for that member; after DIRECT_GRACE without a handshake the peer is
moved to the relay. A stale handshake (network loss, standby, new NAT mapping) starts the attempt again.
"""
from __future__ import annotations

import base64
import ipaddress
import time

from akeno.network.credentials import verify_peer

LAN_NETWORK = ipaddress.ip_network('10.203.0.0/16')
COORDINATOR_ADDRESS = '10.203.0.1'
KEEPALIVE = 25


class LanError(ValueError):
    pass


def _endpoint(value):
    """(host, port) from a credential; only literal public IP addresses, never names or private ranges."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2 or type(value[1]) is not int or not 1 <= value[1] <= 65535:
        raise LanError('Invalid peer endpoint.')
    address = ipaddress.ip_address(value[0])
    if not address.is_global:
        raise LanError('Peer endpoints must be public addresses.')
    return str(address), value[1]


def format_endpoint(endpoint):
    host, port = endpoint
    return f'[{host}]:{port}' if ':' in host else f'{host}:{port}'


def verified_peers(result: dict, keys: dict, room: str, own_public: str, now=None) -> list[dict]:
    """Peers from a heartbeat answer whose own signed credentials verify; everything else is ignored."""
    peers, seen_keys, seen_ips = [], set(), set()
    for item in result.get('peers', [])[:64]:
        try:
            claims = verify_peer(item.get('credential'), keys, room, now)
            endpoint = _endpoint(claims.get('endpoint'))
        except (ValueError, AttributeError):
            continue
        if claims['public_key'] == own_public or claims['public_key'] in seen_keys or claims['virtual_ip'] in seen_ips:
            continue
        if claims['virtual_ip'] == COORDINATOR_ADDRESS:
            continue
        seen_keys.add(claims['public_key'])
        seen_ips.add(claims['virtual_ip'])
        peers.append({'public_key': claims['public_key'], 'virtual_ip': claims['virtual_ip'], 'endpoint': endpoint,
                      'expires': claims['exp'], 'member': claims['sub'], 'device_id': claims['device_id']})
    return peers


def coordinator_of(result: dict) -> dict:
    c = result.get('coordinator')
    if not isinstance(c, dict):
        raise LanError('LAN Play is not available on this server.')
    try:
        key = base64.b64decode(c['public_key'], validate=True)
        if len(key) != 32:
            raise ValueError()
        host, port = c['host'], c['port']
        if not isinstance(host, str) or not 1 <= len(host) <= 253 or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise LanError('Invalid coordinator information.') from None
    return {'public_key': c['public_key'], 'host': host, 'port': port}


def interface_profile(private_key: str, virtual_ip: str, coordinator: dict, mtu=1380) -> str:
    """The LAN tunnel: own /24 address, the coordinator as its only static peer (10.203.0.1/32, keepalive)."""
    address = ipaddress.ip_address(virtual_ip)
    if address not in LAN_NETWORK:
        raise LanError('Virtual address outside the LAN Play range.')
    subnet = ipaddress.ip_network(f'{address}/24', strict=False)
    host = coordinator['host']
    endpoint = f'[{host}]:{coordinator["port"]}' if ':' in host else f'{host}:{coordinator["port"]}'
    return (f'[Interface]\nPrivateKey = {private_key}\nAddress = {address}/{subnet.prefixlen}\nMTU = {mtu}\n\n'
            f'[Peer]\nPublicKey = {coordinator["public_key"]}\nAllowedIPs = {COORDINATOR_ADDRESS}/32\n'
            f'Endpoint = {endpoint}\nPersistentKeepalive = {KEEPALIVE}\n')


class PathController:
    DIRECT_GRACE = 12        # WireGuard retries a handshake every 5 s: two attempts plus slack
    RELAY_GRACE = 15
    STALE = 185              # no handshake for longer than a session lives: connection lost

    def __init__(self, backend, relay_endpoint: tuple[str, int], subnet: str, clock=time.time):
        self.backend, self.relay, self.clock = backend, (str(ipaddress.ip_address(relay_endpoint[0])),
                                                        int(relay_endpoint[1])), clock
        self.subnet = ipaddress.ip_network(subnet)
        if not self.subnet.subnet_of(LAN_NETWORK):
            raise LanError('Room subnet outside the LAN Play range.')
        self.state: dict[str, dict] = {}

    def apply(self, peers: list[dict]):
        """Make WireGuard's peer set equal to the verified, unexpired members of this room."""
        now = self.clock()
        wanted = {}
        for p in peers:
            ip = ipaddress.ip_address(p['virtual_ip'])
            if ip in self.subnet and p['expires'] > now:
                wanted[p['public_key']] = p
        for key in [k for k in self.state if k not in wanted]:
            self.backend.remove_peer(key)
            del self.state[key]
        for key, p in wanted.items():
            known = self.state.get(key)
            if known is None or known['virtual_ip'] != p['virtual_ip']:
                if known is not None:
                    self.backend.remove_peer(key)
                self.state[key] = {'virtual_ip': p['virtual_ip'], 'endpoint': p['endpoint'], 'expires': p['expires'],
                                   'mode': None, 'since': now, 'handshake': None, 'rx': 0, 'tx': 0}
                self._attempt(key, now)
            else:
                known['expires'] = p['expires']
                if p['endpoint'] != known['endpoint']:
                    known['endpoint'] = p['endpoint']
                    if known['mode'] in ('direct_trying', 'direct') or known['mode'] is None:
                        self._attempt(key, now)     # the member roamed: follow its newly proven endpoint

    def expire(self):
        """Lease enforcement without the server: a member whose credential was not renewed is removed."""
        now = self.clock()
        for key in [k for k, s in self.state.items() if s['expires'] <= now]:
            self.backend.remove_peer(key)
            del self.state[key]

    def _attempt(self, key, now):
        s = self.state[key]
        if s['endpoint'] is not None:
            self.backend.set_peer(key, s['endpoint'], s['virtual_ip'])
            s['mode'] = 'direct_trying'
        else:
            self.backend.set_peer(key, self.relay, s['virtual_ip'])
            s['mode'] = 'relay_trying'
        s['since'] = now

    def tick(self) -> dict[str, dict]:
        self.expire()
        now = self.clock()
        actual = self.backend.peers()
        for key, s in self.state.items():
            seen = actual.get(key, {})
            handshake = seen.get('latest_handshake') or None
            s['handshake'], s['rx'], s['tx'] = handshake, seen.get('rx', 0), seen.get('tx', 0)
            fresh = handshake is not None and now - handshake < self.STALE
            via_relay = seen.get('endpoint') == self.relay
            if fresh and handshake >= s['since'] - 1:
                s['mode'] = 'relay' if via_relay else 'direct'
            elif s['mode'] == 'direct_trying' and now - s['since'] > self.DIRECT_GRACE:
                self.backend.set_peer(key, self.relay, s['virtual_ip'])
                s['mode'], s['since'] = 'relay_trying', now
            elif s['mode'] in ('direct', 'relay') and not fresh:
                self._attempt(key, now)                     # connection lost: start over, direct first
            elif s['mode'] == 'relay_trying' and now - s['since'] > self.RELAY_GRACE and s['endpoint'] is not None \
                    and not fresh:
                self._attempt(key, now)
        return {k: {kk: v for kk, v in s.items() if kk != 'since'} for k, s in self.state.items()}
