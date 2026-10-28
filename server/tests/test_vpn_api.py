"""Real ASGI authentication/control-plane tests with synthetic exit measurements, not a real country VPN."""
import base64
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[2]))
from akeno_server import clock, db as dbmod
from akeno_server.auth import service as auth
from akeno_server.auth.tokens import b64e
from akeno_server.config import reset_config
from akeno_server.licensing import signing
from akeno_server.models import Device
from tests.helpers import bearer, register, register_device, make_codes

PREFIX = '/api/v1/network/vpn'


def key():
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def vpn(tmp_path, monkeypatch, keys):
    operator = secrets.token_urlsafe(48)
    probe = Ed25519PrivateKey.generate()
    probe_public = signing.raw_public(probe.public_key())
    probe_file = tmp_path / 'probe-keys.json'
    probe_file.write_text(json.dumps({'keys': {signing.kid_for(probe_public): b64e(probe_public)}}))
    for name, value in {
        'AKENO_VPN_ENABLED': 'true', 'AKENO_VPN_DATABASE_PATH': str(tmp_path / 'vpn.sqlite3'),
        'AKENO_VPN_OPERATOR_TOKEN': operator, 'AKENO_VPN_PROBE_KEYS_PATH': str(probe_file),
        'AKENO_NETWORK_SIGNING_KEY_PATH': str(tmp_path / 'network-signing.key'),
    }.items():
        monkeypatch.setenv(name, value)
    reset_config()
    from akeno_server.main import create_app
    app = create_app()
    with TestClient(app) as client:
        yield client, app.state.vpn_store, operator, probe


def player(c, email):
    tokens = register(c, email)
    with dbmod.transaction() as db:
        auth.user_by_email(db, email).email_verified_at = clock.utcnow()
    assert c.post('/api/v1/licenses/redeem', headers=bearer(tokens), json={'code': make_codes('monthly')[0]}).status_code == 200
    return tokens, register_device(c, tokens, email)


def exit_registration(c, token):
    args = dict(name='Synthetic exit fixture, not a real VPN', country='DE', public_key=key(),
                endpoint='93.184.215.14:51820', subnet4='10.204.1.0/24', subnet6='fd42:204:0:1::/64',
                dns=['1.1.1.1', '2606:4700:4700::1111'], capacity=2)
    r = c.post(PREFIX + '/operator/servers', headers={'Authorization': 'Bearer ' + token}, json=args)
    assert r.status_code == 201, r.text
    return r.json(), args


def proof(probe, reg, args, **changes):
    now = int(clock.utcnow().timestamp())
    payload = {'iss': 'akeno-vpn-probe', 'aud': reg['server_id'], 'server_id': reg['server_id'],
               'server_public_key': args['public_key'], 'country': args['country'],
               'exit_ipv4': '93.184.215.14', 'exit_ipv6': '2606:4700:4700::1111',
               'iat': now, 'exp': now + 120, 'observed_at': now, 'nonce': secrets.token_urlsafe(24),
               'evidence': 'SYNTHETIC UNIT TEST. No real egress or GeoIP measurement.'}
    payload.update(changes)
    return signing.sign(probe, signing.kid_for(signing.raw_public(probe.public_key())), 'akeno-vpn-exit-proof', payload)


def enable_exit(vpn):
    c, s, operator, probe = vpn
    reg, args = exit_registration(c, operator)
    r = c.post(PREFIX + '/operator/servers/' + reg['server_id'] + '/verify',
               headers={'Authorization': 'Bearer ' + operator}, json={'proof': proof(probe, reg, args)})
    assert r.status_code == 200, r.text
    headers = {'Authorization': 'Bearer ' + reg['agent_token']}
    manifest = c.get(PREFIX + '/agent/' + reg['server_id'] + '/peers', headers=headers)
    assert manifest.status_code == 200, manifest.text
    r = c.post(PREFIX + '/agent/' + reg['server_id'] + '/heartbeat', headers=headers,
               json={'public_key': args['public_key'], 'generation': manifest.json()['generation']})
    assert r.status_code == 200, r.text
    return reg, args, headers


def provision(c, tokens, device, **changes):
    return c.post(PREFIX + '/leases', headers=bearer(tokens), json={
        'device_id': device['id'], 'public_key': key(), 'country': 'DE', 'request_id': str(uuid.uuid4()), **changes})


def test_vpn_routes_absent_by_default(client):
    assert client.get(PREFIX + '/catalog').status_code == 404
    assert client.post(PREFIX + '/operator/servers', json={}).status_code == 404


def test_empty_catalog_and_no_country_until_independent_proof_plus_agent_ack(vpn):
    c, s, op, probe = vpn
    t, d = player(c, 'player@example.test')
    headers, query = bearer(t), {'device_id': d['id']}
    assert c.get(PREFIX + '/catalog', headers=headers, params=query).json()['countries'] == []
    reg, args = exit_registration(c, op)
    assert c.get(PREFIX + '/catalog', headers=headers, params=query).json()['countries'] == []
    # The operator token and a JSON country cannot mint the separate independent probe attestation.
    path = PREFIX + '/operator/servers/' + reg['server_id'] + '/verify'
    assert c.post(path, headers={'Authorization': 'Bearer ' + op}, json={'country': 'DE'}).status_code == 422
    assert c.post(path, headers={'Authorization': 'Bearer ' + op}, json={'proof': 'made-up'}).status_code == 400
    assert provision(c, t, d).status_code == 400


