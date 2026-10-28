"""VPN provisioning contracts with real signatures and in-memory identity/native stand-ins; no Internet VPN."""
import base64
import copy
import json
import os
import secrets
import threading
import time
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from akeno.licensing.crypto import kid_for, public_key
from akeno.network.profiles import ProfileError, parse_wireguard
from akeno.network.vpn import VPNClient


class MemoryIdentity:
    def __init__(self):
        self.value = {}

    def load(self):
        return copy.deepcopy(self.value)

    def save(self, value):
        self.value = copy.deepcopy(value)


def signer():
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip('=')
    return private, kid_for(encoded), encoded


def sign(key, kid, typ, payload):
    enc = lambda data: base64.urlsafe_b64encode(data).rstrip(b'=').decode()
    canonical = lambda data: json.dumps(data, sort_keys=True, separators=(',', ':')).encode()
    head = enc(canonical({'alg': 'EdDSA', 'crv': 'Ed25519', 'kid': kid, 'typ': typ}))
    body = enc(canonical(payload))
    return f"{head}.{body}.{enc(key.sign(f'{head}.{body}'.encode()))}"


class License:
    def __init__(self):
        self.state = {'device_id': str(uuid.uuid4())}
        self._lock = threading.RLock()
        self.anchor, self.anchor_id, self.anchor_public = signer()
        self.network, self.network_id, self.network_public = signer()
        self.responses, self.calls = [], []

    def trusted_keys(self):
        return {self.anchor_id: public_key(self.anchor_public)}

    def _api(self):
        return self

    def _authed(self, api, method, path, body):
        self.calls.append((method, path, body))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def response(self, identity, **changes):
        now = int(time.time())
        claims = {'iss': 'akeno-vpn', 'aud': 'akeno-vpn-tunnel', 'sub': str(uuid.uuid4()), 'actor': str(uuid.uuid4()),
                  'device_id': self.state['device_id'], 'client_public_key': identity['public_key'],
                  'server_id': uuid.uuid4().hex, 'lease_id': uuid.uuid4().hex,
                  'server_public_key': base64.b64encode(os.urandom(32)).decode(),
                  'endpoint': '93.184.215.14:51820', 'country': 'DE', 'addresses': ['10.204.1.2/32', 'fd42:204:0:1::2/128'],
                  'dns': ['1.1.1.1', '2606:4700:4700::1111'], 'routes': ['0.0.0.0/0', '::/0'],
                  'expected_exit_ipv4': '93.184.215.14', 'expected_exit_ipv6': '2606:4700:4700::1111',
                  'verified_at': now, 'state': 'awaiting_exit', 'iat': now, 'exp': now + 45, 'expires_at': now + 45,
                  'nonce': secrets.token_urlsafe(24)}
        claims.update(changes)
        claims['actor'] = claims['sub']
        delegated = {'iss': 'akeno-network', 'aud': 'akeno-network-key', 'iat': now, 'exp': now + 3600,
                     'keys': {self.network_id: self.network_public}, 'purposes': ['akeno-vpn-tunnel']}
        return {**claims, 'credential': sign(self.network, self.network_id, 'akeno-vpn-tunnel', claims),
                'network_keys': {'credential': sign(self.anchor, self.anchor_id, 'akeno-network-key-delegation', delegated),
                                 'keys': delegated['keys']}}


@pytest.fixture
def client():
    license, store = License(), MemoryIdentity()
    client = VPNClient(license, store)
    client._identity()
    return client, license, store


def test_generated_private_key_never_leaves_device_in_api_requests(client):
    c, license, store = client
    license.responses.append(license.response(store.load()))
    p = c.provision('DE')
    _, path, body = license.calls[-1]
    assert path == '/network/vpn/leases'
    assert set(body) == {'device_id', 'public_key', 'country', 'request_id'}
    assert store.load()['private_key'] not in json.dumps(license.calls)
    assert p['state'] == 'awaiting_exit'
    with pytest.raises(ProfileError, match='acknowledged'):
        c.profile_text()


def test_same_request_id_survives_lost_response_and_retry(client):
    c, license, store = client
    license.responses.append(TimeoutError('connection interrupted'))
    with pytest.raises(TimeoutError):
        c.provision('DE')
    first = license.calls[-1][2]['request_id']
    license.responses.append(license.response(store.load()))
    c.provision('DE')
    assert license.calls[-1][2]['request_id'] == first


