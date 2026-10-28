"""Automatic AKENO VPN provisioning with device-local keys and independently verified server-country leases.

The privileged native session owns routing/handshake/renewal and must check both real egress addresses before it
reports connected. This class does not label a provisioned profile as an established Internet VPN.
"""
from __future__ import annotations

import base64
import ipaddress
import re
import time
import uuid

from akeno.licensing.crypto import verify
from akeno.network.client import LanClient, _missing_route
from akeno.network.credentials import verify_network_keys
from akeno.network.profiles import parse_wireguard, ProfileError


class VPNUnavailable(Exception):
    def __init__(self):
        super().__init__('AKENO VPN is not available on this server yet. No network settings were changed.')


class VPNClient(LanClient):
    """Shares LicenseManager's exact session/device; pass a separate DPAPI identity store for VPN."""

    def _call(self, method, path, body=None):
        try:
            with self.license._lock:
                return self.license._authed(self.license._api(), method, '/network/vpn/' + path, body)
        except Exception as exc:
            if _missing_route(exc):
                raise VPNUnavailable() from exc
            raise

    def catalog(self):
        i = self._identity()
        result = self._call('GET', 'catalog?device_id=' + i['device_id'])
        if not isinstance(result, dict) or not isinstance(result.get('servers'), list) or len(result['servers']) > 1000:
            raise ProfileError('Invalid AKENO VPN catalog.')
        servers = []
        for server in result['servers']:
            if not isinstance(server, dict) or not re.fullmatch('[A-Z]{2}', str(server.get('country'))) or \
                    not re.fullmatch('[0-9a-f]{32}', str(server.get('id'))) or server.get('protocol') != 'wireguard' or \
                    type(server.get('capacity')) is not int or type(server.get('in_use')) is not int or \
                    not 0 <= server['in_use'] < server['capacity'] <= 250:
                raise ProfileError('Invalid AKENO VPN catalog.')
            servers.append(dict(server))
        return {'servers': servers, 'countries': sorted({s['country'] for s in servers}), 'lease_seconds': 45}

    def _verify(self, result, country=None):
        i = self._identity()
        try:
            keys = verify_network_keys(result['network_keys'], self.license.trusted_keys(), purpose='akeno-vpn-tunnel')
            _, claims = verify(result['credential'], keys, 'akeno-vpn-tunnel')
            now = time.time()
            if claims['iss'] != 'akeno-vpn' or claims['aud'] != 'akeno-vpn-tunnel' or \
                    claims['device_id'] != i['device_id'] or claims['client_public_key'] != i['public_key'] or \
                    type(claims['iat']) is not int or type(claims['exp']) is not int or \
                    not claims['iat'] <= now < claims['exp'] or not 0 < claims['exp'] - claims['iat'] <= 45 or \
                    claims['expires_at'] != claims['exp'] or claims['state'] not in ('ready', 'awaiting_exit'):
                raise ValueError()
            uuid.UUID(claims['sub'])
            if not re.fullmatch('[0-9a-f]{32}', claims['lease_id']) or not re.fullmatch('[0-9a-f]{32}', claims['server_id']) or \
                    not re.fullmatch('[A-Z]{2}', claims['country']) or country and claims['country'] != country:
                raise ValueError()
            if len(base64.b64decode(claims['server_public_key'], validate=True)) != 32 or \
                    claims['routes'] != ['0.0.0.0/0', '::/0'] or len(claims['addresses']) != 2 or \
                    {ipaddress.ip_interface(a).version for a in claims['addresses']} != {4, 6}:
                raise ValueError()
            for address in claims['addresses']:
                interface = ipaddress.ip_interface(address)
                if not interface.ip.is_private or interface.network.prefixlen != interface.max_prefixlen or \
                        interface.version == 4 and interface.ip in ipaddress.ip_network('10.203.0.0/16'):
                    raise ValueError()
            for field, version in (('expected_exit_ipv4', 4), ('expected_exit_ipv6', 6)):
                address = ipaddress.ip_address(claims[field])
                if address.version != version or not address.is_global:
                    raise ValueError()
            if type(claims['verified_at']) is not int or not now - 86400 <= claims['verified_at'] <= now + 5 or \
                    not isinstance(claims['nonce'], str) or len(claims['nonce']) < 24:
                raise ValueError()
            # Validate the whole profile, including endpoint/DNS with the strict no-script parser.
            self._profile(claims, i)
            self.identity.save({**i, 'vpn_lease_id': claims['lease_id'], 'vpn_country': claims['country'],
                                'vpn_claims': claims})
            return claims
        except Exception:
            raise ProfileError('VPN tunnel permission is invalid, expired or belongs to another device or country.') from None

    def provision(self, country):
        if not isinstance(country, str) or not re.fullmatch('[A-Z]{2}', country):
            raise ProfileError('Select an available country.')
        i = self._identity()
        if i.get('vpn_lease_id'):
            raise ProfileError('Disconnect or recover the previous AKENO VPN lease first.')
        if i.get('vpn_request_country') != country or not i.get('vpn_request_id'):
            i = {**i, 'vpn_request_country': country, 'vpn_request_id': str(uuid.uuid4())}
            self.identity.save(i)  # Persist BEFORE HTTP so retry after a lost reply does not allocate twice.
        result = self._call('POST', 'leases', {'device_id': i['device_id'], 'public_key': i['public_key'],
                                             'country': country, 'request_id': i['vpn_request_id']})
        return self._verify(result, country)

    def _current(self):
        i = self._identity()
        if not re.fullmatch('[0-9a-f]{32}', str(i.get('vpn_lease_id'))):
            raise ProfileError('No AKENO VPN lease is active.')
        return i

    def poll(self):
        i = self._current()
        return self._verify(self._call('GET', 'leases/' + i['vpn_lease_id'] + '?device_id=' + i['device_id']), i['vpn_country'])

    status = poll

    def renew(self):
        i = self._current()
        return self._verify(self._call('POST', 'leases/' + i['vpn_lease_id'] + '/renew', {'device_id': i['device_id']}),
                            i['vpn_country'])

    def disconnect(self):
        i = self._current()
        self._call('DELETE', 'leases/' + i['vpn_lease_id'] + '?device_id=' + i['device_id'])
        self.clear_lease()
        return {'state': 'revoked'}

    def clear_lease(self):
        """After native cleanup: forget expired/revoked lease, never the device's protected key."""
        i = self._identity()
        self.identity.save({k: v for k, v in i.items() if not k.startswith('vpn_')})

    @staticmethod
    def _profile(claims, identity):
        text = '[Interface]\nPrivateKey = ' + identity['private_key'] + '\nAddress = ' + ', '.join(claims['addresses'])
        text += '\nDNS = ' + ', '.join(claims['dns']) + '\nMTU = 1280\n\n[Peer]\nPublicKey = ' + claims['server_public_key']
        text += '\nEndpoint = ' + claims['endpoint'] + '\nAllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 20\n'
        return parse_wireguard(text).text

    def profile_text(self, claims=None):
        """Return only to protected native/profile-store code, never diagnostics or cloud sync."""
        i = self._current()
        claims = claims or i.get('vpn_claims', {})
        if claims.get('lease_id') != i['vpn_lease_id'] or claims.get('state') != 'ready' or \
                not time.time() < claims.get('exp', 0):
            raise ProfileError('Wait for a fresh acknowledged VPN lease before starting its tunnel.')
        return self._profile(claims, i)

    @staticmethod
    def verify_observed_exit(claims, ipv4, ipv6):
        """Native egress probes must both traverse this tunnel; mismatch can never become Connected."""
        if str(ipaddress.ip_address(ipv4)) != claims['expected_exit_ipv4'] or \
                str(ipaddress.ip_address(ipv6)) != claims['expected_exit_ipv6']:
            raise ProfileError('Observed VPN exit addresses do not match the independently verified country server.')
        return {'ipv4': ipv4, 'ipv6': ipv6, 'country': claims['country']}
