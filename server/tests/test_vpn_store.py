"""Synthetic exit inventory/lease tests. These do not assert real exits, GeoIP or native Windows networking."""
import base64
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from akeno_server.vpn.store import VPNStore, VPNError


def key():
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def inventory(tmp_path):
    now = [1800000000.0]
    s = VPNStore(tmp_path / 'vpn.sqlite3', lambda: now[0])
    registration = s.register(name='Synthetic local test exit', country='DE', public_key=key(),
                              endpoint='93.184.215.14:51820', subnet4='10.204.1.0/24',
                              subnet6='fd42:204:0:1::/64', dns=['1.1.1.1', '2606:4700:4700::1111'], capacity=2)
    return s, registration, now


def enable(s, reg):
    sid = reg['server_id']
    s.verify_exit(sid, country='DE', exit_ipv4='93.184.215.14', exit_ipv6='2606:4700:4700::1111',
                  evidence='SYNTHETIC TEST: not a measured exit', observed_at=int(s.clock()))
    manifest = s.manifest(sid)
    s.heartbeat(sid, manifest['server_public_key'], manifest['generation'])


def test_registration_is_disabled_and_random_agent_secret_is_hashed(inventory):
    s, reg, now = inventory
    assert s.catalog()['countries'] == []
    assert s.authenticate_agent(reg['server_id'], reg['agent_token'])
    assert not s.authenticate_agent(reg['server_id'], 'incorrect')
    assert reg['agent_token'].encode() not in s.path.read_bytes()
    assert 'agent_token' not in json.dumps(s.catalog())


def test_geoverification_and_live_agent_both_required(inventory):
    s, reg, now = inventory
    manifest = s.manifest(reg['server_id'])
    s.heartbeat(reg['server_id'], manifest['server_public_key'], 0)
    assert s.catalog()['servers'] == []
    enable(s, reg)
    assert s.catalog()['countries'] == ['DE']
    now[0] += s.HEALTH_TTL + 1
    assert s.catalog()['countries'] == []


def test_country_mismatch_disables_even_healthy_exit(inventory):
    s, reg, now = inventory
    enable(s, reg)
    result = s.verify_exit(reg['server_id'], country='US', exit_ipv4='93.184.215.14',
                           exit_ipv6='2606:4700:4700::1111', evidence='SYNTHETIC country mismatch', observed_at=int(now[0]))
    assert not result['country_matches']
    assert s.catalog()['countries'] == []


def provision(s, country='DE', device=None, request=None, public=None):
    return s.provision(str(uuid.uuid4()), device or str(uuid.uuid4()), public or key(), country,
                       request or str(uuid.uuid4()))


def test_no_exit_is_not_a_fake_country_success(inventory):
    s, reg, now = inventory
    enable(s, reg)
    with pytest.raises(VPNError, match='No healthy'):
        provision(s, 'TR')


def test_provision_requires_real_agent_ack_and_never_contains_private_key(inventory):
    s, reg, now = inventory
    enable(s, reg)
    p = provision(s)
    assert p['state'] == 'awaiting_exit'
    assert p['addresses'] == ['10.204.1.2/32', 'fd42:204:0:1::2/128']
    assert p['routes'] == ['0.0.0.0/0', '::/0'] and p['expires_at'] == int(now[0]) + 45
    manifest = s.manifest(reg['server_id'])
    assert manifest['peers'] == [{'public_key': p['client_public_key'], 'addresses': p['addresses'],
                                   'expires_at': p['expires_at']}]
    s.heartbeat(reg['server_id'], manifest['server_public_key'], manifest['generation'])
    assert s.lease(p['lease_id'], p['actor'], p['device_id'])['state'] == 'ready'
    assert 'private' not in json.dumps(p).lower()


def test_agent_cannot_ack_unknown_generation_or_key(inventory):
    s, reg, now = inventory
    enable(s, reg)
    for pub, gen in ((key(), 0), (s.manifest(reg['server_id'])['server_public_key'], 999),
                     (s.manifest(reg['server_id'])['server_public_key'], True)):
        with pytest.raises(VPNError):
            s.heartbeat(reg['server_id'], pub, gen)


def test_expired_lease_removed_from_agent_manifest_and_renewal_denied(inventory):
    s, reg, now = inventory
    enable(s, reg)
    p = provision(s)
    now[0] += 46
    assert s.manifest(reg['server_id'])['peers'] == []
    with pytest.raises(VPNError):
        s.renew(p['lease_id'], p['actor'], p['device_id'])


