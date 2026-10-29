"""The free one-click VPN: a country, a VPN Gate server, OpenVPN, and verified results only.

``FreeVPNSession.run(country)`` runs in the elevated AKENO helper until the user disconnects:

1. reads the official VPN Gate list (fresh) and ranks the usable servers of exactly that country — never another
   country; none usable means an honest "not available";
2. measures the PC's normal public address (to prove later that it changed);
3. per candidate: AKENO's own OpenVPN configuration from the validated profile, start, wait for OpenVPN's own
   "Initialization Sequence Completed"; a refused, unreachable or failing server is skipped;
4. verification before "connected": tunnel adapter, routes and DNS of AKENO's adapter; the public address seen through
   the tunnel differs from the normal one; at least two independent geolocation services confirm the chosen
   country and confirmations outnumber contradictions; IPv6 does not leave outside the tunnel; DNS outside the
   tunnel is denied (native measurement). A mislabelled server is skipped; a DNS or IPv6 leak ends the session;
5. watches the tunnel, re-checks the exit address, switches to another server of the same country when the server
   goes away or the user asks, and always stops OpenVPN (which removes its routes and DNS) at the end.

The status store holds only what the page shows (state, country, server name and address, exit address, verified
sources, measured latency, timestamps, a fixed error code). No traffic or browsing data is recorded.
"""
from __future__ import annotations

import ipaddress
import re
import time
import uuid

from akeno.network.openvpn_profile import ADAPTER, ProfileRejected, parse, render

PROVIDER = 'VPN Gate'
CREDENTIALS = None                    # VPN Gate's OpenVPN servers authenticate the shared published client certificate
SERVER_NAME = 'opengw.net'            # certificate name every VPN Gate server presents (Let's Encrypt)
TUNNEL_DNS = ('1.1.1.1', '9.9.9.9')
CONSENT_VERSION = 1

ERRORS = {
    'free_vpn_no_server': 'Right now no free VPN server is available in this country.',
    'free_vpn_all_failed': 'None of the free servers in this country accepted a connection. Try again later.',
    'free_vpn_catalog_unavailable': 'The VPN Gate server list is not reachable right now.',
    'free_vpn_component_missing': 'The VPN component (official OpenVPN) is not installed.',
    'free_vpn_leak': 'A leak check failed, so the connection was closed for your safety.',
    'free_vpn_unverified': 'The connection could not be verified, so it was closed.',
    'free_vpn_connection_lost': 'The connection to the free server was lost and no other server answered.',
    'free_vpn_cleanup_incomplete': 'The VPN could not be removed completely. Use Recover.',
    'free_vpn_session_failed': 'The free VPN stopped unexpectedly. Use Recover before retrying.',
    'free_vpn_busy': 'Disconnect LAN Play and other VPN connections first.',
}


class VerificationFailed(Exception):
    def __init__(self, code: str, detail: str = ''):
        super().__init__(code)
        self.code, self.detail = code, detail


class _Stop(Exception):
    pass


def geo_verdict(country: str, answers: dict) -> dict:
    """answers: source -> (address, country or None); the exit address is what all answering sources agree on."""
    addresses = {address for address, _ in answers.values()}
    if len(addresses) != 1:
        raise VerificationFailed('free_vpn_unverified', 'observers disagree about the exit address')
    confirm = sorted(name for name, (_, code) in answers.items() if code == country)
    contradict = sorted(name for name, (_, code) in answers.items() if code and code != country)
    return {'exit_ipv4': addresses.pop(), 'confirmed_by': confirm, 'contradicted_by': contradict,
            'country_verified': len(confirm) >= 2 and len(confirm) > len(contradict)}


