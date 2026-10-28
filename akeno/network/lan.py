"""Relay-only LAN data plane. Signed room leases control the peer set; peer home endpoints are never accepted.

Every member uses the controlled coordinator/relay. A missing relay is a connection failure, never a reason to
fall back to direct traffic. WireGuard authenticated endpoint roaming is detected and reset to the relay.
"""
from __future__ import annotations

import base64
import ipaddress
import time

from akeno.network.credentials import verify_peer, verify_network_keys

LAN_NETWORK = ipaddress.ip_network('10.203.0.0/16')
COORDINATOR_ADDRESS = '10.203.0.1'
KEEPALIVE = 25


class LanError(ValueError):
    pass


def format_endpoint(endpoint):
    host, port = endpoint
    return f'[{host}]:{port}' if ':' in host else f'{host}:{port}'


def verified_peers(result: dict, keys: dict, room: str, own_public: str, now=None) -> list[dict]:
    """Peers from a heartbeat answer whose own signed credentials verify; everything else is ignored."""
    peers, seen_keys, seen_ips = [], set(), set()
    for item in result.get('peers', [])[:64]:
        try:
            claims = verify_peer(item.get('credential'), keys, room, now)
        except (ValueError, AttributeError):
            continue
        if claims['public_key'] == own_public or claims['public_key'] in seen_keys or claims['virtual_ip'] in seen_ips:
            continue
        if claims['virtual_ip'] == COORDINATOR_ADDRESS:
            continue
        seen_keys.add(claims['public_key'])
        seen_ips.add(claims['virtual_ip'])
        peers.append({'public_key': claims['public_key'], 'virtual_ip': claims['virtual_ip'],
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


def interface_profile(private_key: str, virtual_ip: str, coordinator: dict, mtu=1380, listen_port=None) -> str:
    """The LAN tunnel: own /24 address, the coordinator as its only static peer (10.203.0.1/32, keepalive)."""
    address = ipaddress.ip_address(virtual_ip)
    if address not in LAN_NETWORK:
        raise LanError('Virtual address outside the LAN Play range.')
    subnet = ipaddress.ip_network(f'{address}/24', strict=False)
    host = coordinator['host']
    endpoint = f'[{host}]:{coordinator["port"]}' if ':' in host else f'{host}:{coordinator["port"]}'
    if listen_port is not None and (type(listen_port) is not int or not 1024 <= listen_port <= 65535):
        raise LanError('Invalid LAN listen port.')
    listen = f'ListenPort = {listen_port}\n' if listen_port is not None else ''
    return (f'[Interface]\nPrivateKey = {private_key}\nAddress = {address}/{subnet.prefixlen}\nMTU = {mtu}\n{listen}\n'
            f'[Peer]\nPublicKey = {coordinator["public_key"]}\nAllowedIPs = {COORDINATOR_ADDRESS}/32\n'
            f'Endpoint = {endpoint}\nPersistentKeepalive = {KEEPALIVE}\n')


class PathController:
    RELAY_GRACE = 15
    STALE = 185              # no handshake for longer than a session lives: connection lost
    SILENT = 2 * KEEPALIVE + 10   # a working path delivers the peer's keepalive every 25 s

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
                self.state[key] = {'virtual_ip': p['virtual_ip'], 'expires': p['expires'],
                                   'mode': None, 'since': now, 'handshake': None, 'rx': 0, 'tx': 0, 'rx_at': now}
                self._attempt(key, now)
            else:
                known['expires'] = p['expires']

    def expire(self):
        """Lease enforcement without the server: a member whose credential was not renewed is removed."""
        now = self.clock()
        for key in [k for k, s in self.state.items() if s['expires'] <= now]:
            self.backend.remove_peer(key)
            del self.state[key]

    def _attempt(self, key, now):
        # Re-adding the peer discards its current session, so WireGuard must complete a new handshake over exactly
        # this path. Only that handshake counts as success: an old session would keep "working" on stale state
        # (WireGuard rekeys only every 120 s), which made a moved member look unreachable.
        s = self.state[key]
        self.backend.remove_peer(key)
        self.backend.set_peer(key, self.relay, s['virtual_ip'])
        s['mode'], s['since'], s['rx_at'] = 'relay_trying', now, now

    def tick(self) -> dict[str, dict]:
        self.expire()
        now = self.clock()
        actual = self.backend.peers()
        for key, s in self.state.items():
            seen = actual.get(key, {})
            handshake = seen.get('latest_handshake') or None
            if seen.get('rx', 0) != s['rx']:
                s['rx_at'] = now
            s['handshake'], s['rx'], s['tx'] = handshake, seen.get('rx', 0), seen.get('tx', 0)
            fresh = handshake is not None and now - handshake < self.STALE
            via_relay = seen.get('endpoint') == self.relay
            if seen.get('endpoint') is not None and not via_relay:
                # WireGuard can roam after an authenticated packet. Refuse to treat an unapproved path as active.
                self._attempt(key, now)
            elif fresh and handshake >= s['since'] - 1 and now - s['rx_at'] <= self.SILENT:
                s['mode'] = 'relay'
            elif s['mode'] == 'relay' and (not fresh or now - s['rx_at'] > self.SILENT):
                self._attempt(key, now)
            elif s['mode'] == 'relay_trying' and now - s['since'] > self.RELAY_GRACE and not fresh:
                self._attempt(key, now)

        return {k: {kk: v for kk, v in s.items() if kk not in ('since', 'rx_at')} for k, s in self.state.items()}


class LanControl:
    """The real control plane of a session: the existing sign-in and device (LanClient) and the server's signed
    credentials. Only verified data leaves this class; this PC's private key never leaves the PC."""

    def __init__(self, client, room: str):
        self.client, self.room = client, room

    def refresh(self) -> dict:
        result = self.client.heartbeat(self.room)              # verifies this PC's own credential
        identity = self.client._identity()
        keys = verify_network_keys(result.get('network_keys'), self.client.license.trusted_keys())
        own = verify_peer(result['credential'], keys, self.room)
        return {'self': {'public_key': identity['public_key'], 'private_key': identity['private_key'],
                         'virtual_ip': own['virtual_ip']},
                'coordinator': coordinator_of(result),
                'peers': verified_peers(result, keys, self.room, identity['public_key'])}
