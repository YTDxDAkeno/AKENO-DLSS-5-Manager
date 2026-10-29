"""Free VPN session logic with stand-ins for OpenVPN and the native checks (no network, no Windows)."""
import pytest

from akeno.network import free_vpn
from akeno.network.free_vpn import FreeVPNSession, VerificationFailed, geo_verdict
from akeno.network.vpngate import CatalogError, VPNGateProvider, parse_catalog
from tests_support_free_vpn import catalog, row


class Memory:
    def __init__(self, value=None):
        self.value = dict(value or {})
        self.history = []
    def load(self):
        return dict(self.value)
    def save(self, value):
        self.value = dict(value)
        self.history.append(dict(value))


class Clock:
    def __init__(self):
        self.now = 1000.0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class Process:
    """Plays a scripted sequence of OpenVPN states per started server."""
    def __init__(self, script, log):
        self.script, self.log, self.states, self.stopped = script, log, [], False
    def start(self, config):
        assert 'verify-x509-name opengw.net name' in config and 'block-outside-dns' in config
        remote = next(l for l in config.splitlines() if l.startswith('remote '))
        self.server = remote.split()[1]
        self.log.append(('start', self.server))
        self.states = list(self.script.get(self.server, ['connected']))
    def state(self):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]
    def stop(self):
        self.log.append(('stop', self.server))
        return True


class Checks:
    def __init__(self, verdicts=None):
        self.verdicts = verdicts or {}
        self.current = None
    def baseline(self):
        return '20.1.1.1'
    def verify(self, country, baseline):
        outcome = self.verdicts.get(self.current, 'ok')
        if outcome != 'ok':
            raise VerificationFailed(outcome)
        return {'exit_ipv4': '45.76.99.1', 'confirmed_by': ['country.is', 'ipinfo'], 'contradicted_by': [],
                'country_verified': True, 'dns_verified': True, 'ipv6': 'blocked', 'latency_ms': 42.0}
    def inventory(self):
        return {'index': 7, 'source': '10.0.0.2'}
    def exit_address(self, index, source):
        return '45.76.99.1'


def session(servers_csv, script=None, verdicts=None, control=None, busy=False):
    clock, log = Clock(), []
    provider = VPNGateProvider(fetch=lambda: servers_csv, clock=clock)
    checks = Checks(verdicts)
    def factory():
        process = Process(script or {}, log)
        original = process.start
        def start(config):
            original(config)
            checks.current = process.server
        process.start = start
        return process
    s = FreeVPNSession(provider, factory, Memory(), Memory(), control or Memory(), checks=checks, clock=clock,
                       sleep=clock.sleep, busy=lambda: busy)
    return s, log, clock


def stop_after(s, polls):
    """Control store that asks the session to stop after ``polls`` reads while connected."""
    class Control(Memory):
        def load(self):
            if any(h.get('state') == 'connected' for h in s.status.history):
                self.reads = getattr(self, 'reads', 0) + 1
                if self.reads > polls:
                    return {'session_id': s.session_id, 'stop': True}
            return {'session_id': s.session_id, 'stop': False, 'switch': 0}
    return Control()


def test_connects_to_the_best_server_of_exactly_the_chosen_country_and_reports_verified_facts():
    s, log, _ = session(catalog(row('45.76.10.1', 'US'), row('45.76.10.2', 'JP', ping='1')))
    s.control = stop_after(s, 3)
    result = s.run('US')
    connected = [h for h in s.status.history if h['state'] == 'connected'][0]
    assert connected['exit_ipv4'] == '45.76.99.1' and connected['country'] == 'US' and connected['provider'] == 'VPN Gate'
    assert connected['server_ip'] == '45.76.10.1' and connected['latency_ms'] == 42.0 and connected['ipv6'] == 'blocked'
    assert ('start', '45.76.10.2') not in log                                  # never another country
    assert result['state'] == 'stopped' and log[-1] == ('stop', '45.76.10.1')
    assert s.journal.value == {'phase': 'disconnected'}


def test_country_without_servers_is_reported_honestly_without_trying_another_country():
    s, log, _ = session(catalog(row('45.76.10.2', 'JP')))
    result = s.run('TR')
    assert result['state'] == 'unavailable' and result['error'] == 'free_vpn_no_server' and log == []
    assert 'Right now no free VPN server is available' in free_vpn.ERRORS['free_vpn_no_server']


def test_failing_servers_are_skipped_until_one_connects():
    script = {'45.76.10.1': ['connecting', 'auth_failed'], '45.76.10.2': ['tls_failed']}
    s, log, _ = session(catalog(row('45.76.10.1', 'US', ping='5'), row('45.76.10.2', 'US', ping='10'),
                                row('45.76.10.3', 'US', ping='20')), script=script)
    s.control = stop_after(s, 1)
    s.run('US')
    assert [x for x in log if x[0] == 'start'] == [('start', '45.76.10.1'), ('start', '45.76.10.2'), ('start', '45.76.10.3')]
    assert [h for h in s.status.history if h['state'] == 'connected'][0]['server_ip'] == '45.76.10.3'


