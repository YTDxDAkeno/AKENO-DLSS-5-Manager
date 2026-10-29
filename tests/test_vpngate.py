"""VPN Gate provider: catalog parsing, cache, availability and candidate ranking (synthetic official-format CSV)."""
import base64
import csv
import io
import json

import pytest

from akeno.network import vpngate
from akeno.network.vpngate import HEADER, CatalogError, VPNGateProvider, countries, parse_catalog
import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM_KEY = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption()).decode()


def certificate(name):
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    return (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(KEY.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
            .sign(KEY, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())


CA, CERT = certificate('ca'), certificate('client')


def vpn_profile(ip, proto='tcp', port=443, extra=''):
    return (f'dev tun\nproto {proto}\nremote {ip} {port}\ncipher AES-128-CBC\nauth SHA1\nclient\n{extra}'
            f'<ca>\n{CA}</ca>\n<cert>\n{CERT}</cert>\n<key>\n{PEM_KEY}</key>\n')


def row(ip='45.76.10.7', country='US', name='United States', ping='20', speed='50000000', sessions='5',
        uptime=str(48 * 3600 * 1000), host='public-vpn-1', proto='tcp', profile=None, score='1000'):
    blob = base64.b64encode((profile if profile is not None else vpn_profile(ip, proto)).encode()).decode()
    return [host, ip, score, ping, speed, name, country, sessions, uptime, '1', '1', '2weeks', 'Academic Use Only', '', blob]


def catalog(*rows):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['*vpn_servers'])
    w.writerow(['#' + HEADER[0], *HEADER[1:]])
    for r in rows or [row()]:
        w.writerow(r)
    w.writerow(['*'])
    return out.getvalue().encode()


def test_rows_become_servers_with_validated_profiles_and_transport():
    servers = parse_catalog(catalog(row(), row(ip='45.76.10.8', proto='udp')))
    assert {(s.ip, s.proto, s.port, s.connectable) for s in servers} == {('45.76.10.7', 'tcp', 443, True),
                                                                         ('45.76.10.8', 'udp', 443, True)}
    assert countries(servers) == [('US', 'United States')]
    assert servers[0].certificate_name == 'public-vpn-1.opengw.net' and 'profile=' not in repr(servers[0])


def test_malicious_profiles_and_bad_rows_are_never_connectable():
    servers = parse_catalog(catalog(row(profile=vpn_profile('45.76.10.7', extra='up evil.cmd\n')),
                                    row(ip='45.76.10.9', profile=vpn_profile('45.76.10.10')),
                                    row(ip='127.0.0.1'), row(ip='45.76.10.11', country='XX<b>'),
                                    row(ip='45.76.10.12', ping='-5'), row(ip='45.76.10.13', host='bad host')))
    assert [(s.ip, s.connectable) for s in servers] == [('45.76.10.7', False), ('45.76.10.9', False)]
    with pytest.raises(ValueError):
        parse_catalog(b'not the official format')


def test_duplicate_addresses_keep_the_better_entry():
    servers = parse_catalog(catalog(row(ping='200'), row(ping='10')))
    assert len(servers) == 1 and servers[0].ping_ms == 10


def test_candidates_are_only_from_the_chosen_country_and_ranked():
    servers = parse_catalog(catalog(
        row(ip='45.76.10.1', ping='150'), row(ip='45.76.10.2', ping='10'), row(ip='45.76.10.3', uptime='1000'),
        row(ip='45.76.10.4', speed='0'), row(ip='45.76.10.5', country='JP', name='Japan'),
        row(ip='45.76.10.6', ping='10', proto='udp')))
    us = VPNGateProvider.candidates(servers, 'US')
    assert [s.ip for s in us] == ['45.76.10.6', '45.76.10.2', '45.76.10.1']      # UDP preferred, unstable/no speed out
    assert all(s.country == 'US' for s in us)
    assert VPNGateProvider.candidates(servers, 'TR') == [] and VPNGateProvider.candidates(servers, 'us') == []
    assert [s.ip for s in VPNGateProvider.candidates(servers, 'US', exclude=['45.76.10.6'])][0] == '45.76.10.2'
    availability = VPNGateProvider.availability(servers)
    assert availability['US'] == {'name': 'United States', 'servers': 3, 'ping_ms': 10} and 'TR' not in availability


def test_list_is_cached_without_profiles_and_requests_are_rate_limited(tmp_path):
    calls, now = [], [1000.0]
    provider = VPNGateProvider(tmp_path / 'vpngate.json', fetch=lambda: calls.append(1) or catalog(), clock=lambda: now[0])
    first = provider.servers()
    assert len(calls) == 1 and provider.servers() is first                     # fresh within TTL
    now[0] += 30
    provider.servers(refresh=True)
    assert len(calls) == 1                                                      # refresh within MIN_INTERVAL ignored
    now[0] += 61
    provider.servers(refresh=True)
    assert len(calls) == 2
    cache = json.loads((tmp_path / 'vpngate.json').read_text())
    assert 'profile' not in json.dumps(cache) and 'PRIVATE KEY' not in json.dumps(cache)
    cached, fetched = provider.cached()
    assert cached[0].ip == '45.76.10.7' and cached[0].profile == '' and fetched == now[0]
    now[0] += VPNGateProvider.STALE_LIMIT + 1
    assert provider.cached() is None


def test_unreachable_or_malformed_list_raises_a_user_message():
    def down():
        raise CatalogError('The VPN Gate server list is not reachable right now.')
    with pytest.raises(CatalogError, match='not reachable'):
        VPNGateProvider(fetch=down).servers()
    with pytest.raises(CatalogError, match='unexpected format'):
        VPNGateProvider(fetch=lambda: b'<html>blocked</html>').servers()
    with pytest.raises(CatalogError):
        vpngate.fetch_raw('https://example.com/list.csv')


def test_reachability_probe_only_opens_a_tcp_connection_to_the_listed_address(monkeypatch):
    import socket
    calls = []

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(socket, 'create_connection', lambda addr, timeout: calls.append(addr) or Conn())
    tcp = vpngate.Server('h', '45.1.2.3', 'US', 'United States', 10, 1, 1, '2 weeks', 'client\nproto tcp\nremote 45.1.2.3 443\n')
    udp = vpngate.Server('h', '45.1.2.4', 'US', 'United States', 10, 1, 1, '2 weeks', 'client\nproto udp\nremote 45.1.2.4 1194\n')
    other = vpngate.Server('h', '45.1.2.5', 'US', 'United States', 10, 1, 1, '2 weeks', 'client\nproto tcp\nremote 9.9.9.9 443\n')
    assert vpngate.reachable(tcp) == 'reachable' and calls == [('45.1.2.3', 443)]
    assert vpngate.reachable(udp) == 'unknown' and vpngate.reachable(other) == 'unknown' and len(calls) == 1


def test_list_download_retries_once_after_a_network_error_but_not_after_an_http_error(monkeypatch):
    import urllib.error
    calls = []

    class Answer:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n): return b'list'

    class Opener:
        def __init__(self, failures): self.failures = failures
        def open(self, request, timeout):
            calls.append(request.full_url)
            if self.failures:
                error = self.failures.pop(0)
                raise error
            return Answer()
    monkeypatch.setattr(vpngate.urllib.request, 'build_opener', lambda *a: Opener([TimeoutError('read timed out')]))
    assert vpngate.fetch_raw(pause=lambda s: None) == b'list' and len(calls) == 2
    calls.clear()
    monkeypatch.setattr(vpngate.urllib.request, 'build_opener',
                        lambda *a: Opener([TimeoutError(), TimeoutError()]))
    with pytest.raises(CatalogError, match='not reachable'):
        vpngate.fetch_raw(pause=lambda s: None)
    calls.clear()
    monkeypatch.setattr(vpngate.urllib.request, 'build_opener',
                        lambda *a: Opener([urllib.error.HTTPError(vpngate.URL, 503, 'busy', {}, None)]))
    with pytest.raises(CatalogError, match='unexpected answer'):
        vpngate.fetch_raw(pause=lambda s: None)
    assert len(calls) == 1
