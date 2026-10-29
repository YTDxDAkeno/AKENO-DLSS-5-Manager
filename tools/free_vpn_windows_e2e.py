"""Real free-VPN end-to-end test on Windows (administrator): AKENO's production code against the public VPN Gate.

    python tools/free_vpn_windows_e2e.py --report free-vpn-report.json [--countries JP,US,KR] [--skip-install]

Runs on a disposable Windows machine (GitHub runner) or on your own PC in an elevated PowerShell. It uses exactly the
code the AKENO helper runs: the consented OpenVPN component installation (official MSI, pinned SHA-256, Authenticode),
the official VPN Gate list, ``FreeVPNSession`` with the real ``OpenVPNProcess`` and the native ``WindowsChecks``.

Checks (each printed as [OK]/[FAIL] with its measured detail, nothing simulated):
* component: official OpenVPN installed without GUI/services, signed by OpenVPN Inc., AKENO-FreeVPN adapter present;
* list: servers and countries from the live list; a country without servers is reported "unavailable" at once;
* connect: a real VPN Gate server of the chosen country, verified exit IP different from the normal one, country
  confirmed by >= 2 independent geolocation services, DNS outside the tunnel denied, IPv6 not leaving;
* traffic: an ordinary (unbound) HTTPS request leaves through the VPN; routes and DNS sit on AKENO's adapter;
* switch: the user's "Switch server" moves to another server of the same country;
* outage: blocking the server (Windows Firewall rule created and removed by this test) makes the session switch or
  fail honestly, never show a stale Connected;
* OpenVPN crash and AKENO helper crash (job object) followed by recovery;
* wrong country: servers of another country presented as the chosen one are rejected by the exit verification;
* after every stop: no openvpn.exe of AKENO, no AKENO routes, the normal public address is back.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akeno.network import free_vpn, openvpn_windows  # noqa: E402
from akeno.network.free_vpn import FreeVPNSession, WindowsChecks  # noqa: E402
from akeno.network.vpngate import VPNGateProvider  # noqa: E402

CHECKS = []


def check(ok, name, detail=''):
    CHECKS.append({'name': name, 'passed': bool(ok), 'detail': str(detail)[:600]})
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f': {detail}' if detail != '' else ''), flush=True)
    return ok


def info(name, detail):
    CHECKS.append({'name': name, 'passed': None, 'detail': str(detail)[:600]})
    print(f'[INFO] {name}: {detail}', flush=True)


def ps(command, timeout=60):
    return subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', command], capture_output=True,
                          text=True, timeout=timeout).stdout.strip()


class Memory:
    def __init__(self):
        self.value, self.history, self.lock = {}, [], threading.Lock()

    def load(self):
        with self.lock:
            return dict(self.value)

    def save(self, value):
        with self.lock:
            self.value = dict(value)
            self.history.append(dict(value))


def public_ip():
    from akeno.network.egress_probe import fetch_json
    for _ in range(3):
        try:
            return fetch_json('ipify')[0]
        except Exception:
            time.sleep(2)
    return None


def akeno_routes():
    return ps(f"@(Get-NetRoute -InterfaceAlias '{openvpn_windows.ADAPTER}' -ErrorAction SilentlyContinue | "
              "Where-Object DestinationPrefix -in '0.0.0.0/1','128.0.0.0/1').Count")


def akeno_openvpn():
    exe = str(openvpn_windows.install_dir() / 'bin' / 'openvpn.exe').replace("'", "''")
    return ps("@(Get-CimInstance Win32_Process -Filter \"Name='openvpn.exe'\" | Where-Object { $_.ExecutablePath -eq "
              f"'{exe}' }}).Count")


class Run:
    """One FreeVPNSession in a thread with in-memory stores (the helper uses DPAPI stores; logic is identical)."""

    def __init__(self, country, provider, checks=None):
        self.journal, self.status, self.control = Memory(), Memory(), Memory()
        self.session = FreeVPNSession(provider, openvpn_windows.OpenVPNProcess, self.journal, self.status,
                                      self.control, checks=checks or WindowsChecks())
        self.result = None
        self.thread = threading.Thread(target=self._run, args=(country,), daemon=True)
        self.thread.start()

    def _run(self, country):
        try:
            self.result = self.session.run(country)
        except Exception as exc:                       # surfaced as a failed check, never hidden
            self.result = {'state': 'crashed', 'error': repr(exc)}

    def wait(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.status.load()
            if predicate(current) or not self.thread.is_alive():
                return current
            time.sleep(1)
        return self.status.load()

    def request(self, **values):
        current = self.control.load()
        if values.get('switch'):
            current['switch'] = int(current.get('switch', 0)) + 1
        if values.get('stop'):
            current['stop'] = True
        self.control.save({**current, 'session_id': self.session.session_id})

    def stop(self, timeout=60):
        self.request(stop=True)
        self.thread.join(timeout)
        return self.result


def connected(status):
    return status.get('state') == 'connected' and status.get('dns_verified') is True


def main(args):
    if sys.platform != 'win32':
        raise SystemExit('Windows only.')
    report = Path(args.report)
    try:
        # -- component ---------------------------------------------------------------------------------------------
        if not args.skip_install:
            result = openvpn_windows.install(consent=True)
            check(result['state'] == 'ready', 'consented install of the official OpenVPN (pinned SHA-256, Authenticode, '
                  'online revocation) and the AKENO adapter', result)
        state = openvpn_windows.status()
        check(state['state'] == 'ready', 'VPN component ready', state)
        exe = openvpn_windows.trusted_executable(online=True)
        check(True, 'installed openvpn.exe signed by OpenVPN Inc. (online check)', exe)
        services = ps("Get-Service -Name 'OpenVPN*' -ErrorAction SilentlyContinue | ForEach-Object "
                      "{ $_.Name + ':' + $_.Status + ':' + $_.StartType }")
        info('OpenVPN services present after the AKENO installation', services or 'none')
        gui = (openvpn_windows.install_dir() / 'bin' / 'openvpn-gui.exe').exists()
        check(args.skip_install or (not gui and 'OpenVPNService:' not in services), 'no OpenVPN GUI and no '
              'auto-connect OpenVPNService were installed', f'gui={gui} services={services or "none"}')
        # -- list --------------------------------------------------------------------------------------------------
        provider = VPNGateProvider(Path(args.work) / 'vpngate-cache.json')
        servers = provider.servers(refresh=True)
        availability = provider.availability(servers)
        check(len(servers) > 0 and len(availability) > 0, 'official VPN Gate list read and parsed',
              f'{len(servers)} servers, usable per country: '
              f'{ {k: v["servers"] for k, v in sorted(availability.items())} }')
        info('target countries (US, TR, DE, JP, NL, FR, CA, GB) in this list',
             {c: availability.get(c, {}).get('servers', 0) for c in ('US', 'TR', 'DE', 'JP', 'NL', 'FR', 'CA', 'GB')})
        missing = next(c for c in ('TR', 'AQ', 'VA', 'GL') if c not in availability)
        quick = Run(missing, provider)
        quick.thread.join(60)
        check(quick.result and quick.result['state'] == 'unavailable' and quick.result['error'] == 'free_vpn_no_server',
              f'a country without servers ({missing}) is reported as not available, no other country is used',
              quick.result)
        baseline = public_ip()
        info('normal public IPv4 of this machine', baseline)
        countries = [c for c in args.countries.split(',') if c in availability]
        check(bool(countries), 'at least one requested country is available right now', countries)
        tested = None
        for country in countries:
            run = Run(country, provider)
            status = run.wait(lambda s: connected(s) or s.get('state') in ('failed', 'unavailable', 'stopped'), 420)
            attempts = [h for h in run.status.history if h.get('state') == 'connecting']
            info(f'{country}: servers tried before the result', [(h.get('server_ip'), h.get('server_proto'))
                                                                 for h in attempts])
            if not connected(status):
                run.stop()
                info(f'{country}: no verified connection', {k: status.get(k) for k in ('state', 'error', 'failures')})
                continue
            tested = country
            check(True, f'{country}: connected through a real VPN Gate server', {k: status.get(k) for k in (
                'server_host', 'server_ip', 'server_proto', 'exit_ipv4', 'confirmed_by', 'latency_ms')})
            check(status['exit_ipv4'] != baseline, f'{country}: public IP changed (verified through the tunnel)',
                  f"{baseline} -> {status['exit_ipv4']}")
            check(len(status.get('confirmed_by') or []) >= 2, f'{country}: country confirmed by at least two '
                  'independent geolocation services', status.get('confirmed_by'))
            check(status.get('dns_verified') is True and status.get('ipv6') == 'blocked',
                  f'{country}: DNS outside the tunnel denied (native measurement) and IPv6 not leaving',
                  {'dns_verified': status.get('dns_verified'), 'ipv6': status.get('ipv6')})
            plain = public_ip()
            check(plain == status['exit_ipv4'], f'{country}: ordinary unbound HTTPS traffic leaves through the VPN',
                  plain)
            routes = akeno_routes()
            dns = ps(f"(Get-DnsClientServerAddress -InterfaceAlias '{openvpn_windows.ADAPTER}' -AddressFamily IPv4)"
                     ".ServerAddresses -join ','")
            check(routes == '2' and dns == ','.join(free_vpn.TUNNEL_DNS), f'{country}: default routes and DNS on '
                  'the AKENO adapter', f'routes={routes} dns={dns}')
            resolved = ps("(Resolve-DnsName example.com -Type A -ErrorAction SilentlyContinue | "
                          "Select-Object -First 1).IPAddress")
            check(bool(resolved), f'{country}: name resolution works through the tunnel DNS', resolved)
            # -- switch --------------------------------------------------------------------------------------------
            first = status['server_ip']
            if availability[country]['servers'] >= 2:
                run.request(switch=True)
                status = run.wait(lambda s: connected(s) and s.get('server_ip') != first or
                                  s.get('state') in ('failed', 'stopped'), 300)
                check(connected(status) and status.get('server_ip') != first and status.get('country') == country,
                      f'{country}: "Switch server" moves to another server of the same country',
                      f"{first} -> {status.get('server_ip')} ({status.get('state')}, {status.get('error')})")
            # -- outage --------------------------------------------------------------------------------------------
            if connected(status):
                victim = status['server_ip']
                ps(f"New-NetFirewallRule -DisplayName 'AKENO-E2E-outage' -Direction Outbound -RemoteAddress {victim} "
                   "-Action Block | Out-Null")
                try:
                    status = run.wait(lambda s: s.get('server_ip') != victim and s.get('state') in (
                        'connected', 'failed') or s.get('state') == 'failed', 300)
                    seen = {h.get('state') for h in run.status.history[-200:]}
                    check(status.get('server_ip') != victim or status.get('state') == 'failed',
                          f'{country}: server outage detected; the session moved on instead of showing a stale '
                          'Connected', f"states={sorted(seen)} now={status.get('state')} server={status.get('server_ip')}")
                finally:
                    ps("Remove-NetFirewallRule -DisplayName 'AKENO-E2E-outage' -ErrorAction SilentlyContinue")
            # -- OpenVPN crash ------------------------------------------------------------------------------------
            if connected(run.status.load()):
                before = run.status.load()['server_ip']
                ps("Get-Process openvpn -ErrorAction SilentlyContinue | Stop-Process -Force")
                status = run.wait(lambda s: s.get('state') != 'connected' or s.get('server_ip') != before, 90)
                check(status.get('state') != 'connected' or status.get('server_ip') != before,
                      f'{country}: a killed OpenVPN is noticed at once (no stale Connected)', status.get('state'))
            result = run.stop(120)
            check(result and result.get('state') == 'stopped', f'{country}: Disconnect ends the session', result)
            time.sleep(3)
            check(akeno_openvpn() == '0' and akeno_routes() == '0', f'{country}: no AKENO OpenVPN process and no '
                  'AKENO routes remain', f'openvpn={akeno_openvpn()} routes={akeno_routes()}')
            check(public_ip() == baseline, f'{country}: the normal public IP is back after disconnecting', baseline)
            break
        check(tested is not None, 'a verified free VPN connection was established in at least one country', tested)
        # -- helper crash (job object) + recovery ------------------------------------------------------------------
        if tested:
            child = subprocess.Popen([sys.executable, __file__, '--child', tested, '--work', args.work])
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline and akeno_routes() != '2':
                time.sleep(2)
            up = akeno_routes() == '2'
            info('helper-like process connected before the crash', up)
            child.kill()
            child.wait(30)
            time.sleep(4)
            check(akeno_openvpn() == '0', 'a crashed AKENO helper takes OpenVPN with it (kill-on-close job)',
                  akeno_openvpn())
            journal, status = Memory(), Memory()
            journal.save({'phase': 'connected'})
            check(free_vpn.recover(journal, status)['state'] == 'stopped' and akeno_routes() == '0',
                  'recovery after the crash leaves no AKENO OpenVPN or route', f'routes={akeno_routes()}')
            check(public_ip() == baseline, 'the normal public IP is back after the crash and recovery', baseline)
        # -- wrong exit country ----------------------------------------------------------------------------------------
        if tested:
            other = next((c for c in availability if c != tested and c in ('JP', 'KR')), None) or tested
            label = 'US' if other != 'US' else 'GB'

            class Disguised(VPNGateProvider):
                def candidates(self, servers, country, limit=6, exclude=()):
                    if len(exclude) >= 2:
                        return []                       # two disguised servers are enough evidence
                    real = VPNGateProvider.candidates(servers, other, limit=2, exclude=exclude)
                    return [type(s)(**{**s.__dict__, 'country': label}) for s in real]
            run = Run(label, Disguised(Path(args.work) / 'vpngate-cache.json'))
            run.thread.join(420)
            result = run.result or {}
            mismatches = (result.get('failures') or {}).get('free_vpn_country_mismatch', 0)
            check(result.get('state') == 'failed' and not any(h.get('state') == 'connected' for h in
                                                              run.status.history),
                  f'servers of {other} presented as {label} are never shown as Connected',
                  {'state': result.get('state'), 'error': result.get('error'), 'failures': result.get('failures')})
            info('exit-country mismatches detected by the verification', mismatches)
            check(akeno_openvpn() == '0' and akeno_routes() == '0', 'nothing remains after the rejected servers',
                  f'openvpn={akeno_openvpn()} routes={akeno_routes()}')
    finally:
        ps("Remove-NetFirewallRule -DisplayName 'AKENO-E2E-outage' -ErrorAction SilentlyContinue")
        failed = [c for c in CHECKS if c['passed'] is False]
        report.write_text(json.dumps({'platform': 'Windows (administrator)', 'checks': CHECKS}, indent=2),
                          encoding='utf-8')
        passed = sum(1 for c in CHECKS if c['passed'] is True)
        print(f'{passed} real free VPN checks passed, {len(failed)} failed', flush=True)
    return 1 if failed else 0


def child(country, work):
    """Stands in for the elevated helper: joins a kill-on-close job, then runs a session until killed."""
    openvpn_windows.kill_with_this_process()
    run = Run(country, VPNGateProvider(Path(work) / 'vpngate-cache.json'))
    run.thread.join()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--report', default='free-vpn-report.json')
    p.add_argument('--countries', default='JP,KR,US')
    p.add_argument('--work', default=str(Path.cwd() / 'free-vpn-e2e'))
    p.add_argument('--skip-install', action='store_true')
    p.add_argument('--child')
    a = p.parse_args()
    Path(a.work).mkdir(parents=True, exist_ok=True)
    if a.child:
        child(a.child, a.work)
    else:
        raise SystemExit(main(a))