def test_lease_revoke_immediately_changes_desired_peers_and_cannot_renew(inventory):
    s, reg, now = inventory
    enable(s, reg)
    p = provision(s)
    before = s.manifest(reg['server_id'])['generation']
    s.revoke(p['lease_id'], p['actor'], p['device_id'])
    after = s.manifest(reg['server_id'])
    assert after['generation'] > before and after['peers'] == []
    with pytest.raises(VPNError):
        s.renew(p['lease_id'], p['actor'], p['device_id'])


def test_foreign_actor_or_device_cannot_read_renew_or_disconnect(inventory):
    s, reg, now = inventory
    enable(s, reg)
    p = provision(s)
    for action in (s.lease, s.renew, s.revoke):
        with pytest.raises(VPNError):
            action(p['lease_id'], str(uuid.uuid4()), p['device_id'])
        with pytest.raises(VPNError):
            action(p['lease_id'], p['actor'], str(uuid.uuid4()))


def test_idempotent_request_is_bound_to_same_actor_device_and_key(inventory):
    s, reg, now = inventory
    enable(s, reg)
    actor, device, public, request = str(uuid.uuid4()), str(uuid.uuid4()), key(), str(uuid.uuid4())
    first = s.provision(actor, device, public, 'DE', request)
    assert s.provision(actor, device, public, 'DE', request)['lease_id'] == first['lease_id']
    with pytest.raises(VPNError):
        s.provision(str(uuid.uuid4()), device, public, 'DE', request)
    with pytest.raises(VPNError):
        s.provision(actor, device, key(), 'DE', request)
    with pytest.raises(VPNError):
        s.provision(actor, device, public, 'US', request)


def test_one_device_and_key_cannot_occupy_multiple_leases(inventory):
    s, reg, now = inventory
    enable(s, reg)
    p = provision(s)
    for device, public in ((p['device_id'], key()), (str(uuid.uuid4()), p['client_public_key'])):
        with pytest.raises(VPNError):
            provision(s, device=device, public=public)


def test_capacity_checked_atomically_under_parallel_requests(inventory):
    s, reg, now = inventory
    enable(s, reg)

    def attempt(n):
        try:
            return provision(s)
        except VPNError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        leases = [r for r in pool.map(attempt, range(8)) if r]
    assert len(leases) == 2
    assert len({p['addresses'][0] for p in leases}) == 2
    assert s.catalog()['countries'] == []


def test_disabled_and_stale_geo_exit_removes_desired_peers(inventory):
    s, reg, now = inventory
    enable(s, reg)
    provision(s)
    s.disable(reg['server_id'])
    assert s.manifest(reg['server_id'])['peers'] == []
    enable(s, reg)
    provision(s)
    now[0] += s.VERIFICATION_TTL + 1
    assert s.manifest(reg['server_id'])['peers'] == []
    assert s.catalog()['countries'] == []


@pytest.mark.parametrize('change', [
    {'endpoint': '127.0.0.1:51820'}, {'endpoint': 'https://example.com'}, {'endpoint': '93.184.215.14:0'},
    {'subnet4': '10.203.1.0/24'}, {'subnet4': '127.1.0.0/24'}, {'subnet4': '10.204.0.0/16'},
    {'subnet6': 'fe80::/64'}, {'dns': ['127.0.0.1']}, {'capacity': True}, {'capacity': 251}, {'country': 'XX\n'},
])
def test_operator_input_rejects_unsafe_or_overlapping_config(inventory, change):
    s, reg, now = inventory
    args = dict(name='Other', country='DE', public_key=key(), endpoint='93.184.215.15:51820',
                subnet4='10.204.2.0/24', subnet6='fd42:204:0:2::/64', dns=['1.1.1.1'], capacity=2)
    with pytest.raises(VPNError):
        s.register(**(args | change))


def test_overlapping_registered_subnets_rejected(inventory):
    s, reg, now = inventory
    with pytest.raises(VPNError, match='overlap'):
        s.register(name='Other', country='US', public_key=key(), endpoint='93.184.215.15:51820',
                   subnet4='10.204.1.0/24', subnet6='fd42:204:0:2::/64', dns=['1.1.1.1'])
