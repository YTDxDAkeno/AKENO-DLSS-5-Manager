"""Synthetic official-format catalogs; no public VPN connection or terms approval implied."""
import base64,csv,io
import pytest
from akeno.network.vpngate import HEADER,parse_catalog,countries,connect


def catalog(country='US',ip='8.8.8.8',profile='client\ndev tun\nremote 8.8.8.8 1194\n'):
    out=io.StringIO();w=csv.writer(out);w.writerow(['*vpn_servers']);w.writerow(['#'+HEADER[0],*HEADER[1:]])
    w.writerow(['fixture',ip,'10','20','1000','Test country',country,'2','1','1','1','public logs','fixture','',base64.b64encode(profile.encode()).decode()]);w.writerow(['*'])
    return out.getvalue().encode()


def test_actual_rows_only_determine_countries_and_nothing_is_connectable():
    servers=parse_catalog(catalog())
    assert countries(servers)==[('US','Test country')]
    assert all(not s.connectable for s in servers)
    assert countries(parse_catalog(catalog(country='TR')))==[('TR','Test country')]
    assert 'profile=' not in repr(servers[0])
    with pytest.raises(RuntimeError):connect(servers[0])


def test_malicious_profiles_and_private_addresses_are_not_exposed():
    for profile in ('client\nup evil.sh\n','client\nplugin evil.dll\n','client\n--config other.ovpn\n'):
        assert parse_catalog(catalog(profile=profile))==[]
    assert parse_catalog(catalog(ip='127.0.0.1'))==[]
    assert parse_catalog(catalog(country='XX<script>'))==[]
    with pytest.raises(ValueError):parse_catalog(b'not the official format')
