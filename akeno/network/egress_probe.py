"""Fixed, source/interface-bound HTTPS exit measurements using normal TLS trust.

No proxy, redirect, arbitrary URL, external country claim or certificate bypass.
The caller separately verifies tunnel inventory and signed expected exit addresses.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl


class EgressError(ValueError):
    pass


def observe(source, interface):
    address = ipaddress.ip_address(source)
    if type(interface) is not int or not 1 <= interface <= 0xffffffff:
        raise EgressError('Invalid native VPN interface.')
    pool = ipaddress.ip_network('10.204.0.0/16' if address.version == 4 else 'fd42:204::/48')
    if address not in pool:
        raise EgressError('Exit measurement requires the assigned managed VPN source.')
    host = 'api.ipify.org' if address.version == 4 else 'api6.ipify.org'
    family = socket.AF_INET if address.version == 4 else socket.AF_INET6
    endpoints = socket.getaddrinfo(host, 443, family, socket.SOCK_STREAM)
    if not endpoints or len(endpoints) > 32:
        raise EgressError('The fixed exit observer is unavailable.')
    for af, kind, protocol, _, remote in endpoints[:4]:
        target = ipaddress.ip_address(remote[0])
        if af != family or target.version != address.version or not target.is_global or target.is_multicast:
            raise EgressError('The fixed exit observer resolved to an invalid destination.')
        raw = socket.socket(af, kind, protocol)
        try:
            raw.settimeout(5)
            raw.setsockopt(socket.IPPROTO_IP if address.version == 4 else socket.IPPROTO_IPV6, 31,
                           socket.htonl(interface) if address.version == 4 else interface)
            raw.bind((str(address), 0))
            raw.connect(remote)
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as secure:
                request = ('GET /?format=json HTTP/1.1\r\nHost: ' + host +
                           '\r\nAccept: application/json\r\nConnection: close\r\nUser-Agent: AKENO-VPN-probe/1\r\n\r\n')
                secure.sendall(request.encode('ascii'))
                response = http.client.HTTPResponse(secure)
                response.begin()
                if response.status != 200:
                    raise EgressError('The exit observer did not return a direct HTTPS result.')
                payload = response.read(4097)
                if len(payload) > 4096:
                    raise EgressError('The exit observation exceeded its bound.')
                result = json.loads(payload)
                if not isinstance(result, dict) or set(result) != {'ip'}:
                    raise EgressError('The exit observation has an invalid format.')
                observed = ipaddress.ip_address(result['ip'])
                if observed.version != address.version or not observed.is_global or observed.is_multicast:
                    raise EgressError('The observed exit address is invalid.')
                return str(observed)
        except (OSError, http.client.HTTPException):
            continue
        finally:
            raw.close()
    raise EgressError('Source-bound VPN exit measurement failed.')


# Fixed HTTPS observers for the free VPN: what the Internet sees as this tunnel's address, and which country three
# independent geolocation services assign to it. Each answer must name the same address; nothing else is read.
OBSERVERS = {
    'ipify': ('api.ipify.org', '/?format=json', 'ip', None),
    'ipinfo': ('ipinfo.io', '/json', 'ip', 'country'),
    'country.is': ('api.country.is', '/', 'ip', 'country'),
    'ifconfig.co': ('ifconfig.co', '/json', 'ip', 'country_iso'),
}


def fetch_json(name, source=None, interface=None, timeout=6.0):
    """One fixed observer over normal TLS trust, optionally bound to a tunnel source address and interface.

    Returns (address, country or None). Raises EgressError for anything unexpected."""
    host, path, ip_key, country_key = OBSERVERS[name]
    family = socket.AF_INET
    if source is not None:
        address = ipaddress.ip_address(source)
        if address.version != 4 or type(interface) is not int or not 1 <= interface <= 0xffffffff:
            raise EgressError('Invalid tunnel source for the exit measurement.')
    endpoints = socket.getaddrinfo(host, 443, family, socket.SOCK_STREAM)
    if not endpoints or len(endpoints) > 32:
        raise EgressError('The exit observer is unavailable.')
    for af, kind, protocol, _, remote in endpoints[:4]:
        target = ipaddress.ip_address(remote[0])
        if not target.is_global or target.is_multicast:
            raise EgressError('The exit observer resolved to an invalid destination.')
        raw = socket.socket(af, kind, protocol)
        try:
            raw.settimeout(timeout)
            if source is not None:
                raw.setsockopt(socket.IPPROTO_IP, 31, socket.htonl(interface))
                raw.bind((str(source), 0))
            raw.connect(remote)
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as secure:
                secure.sendall((f'GET {path} HTTP/1.1\r\nHost: {host}\r\nAccept: application/json\r\n'
                                'Connection: close\r\nUser-Agent: AKENO-VPN-probe/1\r\n\r\n').encode('ascii'))
                response = http.client.HTTPResponse(secure)
                response.begin()
                if response.status != 200:
                    raise EgressError('The exit observer did not answer.')
                payload = response.read(16385)
                if len(payload) > 16384:
                    raise EgressError('The exit observation exceeded its bound.')
                result = json.loads(payload)
                observed = ipaddress.ip_address(result[ip_key])
                if observed.version != 4 or not observed.is_global:
                    raise EgressError('The observed exit address is invalid.')
                country = None
                if country_key:
                    value = result.get(country_key)
                    country = value.upper() if isinstance(value, str) and len(value) == 2 and value.isalpha() else None
                return str(observed), country
        except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
            continue
        finally:
            raw.close()
    raise EgressError('The exit observer could not be reached.')