def test_acknowledged_config_constructed_automatically_and_strictly_parsed(client):
    c, license, store = client
    license.responses.append(license.response(store.load(), state='ready'))
    claims = c.provision('DE')
    p = parse_wireguard(c.profile_text())
    assert p.routing == 'full_ipv4_ipv6'
    assert p.addresses == ('10.204.1.2/32', 'fd42:204:0:1::2/128')
    assert p.endpoint == '93.184.215.14:51820' and 'PostUp' not in p.text
    assert store.load()['private_key'] in p.text
    assert c.verify_observed_exit(claims, '93.184.215.14', '2606:4700:4700::1111')['country'] == 'DE'
    with pytest.raises(ProfileError):
        c.verify_observed_exit(claims, '1.1.1.1', '2606:4700:4700::1111')


@pytest.mark.parametrize('changes', [
    {'country': 'US'}, {'device_id': str(uuid.uuid4())}, {'client_public_key': base64.b64encode(os.urandom(32)).decode()},
    {'aud': 'akeno-lan-peer'}, {'iss': 'unknown'}, {'exp': 1}, {'iat': True}, {'exp': int(time.time()) + 60},
    {'addresses': ['127.0.0.1/32', '::1/128']}, {'addresses': ['10.203.1.2/32', 'fd42:204:0:1::2/128']},
    {'routes': ['0.0.0.0/0']}, {'endpoint': 'host:51820\nPostUp = calc.exe'},
    {'endpoint': '127.0.0.1:51820'}, {'dns': ['127.0.0.1']}, {'dns': []},
    {'server_public_key': base64.b64encode(bytes(32)).decode()},
    {'expected_exit_ipv4': '127.0.0.1'}, {'expected_exit_ipv6': 'fe80::1'}, {'verified_at': 1},
])
def test_forged_or_wrong_country_device_expiry_config_never_activates(client, changes):
    c, license, store = client
    license.responses.append(license.response(store.load(), **changes))
    with pytest.raises(ProfileError):
        c.provision('DE')
    assert 'vpn_lease_id' not in store.load()


def test_changed_plaintext_cannot_override_signed_config(client):
    c, license, store = client
    result = license.response(store.load(), state='ready')
    result['country'] = 'US'; result['endpoint'] = '127.0.0.1:1'
    license.responses.append(result)
    claims = c.provision('DE')
    assert claims['country'] == 'DE'
    assert parse_wireguard(c.profile_text()).endpoint == '93.184.215.14:51820'


def test_invalid_signatures_and_bare_network_keys_rejected(client):
    c, license, store = client
    result = license.response(store.load())
    other, _, _ = signer()
    result['credential'] = sign(other, license.network_id, 'akeno-vpn-tunnel', {'iss': 'akeno-vpn'})
    license.responses.append(result)
    with pytest.raises(ProfileError):
        c.provision('DE')
    result = license.response(store.load())
    del result['network_keys']['credential']
    license.responses.append(result)
    with pytest.raises(ProfileError):
        c.provision('DE')


def test_revocation_clears_lease_but_preserves_device_key(client):
    c, license, store = client
    private = store.load()['private_key']
    result = license.response(store.load())
    license.responses.extend([result, {}])
    c.provision('DE')
    assert c.disconnect()['state'] == 'revoked'
    assert store.load()['private_key'] == private
    assert not any(k.startswith('vpn_') for k in store.load())


def test_supplied_profile_claims_cannot_override_verified_claims(client):
    c, license, store = client
    license.responses.append(license.response(store.load(), state='ready'))
    claims = c.provision('DE')
    with pytest.raises(ProfileError):
        c.profile_text({**claims, 'endpoint': '127.0.0.1:51820'})


def test_country_catalog_does_not_invent_unavailable_countries(client):
    c, license, store = client
    license.responses.extend([{'servers': [], 'countries': ['TR', 'US']},
                              {'servers': [{'id': uuid.uuid4().hex, 'country': 'DE', 'name': 'Fixture',
                                            'capacity': 5, 'in_use': 4, 'protocol': 'wireguard'}], 'countries': ['TR']}])
    assert c.catalog()['countries'] == []
    assert c.catalog()['countries'] == ['DE']
