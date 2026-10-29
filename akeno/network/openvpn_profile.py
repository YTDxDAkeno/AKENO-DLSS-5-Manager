"""Untrusted OpenVPN provider profiles reduced to validated data, and AKENO's own fixed client configuration.

A community provider (VPN Gate) ships a complete OpenVPN profile per server. None of it is passed to OpenVPN. This
module reads only what a client technically needs and validates each value:

* transport: ``proto`` udp/tcp and exactly one ``remote`` whose address is the literal IP the catalog listed;
* data channel: ``cipher``/``data-ciphers``/``auth`` from small allowlists (no ``none``, no 64-bit block ciphers);
* identity: ``<ca>`` certificates, an optional client ``<cert>``/``<key>`` pair and an optional ``tls-auth``/
  ``tls-crypt`` static key, each parsed by a real X.509/PEM parser and re-serialised, never copied as text.

Every other directive is either a known harmless client setting that AKENO sets itself (ignored) or a reason to refuse
the server: scripts, plugins, file paths, management, proxies, compression, routes, ``setenv``, unknown inline blocks
and anything unknown. A server without a CA to authenticate it is refused.

``render`` writes AKENO's configuration from fixed directives plus the validated values: full tunnel for IPv4,
IPv6 blocked (VPN Gate servers are IPv4-only), DNS outside the tunnel blocked by OpenVPN's own WFP filter, server
pushes filtered (routes, IPv6, domains and environment from a server are ignored), no scripts.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

MAX_PROFILE = 64 * 1024
MAX_CA = 8

CIPHERS = {'AES-128-GCM', 'AES-256-GCM', 'CHACHA20-POLY1305', 'AES-128-CBC', 'AES-192-CBC', 'AES-256-CBC'}
AEAD = ('AES-256-GCM', 'AES-128-GCM', 'CHACHA20-POLY1305')
DIGESTS = {'SHA1', 'SHA256', 'SHA384', 'SHA512'}

# Client settings AKENO decides itself; a provider value is read and dropped.
IGNORED = {'client', 'tls-client', 'pull', 'nobind', 'persist-key', 'persist-tun', 'resolv-retry', 'verb', 'mute',
           'float', 'reneg-sec', 'ping', 'ping-restart', 'ping-exit', 'connect-retry', 'connect-retry-max',
           'explicit-exit-notify', 'mute-replay-warnings', 'remote-random', 'auth-nocache', 'server-poll-timeout',
           'hand-window', 'tun-mtu', 'mssfix', 'sndbuf', 'rcvbuf', 'fast-io', 'auth-retry', 'remote-cert-tls',
           'tls-version-min', 'key-direction', 'redirect-gateway', 'block-outside-dns', 'setenv-opt'}
# Values this module reads.
READ = {'proto', 'remote', 'dev', 'cipher', 'data-ciphers', 'data-ciphers-fallback', 'ncp-ciphers', 'auth',
        'auth-user-pass', 'tls-auth', 'tls-crypt'}
BLOCKS = {'ca', 'cert', 'key', 'tls-auth', 'tls-crypt'}


class ProfileRejected(ValueError):
    """The provider profile cannot be used safely; the server is skipped (the message names no secret)."""


@dataclass(frozen=True)
class SafeProfile:
    proto: str                                  # 'udp' or 'tcp'
    host: str                                   # literal IPv4/IPv6 address
    port: int
    ciphers: tuple[str, ...]                     # negotiable data ciphers, preferred first
    fallback: str | None                        # the provider's single cipher for servers without negotiation
    digest: str
    ca: tuple[str, ...] = field(repr=False)     # canonical PEM certificates
    cert: str | None = field(default=None, repr=False)
    key: str | None = field(default=None, repr=False)
    static_key: str | None = field(default=None, repr=False)   # canonical 2048-bit OpenVPN static key
    static_mode: str | None = None              # 'tls-auth' or 'tls-crypt'
    key_direction: int | None = None
    password_auth: bool = False                 # the provider requires username/password (not used by VPN Gate)


def _directive(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped or stripped[0] in '#;':
        return []
    if any(ch in stripped for ch in '"\'\\`'):
        raise ProfileRejected('The provider profile uses quoting or escapes.')
    parts = stripped.split()
    parts[0] = parts[0].lower()
    if parts[0].startswith('--'):
        parts[0] = parts[0][2:]
    return parts


def _certificates(text: str, limit: int) -> tuple[str, ...]:
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    pems = re.findall(r'-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----', text)
    rest = re.sub(r'-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----', '', text)
    if not pems or len(pems) > limit or rest.strip():
        raise ProfileRejected('The provider profile has an invalid certificate block.')
    out = []
    for pem in pems:
        try:
            cert = x509.load_pem_x509_certificate(pem.encode('ascii'))
        except ValueError as exc:
            raise ProfileRejected('The provider profile has an unreadable certificate.') from exc
        out.append(cert.public_bytes(Encoding.PEM).decode('ascii'))
    return tuple(out)


def _private_key(text: str) -> str:
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
    from cryptography.hazmat.primitives.serialization import (Encoding, NoEncryption, PrivateFormat,
                                                              load_pem_private_key)
    if len(re.findall(r'-----BEGIN [A-Z ]*PRIVATE KEY-----', text)) != 1:
        raise ProfileRejected('The provider profile has an invalid key block.')
    try:
        key = load_pem_private_key(text.strip().encode('ascii'), password=None)
    except (ValueError, TypeError) as exc:
        raise ProfileRejected('The provider profile has an unreadable client key.') from exc
    if isinstance(key, rsa.RSAPrivateKey) and key.key_size < 2048 and key.key_size != 1024:
        raise ProfileRejected('The provider client key is too weak.')
    if not isinstance(key, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey, ed25519.Ed25519PrivateKey)):
        raise ProfileRejected('The provider client key type is not supported.')
    return key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode('ascii')


def _static_key(text: str) -> str:
    match = re.fullmatch(r'\s*-----BEGIN OpenVPN Static key V1-----\s+([0-9a-fA-F\s]+?)\s*'
                         r'-----END OpenVPN Static key V1-----\s*', text)
    raw = re.sub(r'\s', '', match[1]) if match else ''
    if len(raw) != 512:
        raise ProfileRejected('The provider profile has an invalid static key.')
    lines = [raw[i:i + 32].lower() for i in range(0, 512, 32)]
    return '-----BEGIN OpenVPN Static key V1-----\n' + '\n'.join(lines) + '\n-----END OpenVPN Static key V1-----\n'


def parse(profile: str, expected_ip: str) -> SafeProfile:
    """Validated data of one provider profile for the server the catalog listed at ``expected_ip``."""
    if not isinstance(profile, str) or len(profile) > MAX_PROFILE or '\0' in profile:
        raise ProfileRejected('The provider profile is too large or not text.')
    expected = ipaddress.ip_address(expected_ip)
    blocks: dict[str, str] = {}
    values: dict[str, list[list[str]]] = {}
    current, lines = None, []
    for line in profile.replace('\r\n', '\n').split('\n'):
        stripped = line.strip()
        if current is not None:
            if stripped.lower() == f'</{current}>':
                blocks[current] = '\n'.join(lines)
                current, lines = None, []
            else:
                lines.append(stripped)
            continue
        tag = re.fullmatch(r'<([a-z0-9-]+)>', stripped.lower())
        if tag:
            if tag[1] not in BLOCKS or tag[1] in blocks:
                raise ProfileRejected('The provider profile has an unsupported or repeated inline block.')
            current = tag[1]
            continue
        if stripped.startswith('<'):
            raise ProfileRejected('The provider profile has a malformed inline block.')
        parts = _directive(line)
        if not parts:
            continue
        name = parts[0]
        if name in IGNORED:
            continue
        if name not in READ:
            raise ProfileRejected(f'The provider profile uses a directive AKENO does not allow ({name[:32]}).')
        values.setdefault(name, []).append(parts[1:])
    if current is not None:
        raise ProfileRejected('The provider profile has an unterminated inline block.')

    def single(name, default=None):
        found = values.get(name, [])
        if len(found) > 1:
            raise ProfileRejected(f'The provider profile repeats {name}.')
        return found[0] if found else default

    dev = single('dev', ['tun'])
    if dev != ['tun']:
        raise ProfileRejected('Only routed (tun) provider profiles are supported.')
    proto = (single('proto', ['udp']) or [''])[0].lower()
    proto = {'udp': 'udp', 'udp4': 'udp', 'tcp': 'tcp', 'tcp-client': 'tcp', 'tcp4': 'tcp',
             'tcp4-client': 'tcp'}.get(proto)
    if proto is None:
        raise ProfileRejected('The provider profile uses an unsupported transport.')
    remotes = values.get('remote', [])
    if len(remotes) != 1 or not 2 <= len(remotes[0]) <= 3:
        raise ProfileRejected('The provider profile must name exactly one server.')
    host, port = remotes[0][0], remotes[0][1]
    if len(remotes[0]) == 3:
        proto = {'udp': 'udp', 'tcp': 'tcp', 'tcp-client': 'tcp'}.get(remotes[0][2].lower())
        if proto is None:
            raise ProfileRejected('The provider profile uses an unsupported transport.')
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ProfileRejected('The provider profile names a host name instead of the listed address.') from exc
    if address != expected or not address.is_global:
        raise ProfileRejected('The provider profile points to a different server than the catalog lists.')
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ProfileRejected('The provider profile has an invalid port.')

    offered = []
    for name in ('data-ciphers', 'ncp-ciphers'):
        for value in values.get(name, []):
            if len(value) != 1:
                raise ProfileRejected('The provider cipher list is invalid.')
            offered += value[0].upper().split(':')
    cipher = single('cipher')
    fallback_value = single('data-ciphers-fallback')
    fallback = None
    for value in (cipher, fallback_value):
        if value is not None:
            if len(value) != 1:
                raise ProfileRejected('The provider cipher is invalid.')
            fallback = value[0].upper()
            offered.append(fallback)
    if any(name not in CIPHERS for name in offered):
        raise ProfileRejected('The provider profile requires a cipher AKENO does not allow.')
    ciphers = tuple(dict.fromkeys([c for c in AEAD] + [c for c in offered if c not in AEAD]))
    digest = (single('auth', ['SHA1']) or ['SHA1'])
    if len(digest) != 1 or digest[0].upper() not in DIGESTS:
        raise ProfileRejected('The provider profile requires a digest AKENO does not allow.')

    auth_user_pass = values.get('auth-user-pass', [])
    if any(value for value in auth_user_pass):
        raise ProfileRejected('The provider profile reads credentials from a file.')

    if 'ca' not in blocks:
        raise ProfileRejected('The provider profile cannot authenticate the server (no CA).')
    ca = _certificates(blocks['ca'], MAX_CA)
    cert = key = None
    if ('cert' in blocks) != ('key' in blocks):
        raise ProfileRejected('The provider profile has a certificate without its key.')
    if 'cert' in blocks:
        cert = _certificates(blocks['cert'], 1)[0]
        key = _private_key(blocks['key'])
    elif not auth_user_pass:
        raise ProfileRejected('The provider profile has no client authentication.')

    static_key = static_mode = direction = None
    for mode in ('tls-auth', 'tls-crypt'):
        inline = mode in blocks
        lines_for = values.get(mode, [])
        if lines_for and any(v and v[0].lower() != '[inline]' and not v[0].isdecimal() for v in lines_for):
            raise ProfileRejected('The provider profile reads a key file.')
        if inline:
            if static_mode:
                raise ProfileRejected('The provider profile has two static keys.')
            static_key, static_mode = _static_key(blocks[mode]), mode
    if static_mode == 'tls-auth':
        for line in profile.splitlines():
            parts = _directive(line)
            if parts and parts[0] == 'key-direction':
                if len(parts) != 2 or parts[1] not in ('0', '1'):
                    raise ProfileRejected('The provider key direction is invalid.')
                direction = int(parts[1])
        if direction is None:
            direction = 1
    return SafeProfile(proto, str(address), int(port), ciphers, fallback, digest[0].upper(), ca, cert, key,
                       static_key, static_mode, direction, bool(auth_user_pass))


ADAPTER = 'AKENO-FreeVPN'


def render(profile: SafeProfile, *, dns: tuple[str, ...] = ('1.1.1.1', '9.9.9.9'), server_name: str | None = None,
           adapter: str | None = ADAPTER, credentials: tuple[str, str] | None = None) -> str:
    """AKENO's complete client configuration: fixed directives plus validated values, nothing copied as text.

    File paths (log, status) are never written into the configuration: OpenVPN reads backslashes there as escapes.
    The caller passes them as fixed command-line arguments. ``server_name`` pins the certificate name of the
    server (the provider's CA alone accepts any certificate of that public CA; VPN Gate servers present a Let's
    Encrypt certificate for ``opengw.net`` without the TLS-server key usage OpenVPN's ``remote-cert-tls`` demands,
    so the name is the binding); ``adapter`` is AKENO's own TAP
    adapter so that routes and DNS checks concern exactly this tunnel."""
    for value in dns:
        address = ipaddress.ip_address(value)
        if address.version != 4 or not address.is_global:
            raise ValueError('Tunnel DNS must be public IPv4 addresses reached through the tunnel.')
    if credentials is not None and (len(credentials) != 2 or
                                    not all(re.fullmatch(r'[A-Za-z0-9._-]{1,64}', c or '') for c in credentials)):
        raise ValueError('Invalid provider credentials.')
    if profile.password_auth and credentials is None:
        raise ValueError('This provider needs its published fixed credentials.')
    lines = [
        '# AKENO free VPN: generated configuration. Provider values are validated; nothing else is accepted.',
        'client', 'dev tun', 'disable-dco',
        f'proto {"tcp-client" if profile.proto == "tcp" else "udp"}',
        f'remote {profile.host} {profile.port}', 'nobind',
        'resolv-retry 0', 'connect-retry 2 5', 'server-poll-timeout 10', 'hand-window 30',
        'tls-version-min 1.2',
        f'data-ciphers {":".join(profile.ciphers)}',
        f'auth {profile.digest}',
        'script-security 1', 'auth-nocache',
        # full tunnel: IPv4 through the server, every IPv6 packet answered locally (the server is IPv4-only)
        'redirect-gateway def1 ipv6', 'block-ipv6', 'block-outside-dns',
        *[f'dhcp-option DNS {value}' for value in dns],
        # what a server may push: its tunnel address, gateway and keepalive. Routes, IPv6, DNS servers, domains,
        # proxies, environment and messages from a server are dropped (AKENO sets DNS itself, through the tunnel).
        'pull-filter ignore "route "', 'pull-filter ignore "route-ipv6"', 'pull-filter ignore "ifconfig-ipv6"',
        'pull-filter ignore "redirect-private"', 'pull-filter ignore "dhcp-option"', 'pull-filter ignore "dns "',
        'pull-filter ignore "setenv"', 'pull-filter ignore "echo"', 'pull-filter ignore "register-dns"',
        'pull-filter ignore "block-outside-dns"',
        'ping 10', 'ping-restart 45', 'verb 3', 'mute 20',
    ]
    if profile.fallback:
        lines.append(f'data-ciphers-fallback {profile.fallback}')
    if adapter:
        if not re.fullmatch(r'[A-Za-z0-9-]{1,32}', adapter):
            raise ValueError('Invalid adapter name.')
        lines.append(f'dev-node {adapter}')
    if server_name:
        if not re.fullmatch(r'[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63}){1,4}', server_name):
            raise ValueError('Invalid server name.')
        lines.append(f'verify-x509-name {server_name} name')
    if credentials is not None:
        # the provider's published shared credentials (VPN Gate: vpn/vpn), inline: no file path, nothing secret
        lines += ['<auth-user-pass>', credentials[0], credentials[1], '</auth-user-pass>']
    lines += ['<ca>', *(pem.strip() for pem in profile.ca), '</ca>']
    if profile.cert:
        lines += ['<cert>', profile.cert.strip(), '</cert>', '<key>', profile.key.strip(), '</key>']
    if profile.static_mode:
        lines += [f'<{profile.static_mode}>', profile.static_key.strip(), f'</{profile.static_mode}>']
        if profile.static_mode == 'tls-auth':
            lines.append(f'key-direction {profile.key_direction}')
    return '\n'.join(lines) + '\n'