def test_authentication_license_device_binding_and_extra_fields(vpn):
    c, s, op, probe = vpn
    t, d = player(c, 'player@example.test')
    other, od = player(c, 'other@example.test')
    assert c.get(PREFIX + '/catalog', params={'device_id': d['id']}).status_code == 401
    assert c.get(PREFIX + '/catalog', headers=bearer(t), params={'device_id': od['id']}).status_code == 403
    assert provision(c, t, d, actor=other['user']['id']).status_code == 422
    u = register(c, 'unlicensed@example.test')
    ud = register_device(c, u, 'unlicensed')
    assert c.get(PREFIX + '/catalog', headers=bearer(u), params={'device_id': ud['id']}).status_code == 403
    with dbmod.transaction() as db:
        auth.user_by_email(db, 'unlicensed@example.test').email_verified_at = clock.utcnow()
    assert c.get(PREFIX + '/catalog', headers=bearer(u), params={'device_id': ud['id']}).status_code == 403


def test_operator_and_agent_purposes_are_separate(vpn):
    c, s, op, probe = vpn
    t, d = player(c, 'player@example.test')
    reg, args = exit_registration(c, op)
    for headers in ({}, bearer(t), {'Authorization': 'Bearer ' + reg['agent_token']}):
        assert c.post(PREFIX + '/operator/servers/' + reg['server_id'] + '/disable', headers=headers).status_code == 403
    for token in (op, t['access_token'], secrets.token_urlsafe(48)):
        assert c.get(PREFIX + '/agent/' + reg['server_id'] + '/peers',
                     headers={'Authorization': 'Bearer ' + token}).status_code == 403


def test_probe_forgery_replay_wrong_exit_and_country_mismatch(vpn):
    c, s, op, probe = vpn
    reg, args = exit_registration(c, op)
    path, headers = PREFIX + '/operator/servers/' + reg['server_id'] + '/verify', {'Authorization': 'Bearer ' + op}
    for token in (proof(Ed25519PrivateKey.generate(), reg, args), proof(probe, reg, args, server_public_key=key()),
                  proof(probe, reg, args, aud=uuid.uuid4().hex), proof(probe, reg, args, exp=1)):
        assert c.post(path, headers=headers, json={'proof': token}).status_code == 400
    token = proof(probe, reg, args)
    assert c.post(path, headers=headers, json={'proof': token}).status_code == 200
    assert c.post(path, headers=headers, json={'proof': token}).status_code == 400
    r = c.post(path, headers=headers, json={'proof': proof(probe, reg, args, country='US')})
    assert r.status_code == 200 and r.json()['enabled'] is False
    assert s.catalog()['countries'] == []


def test_full_control_flow_signed_lease_agent_ack_renew_and_revoke(vpn):
    c, s, op, probe = vpn
    reg, args, ah = enable_exit(vpn)
    t, d = player(c, 'player@example.test')
    catalog = c.get(PREFIX + '/catalog', headers=bearer(t), params={'device_id': d['id']}).json()
    assert catalog['countries'] == ['DE'] and len(catalog['servers']) == 1
    assert 'token' not in json.dumps(catalog) and 'exit4' not in json.dumps(catalog)
    r = provision(c, t, d)
    assert r.status_code == 201, r.text
    p = r.json()
    from akeno.network.credentials import verify_network_keys
    from akeno.licensing.crypto import public_key, verify
    with dbmod.session() as db:
        anchors = {row.kid: public_key(row.public_key) for row in signing.public_keys(db)}
    network_keys = verify_network_keys(p['network_keys'], anchors, purpose='akeno-vpn-tunnel')
    kid, claims = verify(p['credential'], network_keys, 'akeno-vpn-tunnel')
    assert kid not in anchors and claims['device_id'] == d['id']
    assert claims['country'] == 'DE' and claims['state'] == 'awaiting_exit' and claims['exp'] - claims['iat'] <= 45
    assert claims['server_public_key'] == args['public_key']
    manifest = c.get(PREFIX + '/agent/' + reg['server_id'] + '/peers', headers=ah).json()
    assert len(manifest['peers']) == 1
    assert c.post(PREFIX + '/agent/' + reg['server_id'] + '/heartbeat', headers=ah,
                  json={'public_key': args['public_key'], 'generation': manifest['generation']}).status_code == 200
    path = PREFIX + '/leases/' + p['lease_id']
    r = c.get(path, headers=bearer(t), params={'device_id': d['id']})
    assert r.status_code == 200 and r.json()['state'] == 'ready'
    assert c.post(path + '/renew', headers=bearer(t), json={'device_id': d['id']}).status_code == 200
    assert c.delete(path, headers=bearer(t), params={'device_id': d['id']}).status_code == 204
    assert c.get(PREFIX + '/agent/' + reg['server_id'] + '/peers', headers=ah).json()['peers'] == []
    assert c.post(path + '/renew', headers=bearer(t), json={'device_id': d['id']}).status_code == 400


def test_revoked_device_is_removed_from_next_agent_poll(vpn):
    c, s, op, probe = vpn
    reg, args, ah = enable_exit(vpn)
    t, d = player(c, 'player@example.test')
    p = provision(c, t, d).json()
    with dbmod.transaction() as db:
        db.get(Device, uuid.UUID(d['id'])).revoked_at = clock.utcnow()
    assert c.get(PREFIX + '/agent/' + reg['server_id'] + '/peers', headers=ah).json()['peers'] == []
    assert c.post(PREFIX + '/leases/' + p['lease_id'] + '/renew', headers=bearer(t),
                  json={'device_id': d['id']}).status_code == 403
