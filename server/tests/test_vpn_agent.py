"""Exit-agent policy tests with an explicit native WireGuard stand-in; these are not public exit tests."""
import base64
import copy
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from akeno_server.vpn.agent import AgentError, Control, ExitAgent, Journal, WireGuard, private_read


def key(byte):
    return base64.b64encode(bytes([byte]) * 32).decode()


SERVER, A, B = key(1), key(2), key(3)


class Native:
    interface = 'akenoexit0'

    def __init__(self):
        self.values = {}
        self.calls = []

    def key(self):
        return SERVER

    def peers(self):
        return self.values.copy()

    def add(self, public, addresses):
        self.calls.append(('add', public, addresses))
        self.values[public] = addresses

    def remove(self, public):
        self.calls.append(('remove', public))
        self.values.pop(public, None)


class MemoryJournal:
    def __init__(self):
        self.value = None

    def load(self):
        return copy.deepcopy(self.value)

    def save(self, value):
        self.value = copy.deepcopy(value)


class API:
    def __init__(self):
        self.acks = []
        self.document = {'generation': 7, 'expires_at': 1020, 'server_public_key': SERVER,
            'peers': [{'public_key': A, 'addresses': ['10.204.1.2/32', 'fd42:204:0:1::2/128'], 'expires_at': 1030}]}

    def fetch(self):
        return copy.deepcopy(self.document)

    def heartbeat(self, generation, public):
        self.acks.append((generation, public))


@pytest.fixture
def stack():
    native, api, journal = Native(), API(), MemoryJournal()
    return ExitAgent(native, api, journal, SERVER, clock=lambda: 1000), native, api, journal


def test_reconciles_real_command_contract_and_removes_revoked_peer(stack):
    agent, native, api, journal = stack
    assert agent.tick() == {'generation': 7, 'active_peers': 1}
    assert native.values == {A: ('10.204.1.2/32', 'fd42:204:0:1::2/128')}
    assert api.acks == [(7, SERVER)]
    api.document['generation'] += 1
    api.document['peers'] = []
    assert agent.tick()['active_peers'] == 0
    assert not native.values and journal.value['managed'] == []


@pytest.mark.parametrize('field,value', [('expires_at', 1000), ('expires_at', 1031), ('generation', True),
                                     ('generation', -1), ('server_public_key', B), ('command', 'malicious')])
def test_malformed_manifest_revokes_existing_peers_without_running_instructions(stack, field, value):
    agent, native, api, _ = stack
    agent.tick()
    api.document[field] = value
    with pytest.raises(AgentError):
        agent.tick()
    assert not native.values and native.calls[-1] == ('remove', A)


@pytest.mark.parametrize('addresses', [
    ['0.0.0.0/0', 'fd42:204:0:1::2/128'],
    ['10.203.1.2/32', 'fd42:204:0:1::2/128'],
    ['10.204.1.0/24', 'fd42:204:0:1::2/128'],
    ['10.204.1.2/32', '::/0'],
    ['10.204.1.2/32'],
    ['fd42:204:0:1::2/128', '10.204.1.2/32'],
])
def test_peer_cannot_route_other_clients_internet_or_physical_lan(stack, addresses):
    agent, native, api, _ = stack
    api.document['peers'][0]['addresses'] = addresses
    with pytest.raises(AgentError):
        agent.tick()
    assert not native.values
    assert not any(call[0] == 'add' for call in native.calls)


def test_duplicate_addresses_fail_closed(stack):
    agent, native, api, _ = stack
    api.document['peers'].append({**api.document['peers'][0], 'public_key': B})
    with pytest.raises(AgentError):
        agent.tick()
    assert not native.values


def test_api_outage_immediately_removes_managed_peers(stack):
    agent, native, api, _ = stack
    agent.tick()

    def offline():
        raise AgentError('The authenticated control endpoint is unavailable.')

    api.fetch = offline
    with pytest.raises(AgentError):
        agent.tick()
    assert not native.values


def test_applied_peers_removed_when_heartbeat_fails(stack):
    agent, native, api, _ = stack

    def fail(*_):
        raise AgentError('The authenticated control endpoint is unavailable.')

    api.heartbeat = fail
    with pytest.raises(AgentError):
        agent.tick()
    assert native.calls == [('add', A, ('10.204.1.2/32', 'fd42:204:0:1::2/128')), ('remove', A)]
    assert not native.values


def test_restart_cleans_previous_recorded_peers_before_accepting_new_manifest(stack):
    agent, native, api, journal = stack
    agent.tick()
    replacement = ExitAgent(native, api, journal, SERVER, clock=lambda: 1000)
    replacement.startup()
    assert native.calls[-1] == ('remove', A) and not native.values


def test_refuses_takeover_of_unowned_interface(stack):
    agent, native, _, _ = stack
    native.values[B] = ('10.204.1.3/32', 'fd42:204:0:1::3/128')
    with pytest.raises(AgentError, match='unowned'):
        agent.startup()
    assert native.calls == [] and B in native.values


def test_native_uses_fixed_binary_and_argv_with_no_shell():
    calls = []

    def run(argv, **options):
        calls.append((argv, options))
        return SimpleNamespace(stdout=SERVER + '\n')

    wg = WireGuard(run=run)
    assert wg.key() == SERVER
    wg.add(A, ['10.204.1.2/32', 'fd42:204:0:1::2/128'])
    wg.remove(A)
    assert calls[1][0] == ['/usr/bin/wg', 'set', 'akenoexit0', 'peer', A, 'allowed-ips',
                           '10.204.1.2/32,fd42:204:0:1::2/128']
    for _, opts in calls:
        assert opts['check'] and opts['timeout'] == 5 and opts['stdin'] == subprocess.DEVNULL
        assert 'shell' not in opts
    with pytest.raises(AgentError):
        WireGuard('wg0')
    with pytest.raises(AgentError):
        WireGuard('akenoexit0; malicious')


def test_private_files_enforce_owner_permissions_and_no_symlink(tmp_path):
    secret = tmp_path / 'agent-token'
    secret.write_text('test-data')
    secret.chmod(0o600)
    assert private_read(secret, 32) == b'test-data'
    secret.chmod(0o644)
    with pytest.raises(AgentError):
        private_read(secret, 32)
    secret.chmod(0o600)
    alias = tmp_path / 'alias'
    alias.symlink_to(secret)
    with pytest.raises(AgentError):
        private_read(alias, 32)


def test_protected_journal_roundtrip_and_no_secret_material(tmp_path):
    journal = Journal(tmp_path / 'state' / 'peers.json')
    assert journal.load() is None
    value = {'interface': 'akenoexit0', 'public_key': SERVER, 'managed': [A]}
    journal.save(value)
    assert journal.load() == value
    assert (journal.path.stat().st_mode & 0o777) == 0o600
    assert set(json.loads(journal.path.read_text())) == {'interface', 'public_key', 'managed'}


def test_control_rejects_insecure_or_redirectable_origins(tmp_path):
    secret = tmp_path / 'agent-token'
    secret.write_text('x' * 43)
    secret.chmod(0o600)
    for url in ('http://example.com', 'https://u:p@example.com', 'https://example.com/redirect',
                'https://example.com?token=x'):
        with pytest.raises(AgentError):
            Control(url, 'a' * 32, secret)
    c = Control('https://example.com', 'a' * 32, secret)
    assert c.origin == 'https://example.com/api/v1/network/vpn/agent/' + 'a' * 32
    assert not any(isinstance(h, __import__('urllib.request').request.HTTPSHandler) and h._context
                   and not h._context.check_hostname for h in c.opener.handlers)