class WindowsChecks:
    """The native checks of one free-VPN tunnel on Windows (AKENO's TAP adapter only)."""

    def __init__(self, run=None, observe=None):
        if run is None:
            from akeno.network.lan_windows import powershell as run
        if observe is None:
            from akeno.network.egress_probe import fetch_json as observe
        self.run, self.observe = run, observe

    def baseline(self) -> str | None:
        try:
            return self.observe('ipify')[0]
        except Exception:
            return None

    def inventory(self) -> dict:
        import json
        script = (f"$n='{ADAPTER}'; $a=Get-NetAdapter -Name $n -ErrorAction Stop; "
                  "$ip=@(Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -AddressState Preferred "
                  "-ErrorAction Stop | ForEach-Object {$_.IPAddress}); "
                  "$r=@(); foreach($t in '1.1.1.1','8.8.8.8','203.0.113.10'){ $r+=@(Find-NetRoute -RemoteIPAddress $t "
                  "-ErrorAction Stop | Select-Object -ExpandProperty InterfaceAlias) }; "
                  "$d=@(Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 | "
                  "ForEach-Object {$_.ServerAddresses}); "
                  "@{index=[int]$a.ifIndex;status=[string]$a.Status;addresses=$ip;routes=$r;dns=$d} | ConvertTo-Json -Compress")
        try:
            raw = json.loads(self.run(script, timeout=20))
            addresses = [str(ipaddress.ip_address(a)) for a in raw['addresses']]
            if type(raw['index']) is not int or raw['index'] <= 0 or len(addresses) != 1:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise VerificationFailed('free_vpn_unverified', 'tunnel adapter inventory') from None
        if raw.get('status') != 'Up' or not raw['routes'] or any(alias != ADAPTER for alias in raw['routes']):
            raise VerificationFailed('free_vpn_leak', 'Internet routes are not on the VPN adapter')
        if sorted(raw.get('dns') or []) != sorted(TUNNEL_DNS):
            raise VerificationFailed('free_vpn_unverified', 'tunnel DNS servers')
        return {'index': raw['index'], 'source': addresses[0]}

    def ipv6_outside(self) -> str:
        """'blocked' when no IPv6 connection to the Internet can be made while the tunnel is up."""
        import socket
        for target in ('2606:4700:4700::1111', '2001:4860:4860::8888'):
            try:
                with socket.create_connection((target, 443), timeout=3):
                    return 'leaks'
            except OSError:
                continue
        return 'blocked'

    def dns(self) -> dict:
        from akeno.network.dns_probe import DNSVerificationError, verify
        try:
            return verify(ADAPTER, list(TUNNEL_DNS), encrypted_dns=False)
        except (OSError, DNSVerificationError) as exc:
            raise VerificationFailed('free_vpn_leak', 'DNS outside the tunnel was not denied') from exc

    def latency(self) -> float | None:
        from akeno.network.vpn_session import tunnel_latency
        return tunnel_latency(TUNNEL_DNS[0])

    def verify(self, country: str, baseline: str | None) -> dict:
        native = self.inventory()
        if self.ipv6_outside() != 'blocked':
            raise VerificationFailed('free_vpn_leak', 'IPv6 reaches the Internet outside the tunnel')
        answers = {}
        for name in ('ipify', 'ipinfo', 'country.is', 'ifconfig.co'):
            try:
                answers[name] = self.observe(name, native['source'], native['index'])
            except Exception:
                continue
        if 'ipify' not in answers and not answers:
            raise VerificationFailed('free_vpn_unverified', 'no exit observer answered through the tunnel')
        verdict = geo_verdict(country, answers)
        if baseline and verdict['exit_ipv4'] == baseline:
            raise VerificationFailed('free_vpn_leak', 'the public address did not change')
        if not verdict['country_verified']:
            raise VerificationFailed('free_vpn_country_mismatch', 'the exit is not confirmed in the chosen country')
        evidence = self.dns()
        return {**verdict, 'dns_verified': evidence.get('dns_verified') is True, 'ipv6': 'blocked',
                'denied_dns_paths': evidence.get('denied_paths'), 'latency_ms': self.latency()}

    def exit_address(self, index: int, source: str) -> str | None:
        try:
            return self.observe('ipify', source, index)[0]
        except Exception:
            return None


