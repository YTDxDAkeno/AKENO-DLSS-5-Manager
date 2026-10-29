"""Untrusted provider profiles: only validated values reach AKENO's own configuration (synthetic certificates)."""
import datetime

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from akeno.network.openvpn_profile import ProfileRejected, parse, render

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def certificate(name='fixture'):
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(KEY.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
            .sign(KEY, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


PEM_KEY = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption()).decode()
STATIC = '-----BEGIN OpenVPN Static key V1-----\n' + '\n'.join(['ab' * 16] * 16) + '\n-----END OpenVPN Static key V1-----'


def profile(*extra, remote='remote 45.76.10.7 443', blocks=True):
    lines = ['dev tun', 'proto tcp', remote, 'cipher AES-128-CBC', 'auth SHA1', 'resolv-retry infinite', 'nobind',
             'persist-key', 'persist-tun', 'client', 'verb 3', '#auth-user-pass', ';http-proxy 1.2.3.4 8080', *extra]
    if blocks:
        lines += ['<ca>', certificate('ca'), '</ca>', '<cert>', certificate('client'), '</cert>', '<key>', PEM_KEY, '</key>']
    return '\r\n'.join(lines)


def test_vpn_gate_shaped_profile_becomes_akenos_own_configuration():
    safe = parse(profile(), '45.76.10.7')
    assert (safe.proto, safe.host, safe.port, safe.fallback, safe.digest) == ('tcp', '45.76.10.7', 443, 'AES-128-CBC', 'SHA1')
    assert safe.ciphers[:3] == ('AES-256-GCM', 'AES-128-GCM', 'CHACHA20-POLY1305') and 'AES-128-CBC' in safe.ciphers
    text = render(safe, server_name='opengw.net')
    for required in ('redirect-gateway def1 ipv6', 'block-ipv6', 'block-outside-dns', 'script-security 1',
                     'data-ciphers-fallback AES-128-CBC', 'remote 45.76.10.7 443',
                     'proto tcp-client', '<ca>', '<cert>', '<key>', 'dev-node AKENO-FreeVPN', 'disable-dco',
                     'verify-x509-name opengw.net name', 'pull-filter ignore "route "', 'pull-filter ignore "dhcp-option"'):
        assert required in text
    for absent in ('resolv-retry infinite', 'persist-tun', 'http-proxy', 'auth-user-pass', 'log ', '\\'):
        assert absent not in text
    assert 'BEGIN PRIVATE KEY' in text and 'BEGIN RSA PRIVATE KEY' not in text          # re-serialised, not copied


@pytest.mark.parametrize('line', ['up evil.cmd', 'plugin evil.dll', 'script-security 3', 'config other.ovpn',
    'management 127.0.0.1 7505', 'route 192.168.1.0 255.255.255.0', 'setenv PATH x', 'http-proxy 1.2.3.4 8080',
    'comp-lzo', 'compress lz4', 'cipher none', 'cipher BF-CBC', 'auth none', 'auth-user-pass secrets.txt',
    'tls-auth ta.key 1', 'ca other.crt', 'dev tap', 'log C:/Windows/evil.log', 'cd C:/', 'pkcs12 x.p12',
    'dhcp-option DNS 1.2.3.4', 'push "route 0.0.0.0"', 'remote 45.76.10.8 443', 'proto tcp-server',
    'tls-verify cmd', 'route-up cmd', 'ipchange cmd', 'learn-address cmd', 'writepid x', 'status x',
    'unknown-new-option 1', 'dev-node x', 'echo "x"'])
def test_dangerous_or_unknown_directives_refuse_the_server(line):
    with pytest.raises(ProfileRejected):
        parse(profile(line), '45.76.10.7')


@pytest.mark.parametrize('remote,expected', [('remote vpn.example.com 443', '45.76.10.7'),
    ('remote 45.76.10.9 443', '45.76.10.7'), ('remote 45.76.10.7 0', '45.76.10.7'),
    ('remote 10.0.0.1 443', '10.0.0.1'), ('remote 45.76.10.7 443 tcp-server', '45.76.10.7')])
def test_server_must_be_the_listed_public_address(remote, expected):
    with pytest.raises(ProfileRejected):
        parse(profile(remote=remote), expected)


def test_server_without_ca_or_client_authentication_is_refused():
    with pytest.raises(ProfileRejected, match='no CA'):
        parse(profile(blocks=False) + '\n<cert>\n' + certificate() + '\n</cert>\n<key>\n' + PEM_KEY + '\n</key>', '45.76.10.7')
    with pytest.raises(ProfileRejected, match='client authentication'):
        parse(profile(blocks=False) + '\n<ca>\n' + certificate() + '\n</ca>', '45.76.10.7')
    with pytest.raises(ProfileRejected):
        parse(profile(blocks=False) + '\n<ca>\nnot a certificate\n</ca>', '45.76.10.7')


@pytest.mark.parametrize('block', ['<connection>\nremote 1.2.3.4 1\n</connection>', '<auth-user-pass>\nu\np\n</auth-user-pass>',
                                   '<dh>\nx\n</dh>', '<ca>\nx\n', '<ca>'])
def test_unknown_unterminated_or_repeated_inline_blocks_refuse_the_server(block):
    with pytest.raises(ProfileRejected):
        parse(profile() + '\n' + block, '45.76.10.7')


def test_quoting_and_injection_attempts_are_refused():
    for line in ('cipher "AES-128-CBC"', "auth 'SHA1'", 'cipher AES-128-CBC\\', 'remote 45.76.10.7 443 `x`'):
        with pytest.raises(ProfileRejected):
            parse(profile(line), '45.76.10.7')
    with pytest.raises(ProfileRejected):
        parse(profile() + '\0', '45.76.10.7')
    with pytest.raises(ProfileRejected):
        parse('x' * 70000, '45.76.10.7')


def test_tls_auth_static_key_is_validated_and_rewritten():
    safe = parse(profile('key-direction 1', '<tls-auth>', STATIC, '</tls-auth>'), '45.76.10.7')
    assert safe.static_mode == 'tls-auth' and safe.key_direction == 1
    assert '<tls-auth>' in render(safe) and 'key-direction 1' in render(safe)
    with pytest.raises(ProfileRejected):
        parse(profile('<tls-crypt>', 'short', '</tls-crypt>'), '45.76.10.7')


def test_render_refuses_private_dns_and_paths_that_would_inject_directives():
    safe = parse(profile(), '45.76.10.7')
    with pytest.raises(ValueError):
        render(safe, dns=('192.168.1.1',))
    assert '<auth-user-pass>\nvpn\nvpn\n</auth-user-pass>' in render(safe, credentials=('vpn', 'vpn'))
    for bad in ({'server_name': 'x\nup evil.cmd'}, {'adapter': 'a b'}, {'server_name': 'UPPER.example.com'},
                {'credentials': ('vpn', 'x\nup evil.cmd')}, {'credentials': ('vpn',)}):
        with pytest.raises(ValueError):
            render(safe, **bad)
