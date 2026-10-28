"""Real WireGuard for Windows checks for AKENO's own VPN backend, on a disposable Windows runner only.

Runs AKENO's unchanged code (DPAPI ProfileStore, DPAPI tunnel journal, TunnelManager, WindowsWireGuard, the elevated
helper's request handling) against the official signed WireGuard manager and the WireGuardNT driver. The peer is
tools/network_e2e/wgpeer.py on 127.0.0.1, so real encrypted packets cross the real Windows driver and IP stack
without an Internet exit, third-party server or any change that outlives the test: every tunnel is removed again
and the route and DNS tables are compared with the state before.

Needs: Windows, an elevated process, WireGuard for Windows installed with its manager service running.
The optional full-tunnel scenario (--full-tunnel) cuts this machine's Internet access for a few seconds.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools' / 'network_e2e'))


def ps(command: str):
    p = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', command],
                       capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise RuntimeError(f'PowerShell failed: {p.stderr.strip()[:400]}')
    out = p.stdout.strip()
    data = json.loads(out) if out else []
    return data if isinstance(data, list) else [data]


def routes() -> set[tuple]:
    rows = ps('Get-NetRoute -PolicyStore ActiveStore | Select-Object DestinationPrefix,NextHop,InterfaceAlias '
              '| ConvertTo-Json -Compress')
    return {(r['DestinationPrefix'], r['NextHop'], r['InterfaceAlias']) for r in rows}


def dns() -> set[tuple]:
    rows = ps('Get-DnsClientServerAddress | Select-Object InterfaceAlias,AddressFamily,ServerAddresses '
              '| ConvertTo-Json -Compress')
    return {(r['InterfaceAlias'], r['AddressFamily'], tuple(r['ServerAddresses'] or ())) for r in rows}


def service_exists(name: str) -> bool:
    return subprocess.run(['sc.exe', 'query', 'WireGuardTunnel$' + name], capture_output=True).returncode != 1060


def adapter_exists(name: str) -> bool:
    return bool(ps(f"Get-NetAdapter -IncludeHidden | Where-Object Name -eq '{name}' | Select-Object Name "
                   "| ConvertTo-Json -Compress"))


def profile(private: str, peer: str, port: int, addresses: str, allowed: str, dns_servers: str | None = None) -> str:
    text = f'[Interface]\nPrivateKey = {private}\nAddress = {addresses}\nMTU = 1280\n'
    if dns_servers:
        text += f'DNS = {dns_servers}\n'
    return text + f'\n[Peer]\nPublicKey = {peer}\nAllowedIPs = {allowed}\nEndpoint = 127.0.0.1:{port}\nPersistentKeepalive = 5\n'


def https_ok(url='https://api.github.com/zen', timeout=15) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def run(args):
    if sys.platform != 'win32':
        raise SystemExit('This test requires a disposable real Windows machine.')
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise SystemExit('Run elevated: the tunnel service operations need administrator rights.')
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    os.environ['AKENO_HOME'] = str(work / 'user')
    from akeno.licensing.storage import SecureStore
    from akeno.network import helper
    from akeno.network.profiles import ProfileStore
    from akeno.network.tunnels import TunnelManager
    from akeno.network.windows import NativeUnavailable, WindowsWireGuard
    from akeno.paths import sub
    import wgpeer

    checks: list[dict] = []
    evidence: dict = {}

    def check(ok, name, detail=''):
        checks.append({'name': name, 'passed': bool(ok), 'detail': str(detail)[:600]})
        print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f': {detail}' if detail else ''), flush=True)
        if not ok:
            raise AssertionError(name)

    def info(name, detail):
        checks.append({'name': name, 'passed': None, 'detail': str(detail)[:600]})
        print(f'[INFO] {name}: {detail}', flush=True)

    backend = WindowsWireGuard()
    for exe in ('wireguard.exe', 'wg.exe'):
        backend._trusted(exe)
    check(True, 'official WireGuard binaries Authenticode-signed by WireGuard LLC')
    check(backend.configs.is_dir(), 'official WireGuard manager configuration store exists')

    # The same per-user locations the elevated AKENO helper uses.
    store = ProfileStore(sub('network') / 'profiles.sqlite3')
    journal = SecureStore(sub('network') / 'tunnel')
    manager = TunnelManager(store, journal, backend)
    routes_before, dns_before = routes(), dns()
    evidence['default_routes_before'] = sorted(r for r in routes_before if r[0] in ('0.0.0.0/0', '::/0'))
    check(https_ok(), 'Internet reachable before any tunnel')

    def wait_connected(limit=30):
        deadline = time.monotonic() + limit
        state = manager.status()
        while state['state'] != 'connected' and time.monotonic() < deadline:
            time.sleep(0.5)
            state = manager.status()
        return state

    active: list[str] = []

    def cleanup():
        try:
            if journal.load().get('phase') not in (None, 'disconnected'):
                manager.disconnect()
        except Exception as exc:
            print(f'cleanup through AKENO failed: {type(exc).__name__}', flush=True)
        from akeno.network.windows import service_state
        for name in active:
            # A still-starting service is only marked for deletion by the official uninstall: wait it out first.
            deadline = time.monotonic() + 60
            while service_state(name) not in (None, 'running', 'stopped') and time.monotonic() < deadline:
                time.sleep(0.5)
            if service_state(name) is not None:
                subprocess.run([str(backend.root / 'wireguard.exe'), '/uninstalltunnelservice', name], timeout=60)
            while service_state(name) is not None and time.monotonic() < deadline:
                time.sleep(0.5)
            print(f'emergency cleanup of {name}: service {service_state(name) or "removed"}', flush=True)
            (backend.configs / (name + '.conf.dpapi')).unlink(missing_ok=True)

    try:
        # ---- split tunnel: only the peer's virtual address goes through WireGuard ---------------------------
        client_private, client_public = wgpeer.keypair()
        peer_private, _ = wgpeer.keypair()
        with wgpeer.Responder(peer_private, client_public, ('10.203.77.1',)) as peer:
            text = profile(client_private, peer.public_key, peer.port, '10.203.77.2/32', '10.203.77.1/32')
            ident = store.add('CI split tunnel', text)
            raw = store.path.read_bytes()
            check(client_private.encode() not in raw and b'PrivateKey' not in raw,
                  'imported profile stored only DPAPI-encrypted')
            name = manager.name(ident)
            active.append(name)
            state = manager.connect(ident)
            check(state['state'] in ('awaiting_handshake', 'connected'), 'AKENO connect installs the official tunnel service',
                  state['state'])
            check(service_exists(name) and adapter_exists(name), 'WireGuardNT adapter and tunnel service exist')
            files = sorted(p.name for p in backend.configs.iterdir() if p.name.startswith(name))
            check(files == [name + '.conf.dpapi'], 'only the manager-encrypted configuration remains on disk', files)
            try:
                at_rest = (backend.configs / files[0]).read_bytes()
                check(client_private.encode() not in at_rest, 'manager configuration holds no plaintext private key')
            except PermissionError:
                # The official manager stores it SYSTEM-only: not even this elevated user process can read it.
                check(True, 'manager configuration is unreadable even for the elevated user (SYSTEM-only)')
            state = wait_connected()
            check(state['state'] == 'connected' and peer.handshakes >= 1, 'real WireGuard handshake through WireGuardNT',
                  f"{state['state']}, responder handshakes={peer.handshakes}")
            echoed = wgpeer.udp_echo('10.203.77.1', count=64, size=1200)
            check(echoed == 64, 'encrypted UDP echo through the Windows tunnel (64 x 1200 bytes)', f'{echoed}/64')
            ping = subprocess.run(['ping.exe', '-n', '3', '-w', '2000', '10.203.77.1'], capture_output=True, text=True)
            info('ICMP echo through the tunnel (ping.exe)', 'replies' if ping.returncode == 0 else 'no replies')
            transfer = backend._call('wg.exe', 'show', name, 'transfer').stdout.split()
            check(len(transfer) == 3 and int(transfer[1]) > 64 * 1200 and int(transfer[2]) > 64 * 1200,
                  'WireGuard transfer counters show the encrypted traffic', ' '.join(transfer[1:]))
            during = routes()
            ours = {r for r in during if r[2] == name}
            check(any(r[0] == '10.203.77.1/32' for r in ours), 'route to the peer address on the tunnel adapter only',
                  sorted(ours))
            check({r for r in during if r[2] != name} == {r for r in routes_before if r[2] != name},
                  'split tunnel leaves every other route unchanged')
            check({d for d in dns() if d[0] != name} == {d for d in dns_before if d[0] != name},
                  'split tunnel leaves the DNS configuration of other adapters unchanged')
            check(https_ok(), 'normal Internet traffic keeps working with the split tunnel')

            # A forged public key (e.g. damaged or manipulated local state) must not remove the tunnel.
            _, foreign = wgpeer.keypair()
            try:
                backend.stop(name, public_key=foreign)
                refused = False
            except NativeUnavailable:
                refused = True
            check(refused and service_exists(name), 'removal with a foreign key is refused, tunnel untouched')

            # The app restarts: a new manager reads the DPAPI journal and still controls the tunnel.
            restarted = TunnelManager(ProfileStore(store.path), SecureStore(journal.dir), WindowsWireGuard())
            state = restarted.status()
            check(state['state'] == 'connected' and state['profile_id'] == ident,
                  'journal recovery after an application restart', state['state'])

            # The elevated helper's own request path (status; connect stays license-gated).
            request = helper.request_store('a' * 32)
            request.save({'action': 'status', 'profile_id': None, 'created': time.time()})
            code = helper.execute('a' * 32)
            result = request.load()
            check(code == 0 and result.get('result', {}).get('state') == 'connected',
                  'elevated helper status request on real Windows', result.get('result', {}).get('state'))
            request.save({'action': 'connect', 'profile_id': ident, 'created': time.time()})
            code = helper.execute('a' * 32)
            check(code == 1 and 'license' in request.load().get('error', ''), 'helper connect without a license is refused')
            request.save({'action': 'status', 'profile_id': None, 'created': time.time() - 600})
            check(helper.execute('a' * 32) == 1, 'expired helper request is refused')
            request.clear()

            # Peer outage and recovery: nothing is delivered in plaintext, WireGuard re-handshakes by itself.
            peer.drop = True
            lost = wgpeer.udp_echo('10.203.77.1', count=8, size=200, timeout=1)
            check(lost == 0, 'peer outage: no traffic delivered while the peer is unreachable', f'{lost}/8')
            peer.drop = False
            deadline, back = time.monotonic() + 40, 0
            while time.monotonic() < deadline and back < 8:
                back = wgpeer.udp_echo('10.203.77.1', count=8, size=200, timeout=1)
            check(back == 8, 'traffic resumes automatically after the peer outage', f'{back}/8')

            state = manager.disconnect()
            check(state['state'] == 'disconnected', 'AKENO disconnect')
        check(not service_exists(name) and not adapter_exists(name), 'tunnel service and adapter removed')
        check(not any(p.name.startswith(name) for p in backend.configs.iterdir()), 'no configuration file remains')
        check(routes() == routes_before, 'route table identical to the state before the tunnel')
        check(dns() == dns_before, 'DNS configuration identical to the state before the tunnel')
        active.remove(name)
        store.remove(ident)
        check(store.metadata() == [], 'profile removed from the encrypted store')

        if args.full_tunnel:
            full_tunnel(store, manager, backend, journal, wgpeer, check, info, evidence, active, routes_before,
                        dns_before, wait_connected)
    finally:
        cleanup()
        args.report.write_text(json.dumps({'platform': 'real Windows', 'checks': checks, 'evidence': evidence},
                                          indent=2), encoding='utf-8')
    print(f"{sum(1 for c in checks if c['passed'])} real Windows WireGuard checks passed", flush=True)


def full_tunnel(store, manager, backend, journal, wgpeer, check, info, evidence, active, routes_before, dns_before,
                wait_connected):
    """0.0.0.0/0 and ::/0 through the tunnel, tunnel DNS. Internet is cut for this short window on purpose."""
    client_private, client_public = wgpeer.keypair()
    peer_private, _ = wgpeer.keypair()
    with wgpeer.Responder(peer_private, client_public, ('10.203.78.1', 'fd00:a4e0:78::1')) as peer:
        peer.answer_dns = True
        text = profile(client_private, peer.public_key, peer.port, '10.203.78.2/32, fd00:a4e0:78::2/128',
                       '0.0.0.0/0, ::/0', '10.203.78.1')
        ident = store.add('CI full tunnel', text)
        name = manager.name(ident)
        active.append(name)
        started = time.monotonic()
        try:
            manager.connect(ident)
            state = wait_connected(20)
            check(state['state'] == 'connected', 'full tunnel handshake', state['state'])
            during = routes()
            defaults = sorted(r for r in during if r[0] in ('0.0.0.0/0', '::/0'))
            evidence['default_routes_full_tunnel'] = defaults
            check({'0.0.0.0/0', '::/0'} <= {r[0] for r in during if r[2] == name},
                  'IPv4 and IPv6 default routes on the tunnel adapter', defaults)
            tunnel_dns = {d for d in dns() if d[0] == name}
            check(any('10.203.78.1' in d[2] for d in tunnel_dns), 'tunnel DNS server configured', sorted(tunnel_dns))
            # Production observer, real WireGuardNT and real Windows socket access-denial
            # results. Only the DNS responder is an isolated synthetic test endpoint.
            from akeno.network.dns_probe import verify as verify_dns
            dns_evidence=verify_dns(name,['10.203.78.1'])
            evidence['native_dns_probe']=dns_evidence
            check(dns_evidence['dns_verified'] is True and
                  dns_evidence['denied_paths']+dns_evidence['unroutable_paths']>=3,
                  'production DNS probe measures tunnel DNS replies and native blocked foreign paths',dns_evidence)
            # Leak probes: Internet destinations must arrive inside the tunnel (at the loopback peer). Port 9 is
            # used because WireGuard for Windows' firewall blocks DNS (port 53) to any non-tunnel resolver.
            query = b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07example\x03com\x00\x00\x01\x00\x01'
            probes = [('1.1.1.1', socket.AF_INET, 9), ('2606:4700:4700::1111', socket.AF_INET6, 9),
                      ('1.1.1.1', socket.AF_INET, 53)]
            sent6 = True
            for address, family, port in probes:
                try:
                    with socket.socket(family, socket.SOCK_DGRAM) as s:
                        s.sendto(query, (address, port))
                except OSError as exc:
                    if family == socket.AF_INET6:
                        sent6 = False
                        info('IPv6 probe could not be sent', type(exc).__name__)
                    elif port == 53:
                        info('DNS to a foreign resolver refused by the system', type(exc).__name__)
            resolver = threading.Thread(target=lambda: _resolve('akeno-leak-test.example'), daemon=True)
            resolver.start()
            resolver.join(6)
            time.sleep(1.5)
            seen = peer.observed_snapshot()
            evidence['tunnel_observed'] = seen[:50]
            check(any(o['destination'] == '1.1.1.1' and o['port'] == 9 for o in seen),
                  'IPv4 Internet packet went through the tunnel, not the physical adapter')
            if sent6:
                check(any(o['destination'] == '2606:4700:4700::1111' and o['port'] == 9 for o in seen),
                      'IPv6 Internet packet went through the tunnel, not the physical adapter')
            check(not any(o['destination'] == '1.1.1.1' and o['port'] == 53 for o in seen),
                  'DNS to a foreign resolver is not delivered through the tunnel (WireGuard firewall DNS restriction)')
            check(any(o['destination'] == '10.203.78.1' and o['port'] == 53 for o in seen),
                  'system DNS lookups were sent to the tunnel DNS server')
            check(not _tcp_reachable('140.82.112.3', 443), 'no direct Internet connection while the full tunnel is up')
        finally:
            state = manager.disconnect()
            info('full tunnel window (Internet cut)', f'{time.monotonic() - started:.1f} s')
        check(state['state'] == 'disconnected' and not service_exists(name), 'full tunnel removed')
        active.remove(name)
    store.remove(ident)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and (routes() != routes_before or dns() != dns_before):
        time.sleep(1)
    after = routes()
    check(after == routes_before, 'routes restored after the full tunnel',
          {'missing': sorted(routes_before - after)[:10], 'extra': sorted(after - routes_before)[:10]})
    check(dns() == dns_before, 'DNS restored after the full tunnel')
    check(https_ok(timeout=30), 'Internet works again after disconnect')


def _resolve(host):
    try:
        socket.getaddrinfo(host, 443)
    except OSError:
        pass


def _tcp_reachable(address, port):
    try:
        with socket.create_connection((address, port), timeout=3):
            return True
    except OSError:
        return False


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--report', type=Path, default=Path('network-windows-report.json'))
    p.add_argument('--full-tunnel', action='store_true')
    try:
        run(p.parse_args())
    except Exception as exc:
        from akeno.network.dns_probe import DNSVerificationError
        from akeno.network.windows import NativeUnavailable
        detail=str(exc) if isinstance(exc,(DNSVerificationError,NativeUnavailable,AssertionError)) else \
               f'{type(exc).__name__}; winerror={getattr(exc,"winerror",None)}'
        print('::error title=Native VPN test failure::'+detail,flush=True)
        raise