class FreeVPNSession:
    CONNECT_SECONDS = 35
    POLL_SECONDS = 1
    RECHECK_SECONDS = 90
    RESTART_GRACE = 30
    MAX_CANDIDATES = 6

    def __init__(self, provider, process_factory, journal, status, control, *, checks=None, clock=time.time,
                 sleep=time.sleep, busy=lambda: False):
        self.provider, self.process_factory = provider, process_factory
        self.journal, self.status, self.control = journal, status, control
        self.checks = checks or WindowsChecks()
        self.clock, self.sleep, self.busy = clock, sleep, busy
        self.process = None
        self.session_id = uuid.uuid4().hex
        self.connected_since = None

    # -- status ---------------------------------------------------------------------------------------------------
    def _status(self, state, country, server=None, error=None, **values):
        result = {'state': state, 'country': country, 'provider': PROVIDER, 'session_id': self.session_id,
                  'updated': self.clock()}
        if server is not None:
            result.update(server_host=server.host, server_ip=server.ip, server_proto=server.proto,
                          catalog_ping_ms=server.ping_ms)
        if error:
            result['error'] = error
        result.update({k: v for k, v in values.items() if v is not None})
        self.status.save(result)
        return result

    def _requests(self) -> dict:
        value = self.control.load() or {}
        return value if value.get('session_id') == self.session_id else {}

    def _check_stop(self):
        if self._requests().get('stop'):
            raise _Stop()

    # -- one server -----------------------------------------------------------------------------------------------
    def _start(self, server):
        config = render(parse(server.profile, server.ip), server_name=SERVER_NAME, credentials=CREDENTIALS,
                        dns=TUNNEL_DNS)
        self.process = self.process_factory()
        self.journal.save({'phase': 'connecting', 'session_id': self.session_id, 'server_ip': server.ip,
                           'created': self.clock()})
        self.process.start(config)
        deadline = self.clock() + self.CONNECT_SECONDS
        while self.clock() < deadline:
            self._check_stop()
            state = self.process.state()
            if state == 'connected':
                return 'connected'
            if state not in ('connecting', 'restarting'):
                return state
            self.sleep(self.POLL_SECONDS)
        return 'timeout'

    def _stop_process(self) -> bool:
        if self.process is None:
            return True
        ok = self.process.stop()
        self.process = None if ok else self.process
        return ok

    # -- the session ----------------------------------------------------------------------------------------------
    def run(self, country: str) -> dict:
        if not isinstance(country, str) or not re.fullmatch('[A-Z]{2}', country):
            return self._status('failed', country, error='free_vpn_no_server')
        if self.busy():
            return self._status('failed', country, error='free_vpn_busy')
        self.control.save({'session_id': self.session_id, 'stop': False, 'switch': 0})
        from akeno.network.vpngate import CatalogError
        self._status('searching', country)
        try:
            servers = self.provider.servers(refresh=True)
        except CatalogError:
            return self._status('failed', country, error='free_vpn_catalog_unavailable')
        candidates = self.provider.candidates(servers, country, limit=self.MAX_CANDIDATES)
        if not candidates:
            return self._status('unavailable', country, error='free_vpn_no_server')
        baseline = self.checks.baseline()
        tried, failures, final = set(), {}, None
        try:
            while True:
                server = next((s for s in candidates if s.ip not in tried), None)
                if server is None:
                    more = self.provider.candidates(servers, country, limit=self.MAX_CANDIDATES, exclude=tried)
                    server = more[0] if more else None
                if server is None:
                    code = 'free_vpn_connection_lost' if self.connected_since else 'free_vpn_all_failed'
                    final = self._status('failed', country, error=code, attempts=len(tried), failures=failures)
                    break
                tried.add(server.ip)
                self._status('connecting', country, server, attempt=len(tried))
                try:
                    outcome = self._start(server)
                except ProfileRejected:
                    outcome = 'rejected'
                if outcome != 'connected':
                    failures[outcome] = failures.get(outcome, 0) + 1
                    if not self._stop_process():
                        final = self._status('recovery_required', country, server, error='free_vpn_cleanup_incomplete')
                        return final
                    continue
                self._status('verifying', country, server)
                try:
                    evidence = self.checks.verify(country, baseline)
                except VerificationFailed as failed:
                    failures[failed.code] = failures.get(failed.code, 0) + 1
                    self._stop_process()
                    if failed.code == 'free_vpn_country_mismatch':
                        continue                                    # a mislabelled server: try the next one
                    final = self._status('failed', country, server, error=failed.code)
                    break
                self.connected_since = self.connected_since or self.clock()
                self.journal.save({'phase': 'connected', 'session_id': self.session_id, 'server_ip': server.ip,
                                   'created': self.clock()})
                outcome = self._watch(country, server, evidence)
                self._stop_process()
                if outcome == 'stop':
                    final = self._status('stopped', country)
                    break
                failures[outcome] = failures.get(outcome, 0) + 1   # 'lost' or 'switch': next server, same country
                self._status('switching', country, server)
        except _Stop:
            final = self._status('stopped', country)
        except Exception:
            final = self._status('failed', country, error='free_vpn_session_failed')
        if not self._stop_process():
            return self._status('recovery_required', country, error='free_vpn_cleanup_incomplete')
        self.journal.save({'phase': 'disconnected'})
        return final

    def _watch(self, country, server, evidence) -> str:
        """Keep reporting 'connected' while OpenVPN holds the tunnel; returns 'stop', 'switch' or 'lost'."""
        switch_seen = self._requests().get('switch', 0)
        checked = self.clock()
        native = None
        lost_since = None
        while True:
            self._status('connected', country, server, exit_ipv4=evidence['exit_ipv4'],
                         confirmed_by=evidence['confirmed_by'], dns_verified=evidence['dns_verified'],
                         ipv6=evidence['ipv6'], latency_ms=evidence.get('latency_ms'),
                         connected_since=self.connected_since)
            self.sleep(self.POLL_SECONDS)
            requests = self._requests()
            if requests.get('stop'):
                return 'stop'
            if requests.get('switch', 0) != switch_seen:
                return 'switch'
            state = self.process.state() if self.process else 'exited'
            if state == 'connected':
                lost_since = None
            elif state == 'restarting':
                lost_since = lost_since or self.clock()
                if self.clock() - lost_since > self.RESTART_GRACE:
                    return 'lost'
                self._status('reconnecting', country, server)
                continue
            else:
                return 'lost'
            if self.clock() - checked >= self.RECHECK_SECONDS:
                checked = self.clock()
                try:
                    native = self.checks.inventory()
                except VerificationFailed:
                    return 'lost'
                current = self.checks.exit_address(native['index'], native['source'])
                if current and current != evidence['exit_ipv4']:
                    try:
                        evidence = self.checks.verify(country, None)
                    except VerificationFailed:
                        return 'lost'