def test_all_servers_failing_ends_with_an_honest_error_and_no_running_process():
    script = {'45.76.10.1': ['auth_failed'], '45.76.10.2': ['connecting']}
    s, log, _ = session(catalog(row('45.76.10.1', 'US'), row('45.76.10.2', 'US')), script=script)
    result = s.run('US')
    assert result['state'] == 'failed' and result['error'] == 'free_vpn_all_failed'
    assert result['failures'] == {'auth_failed': 1, 'timeout': 1}
    assert not any(h.get('state') == 'connected' for h in s.status.history)
    assert [x[0] for x in log].count('start') == [x[0] for x in log].count('stop')


def test_mislabelled_country_skips_the_server_but_a_leak_ends_the_session():
    s, log, _ = session(catalog(row('45.76.10.1', 'US', ping='1'), row('45.76.10.2', 'US', ping='9')),
                        verdicts={'45.76.10.1': 'free_vpn_country_mismatch'})
    s.control = stop_after(s, 1)
    s.run('US')
    assert [h for h in s.status.history if h['state'] == 'connected'][0]['server_ip'] == '45.76.10.2'
    s, log, _ = session(catalog(row('45.76.10.1', 'US', ping='1'), row('45.76.10.2', 'US', ping='9')),
                        verdicts={'45.76.10.1': 'free_vpn_leak'})
    result = s.run('US')
    assert result['error'] == 'free_vpn_leak' and ('start', '45.76.10.2') not in log
    assert not any(h.get('state') == 'connected' for h in s.status.history)


def test_lost_server_switches_to_another_server_of_the_same_country():
    script = {'45.76.10.1': ['connected'] * 3 + ['exited'], '45.76.10.2': ['connected']}
    s, log, _ = session(catalog(row('45.76.10.1', 'US', ping='1'), row('45.76.10.2', 'US', ping='9'),
                                row('45.76.10.3', 'JP')), script=script)
    s.control = stop_after(s, 8)
    s.run('US')
    servers = [h['server_ip'] for h in s.status.history if h['state'] == 'connected']
    assert servers[0] == '45.76.10.1' and servers[-1] == '45.76.10.2'
    assert any(h['state'] == 'switching' for h in s.status.history) and ('start', '45.76.10.3') not in log


def test_user_switch_request_moves_to_the_next_server():
    s, log, _ = session(catalog(row('45.76.10.1', 'US', ping='1'), row('45.76.10.2', 'US', ping='9')))
    class Control(Memory):
        reads = 0
        def load(self):
            if any(h.get('state') == 'connected' for h in s.status.history):
                self.reads += 1
            return {'session_id': s.session_id, 'stop': self.reads > 6, 'switch': 1 if self.reads > 2 else 0}
    s.control = Control()
    s.run('US')
    assert ('start', '45.76.10.2') in log


def test_catalog_outage_and_busy_network_fail_closed_before_any_process():
    s, log, _ = session(b'')
    def down():
        raise CatalogError('x')
    s.provider = VPNGateProvider(fetch=down)
    assert s.run('US')['error'] == 'free_vpn_catalog_unavailable' and log == []
    s, log, _ = session(catalog(row('45.76.10.1', 'US')), busy=True)
    assert s.run('US')['error'] == 'free_vpn_busy' and log == []
    assert s.run('usa')['state'] == 'failed'


@pytest.mark.parametrize('answers,verified', [
    ({'ipify': ('1.2.3.4', None), 'ipinfo': ('1.2.3.4', 'US'), 'country.is': ('1.2.3.4', 'US')}, True),
    ({'ipinfo': ('1.2.3.4', 'US'), 'country.is': ('1.2.3.4', 'US'), 'ifconfig.co': ('1.2.3.4', 'GB')}, True),
    ({'ipinfo': ('1.2.3.4', 'US'), 'ifconfig.co': ('1.2.3.4', 'GB')}, False),
    ({'ipinfo': ('1.2.3.4', 'US')}, False),
    ({'ipinfo': ('1.2.3.4', 'JP'), 'country.is': ('1.2.3.4', 'JP')}, False),
])
def test_country_needs_two_independent_confirmations_and_a_majority(answers, verified):
    assert geo_verdict('US', answers)['country_verified'] is verified


def test_observers_disagreeing_about_the_address_is_unverified():
    with pytest.raises(VerificationFailed):
        geo_verdict('US', {'ipinfo': ('1.2.3.4', 'US'), 'country.is': ('1.2.3.5', 'US')})


def test_recovery_only_reports_stopped_after_akenos_openvpn_is_gone():
    journal, status = Memory({'phase': 'connected'}), Memory()
    assert free_vpn.recover(journal, status, kill=lambda: False)['state'] == 'recovery_required'
    assert journal.value['phase'] == 'connected'
    assert free_vpn.recover(journal, status, kill=lambda: True)['state'] == 'stopped'
    assert journal.value == {'phase': 'disconnected'}