def recover(journal, status, *, kill=None) -> dict:
    """After a crash: the helper's job object already ended OpenVPN with it; this clears the journal and stops any
    openvpn.exe still running with AKENO's configuration file (and nothing else)."""
    state = journal.load()
    if state.get('phase') in (None, 'disconnected'):
        status.save({'state': 'stopped', 'updated': time.time()})
        return {'state': 'stopped'}
    if kill is None:
        kill = _kill_akeno_openvpn
    if not kill():
        status.save({'state': 'recovery_required', 'error': 'free_vpn_cleanup_incomplete', 'updated': time.time()})
        return {'state': 'recovery_required'}
    journal.save({'phase': 'disconnected'})
    status.save({'state': 'stopped', 'updated': time.time()})
    return {'state': 'stopped'}


def _kill_akeno_openvpn() -> bool:
    from akeno.network.lan_windows import powershell
    from akeno.network.openvpn_windows import install_dir
    exe = str(install_dir() / 'bin' / 'openvpn.exe').replace("'", "''")
    script = ("$p=@(Get-CimInstance Win32_Process -Filter \"Name='openvpn.exe'\" | Where-Object { "
              f"$_.ExecutablePath -eq '{exe}' -and $_.CommandLine -like '*\\.AkenoFreeVPN-*\\akeno.ovpn*' }}); "
              "foreach($x in $p){ Stop-Process -Id $x.ProcessId -Force }; Start-Sleep -Milliseconds 500; "
              "@(Get-CimInstance Win32_Process -Filter \"Name='openvpn.exe'\" | Where-Object { "
              f"$_.ExecutablePath -eq '{exe}' -and $_.CommandLine -like '*\\.AkenoFreeVPN-*\\akeno.ovpn*' }}).Count")
    try:
        return powershell(script, timeout=30).strip() == '0'
    except Exception:
        return False
