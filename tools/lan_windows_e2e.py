"""Real LAN Play checks on a disposable Windows runner: AKENO's LanSession on the official WireGuard (WireGuardNT).

Runs AKENO's unchanged LanSession, WindowsLanBackend, LanFirewall, PathController and LatencyProbe against the
official signed WireGuard for Windows, the unchanged server coordinator on a private container switch, and a second room member
played by tools/network_e2e/wgpeer.py (real WireGuard protocol, raw IP packets through the tunnel). The control
plane (accounts, signed credentials) is replaced by a fixed member list: its own tests are elsewhere.

Changes Windows Firewall rules and the firewall's Public profile state while it runs; everything AKENO adds is
removed again. Needs an elevated process and WireGuard for Windows with its manager service running.
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
sys.path[:0] = [str(ROOT), str(ROOT / 'server'), str(ROOT / 'tools' / 'network_e2e')]

ROOM = 'c0ffee00' * 4
A_IP, B_IP = '10.203.9.2', '10.203.9.3'


def ps(command: str) -> str:
    p = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', command],
                       capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise RuntimeError(p.stderr.strip()[:400])
    return p.stdout.strip()


class FileControl:
    """The verified member list the real control plane would deliver, read from a JSON file the test writes."""

    def __init__(self, path):
        self.path = Path(path)

    def refresh(self):
        data = json.loads(self.path.read_text(encoding='utf-8'))
        now = time.time()
        for p in data['peers']:
            p['expires'] = now + 45
        return data


def session_parts(work: Path):
    from akeno.licensing.storage import SecureStore
    from akeno.network.lan import PathController
    from akeno.network.lan_windows import LanSession
    from akeno.network.windows import WindowsWireGuard
    # Shorter timers than production so the scenario fits a CI job; the logic is unchanged.
    PathController.SILENT = 20
    LanSession.HEARTBEAT, LanSession.TICK = 3, 1
    native = WindowsWireGuard()
    journal, status = SecureStore(work / 'lan-journal'), SecureStore(work / 'lan-status')
    return native, journal, status, LanSession


def child(work: Path):
    """A session in its own process, for the crash-recovery scenario (the parent kills it)."""
    native, journal, status, LanSession = session_parts(work)
    LanSession(ROOM, FileControl(work / 'control.json'), native, journal, status, lambda: False).run()


def _run(args):
    if sys.platform != 'win32' or not ctypes.windll.shell32.IsUserAnAdmin():
        raise SystemExit('Run elevated on a disposable Windows machine.')
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    os.environ['AKENO_HOME'] = str(work / 'user')
    import wgpeer
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from akeno.network.lan_windows import GROUP, LanError, LanFirewall, recover, tunnel_name
    from akeno.network.windows import service_state
    from akeno_server.network.coordinator import Coordinator, Grant
    native, journal, status, LanSession = session_parts(work)
    checks, evidence = [], {}
    args._checks, args._evidence = checks, evidence

    def check(ok, name, detail=''):
        checks.append({'name': name, 'passed': bool(ok), 'detail': str(detail)[:600]})
        print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f': {detail}' if detail != '' else ''), flush=True)
        if not ok:
            print('::error title=Native LAN assertion::'+name,flush=True)
            raise AssertionError(name)

    def info(name, detail):
        checks.append({'name': name, 'passed': None, 'detail': str(detail)[:600]})
        print(f'[INFO] {name}: {detail}', flush=True)

    def wait(predicate, limit, step=0.5):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(step)
        return predicate()

    name = tunnel_name(ROOM)
    a_priv, a_pub = wgpeer.keypair()
    b_priv, b_pub = wgpeer.keypair()
    grants = {a_pub: Grant(ROOM, time.time() + 3600), b_pub: Grant(ROOM, time.time() + 3600)}
    from windows_peer import ContainerPeer
    fixture = args._fixture = ContainerPeer(args.peer_image)
    fixture.topology()
    coordinator = Coordinator(X25519PrivateKey.generate(), lambda key: grants.get(key), bind=(fixture.gateway, 0)).start()
    args._coordinator = coordinator
    fixture.allow_relay(coordinator.address, ps)
    member = fixture.start(b_priv, a_pub, (B_IP,))
    evidence['outer_topology'] = {'relay': fixture.gateway, 'peer': fixture.address,
                                  'stack': 'isolated Windows container; no published host ports'}
    stop_keepalive = threading.Event()

    def keepalive():
        while not stop_keepalive.wait(10):
            member.keepalive()
    threading.Thread(target=keepalive, daemon=True).start()
    member.prove(coordinator.public_key, coordinator.address)
    check(wait(lambda: coordinator.endpoint(b_pub), 5), 'member B proved its endpoint to the coordinator')

    control_file = work / 'control.json'

    def publish(peers=True):
        control_file.write_text(json.dumps({
            'self': {'public_key': a_pub, 'private_key': a_priv, 'virtual_ip': A_IP},
            'coordinator': {'public_key': coordinator.public_key, 'host': coordinator.address[0], 'port': coordinator.address[1]},
            'peers': [{'public_key': b_pub, 'virtual_ip': B_IP}] if peers
            else []}), encoding='utf-8')
    publish()
    routes_before = ps('Get-NetRoute | Select-Object -ExpandProperty DestinationPrefix | Sort-Object | Out-String')

    def session(stop):
        return LanSession(ROOM, FileControl(control_file), native, journal, status, stop.is_set)

    # -- 1. LAN Play refuses to run without Windows Defender Firewall --------------------------------------------
    original = ps('(Get-NetFirewallProfile -Name Public).Enabled')
    evidence['public_profile_initially'] = original
    ps('Set-NetFirewallProfile -Profile Public -Enabled False')
    try:
        session(threading.Event()).run()
        refused = False
    except LanError as exc:
        refused = 'Firewall' in str(exc)
    check(refused and service_state(name) is None, 'no LAN session while the Public firewall profile is off')
    ps('Set-NetFirewallProfile -Profile Public -Enabled True')

    stop = threading.Event()
    errors = []

    def runner():
        try:
            session(stop).run()
        except Exception as exc:                          # noqa: BLE001 (reported by the checks below)
            errors.append(repr(exc))
            from akeno.network.windows import NativeUnavailable
            if isinstance(exc,(NativeUnavailable,LanError)):
                print('::error title=LAN session native failure::'+str(exc),flush=True)
                # Diagnose rejected RemoteAddress CIDRs independently without weakening
                # the production rules or opening a tunnel. Temporary rules only block.
                if 'relay firewall rule' in str(exc):
                    import ipaddress
                    for net in [*ipaddress.ip_network('0.0.0.0/0').address_exclude(
                            ipaddress.ip_network('127.0.0.1/32')),'::/0']:
                        try:
                            ps(f"New-NetFirewallRule -DisplayName 'AKENO diagnostic CIDR' -Direction Outbound "
                               f"-Action Block -Protocol UDP -LocalPort 49999 -RemoteAddress '{net}' | Remove-NetFirewallRule")
                        except RuntimeError:
                            print(f'::error title=Rejected firewall CIDR::Windows rejected {net}',flush=True)
            else:
                print('::error title=LAN session exception type::'+type(exc).__name__,flush=True)
    worker = threading.Thread(target=runner, daemon=True)
    try:
        worker.start()
        active = wait(lambda: status.load().get('state') == 'active', 120)
        if not active:
            print('::error title=LAN startup stage::'+str(status.load().get('phase','no phase')),flush=True)
        check(active and not errors, 'LAN session active on the official WireGuard tunnel service', errors[:1])
        check(wait(lambda: coordinator.endpoint(a_pub), 20), 'Windows endpoint proven by a real WireGuardNT handshake',
              coordinator.endpoint(a_pub))
        def category():
            try:
                return ps(f"(Get-NetConnectionProfile -InterfaceAlias '{name}' -ErrorAction Stop).NetworkCategory")
            except RuntimeError:
                return None
        found = wait(lambda: category() == 'Public', 30, 1)
        check(found, 'LAN adapter network category is Public', category())
        rules = LanFirewall(name, '10.203.9.0/24').rules()
        check('block TCP' in rules and 'block UDP' in rules and 'latency probe' in rules,
              'AKENO LAN firewall rules on the LAN adapter', rules.replace('\n', ' | '))
        check('relay Inbound' in rules and 'relay Outbound' in rules,
              'native outer firewall rules constrain inbound and outbound packets to the relay')

        def mode():
            return (status.load().get('peers', {}).get(b_pub) or {}).get('mode')
        check(wait(lambda: mode() == 'relay', 40), 'path to member B chosen from real handshakes: relay-only', mode())
        check('endpoint' not in (status.load().get('peers', {}).get(b_pub) or {}),
              'session status contains no peer NAT endpoint')
        echoed = wgpeer.udp_echo(B_IP, count=64, size=1000)
        check(echoed == 64, 'encrypted UDP echo to the room member (64 x 1000 bytes)', f'{echoed}/64')
        latency = wait(lambda: (status.load().get('peers', {}).get(b_pub) or {}).get('latency_ms'), 15)
        check(latency is not None, 'latency to the member measured (AKENO probe)', f'{latency} ms')

        # A known peer possesses valid WireGuard transport keys. Send valid encrypted probe datagrams directly
        # to the tunnel socket from a separate network stack, bypassing the relay. The outer firewall
        # must drop them before WireGuard can authenticate/roam to the peer's source endpoint.
        direct_session = member._member_session()
        check(direct_session is not None, 'test responder has a confirmed encrypted peer session')
        token = b'AKLP' + os.urandom(12)
        before_direct = len(member.inbox_snapshot())
        for _ in range(8):
            check(member.send_direct(member.udp_packet(B_IP, A_IP, 41998, 51999, token),
                                     coordinator.endpoint(a_pub)), 'authenticated attack pinned to the direct destination')
            time.sleep(.03)
        time.sleep(.3)
        direct_replies = [item for item in member.inbox_snapshot()[before_direct:]
                          if item['protocol'] == 17 and item['sport'] == 51999
                          and item['body'] == b'AKLR' + token[4:]]
        check(not direct_replies, 'valid encrypted direct datagrams cannot bypass the outer relay-only filter')
        native_peer = native._call('wg.exe', 'show', name, 'endpoints').stdout
        expected_endpoint = f'{coordinator.address[0]}:{coordinator.address[1]}'
        check(any(line.split() == [b_pub, expected_endpoint] for line in native_peer.splitlines()),
              'native peer endpoint stays on the relay after an authenticated direct-roaming attempt')

        # -- inbound policy as seen by a room member ---------------------------------------------------------------
        def probe(packet, want, wait_s=2.5):
            seen = len(member.inbox_snapshot())
            member.send_ip(packet)
            time.sleep(wait_s)
            return [i for i in member.inbox_snapshot()[seen:] if want(i)]
        listening = ps("(Get-NetTCPConnection -LocalPort 445 -State Listen -ErrorAction SilentlyContinue | "
                       "Measure-Object).Count")
        evidence['smb_listening'] = listening
        smb = probe(member.tcp_syn(B_IP, A_IP, 40445, 445), lambda i: i['protocol'] == 6 and i['sport'] == 445)
        check(not smb, 'SMB (TCP 445) on this PC is not reachable for room members',
              f'listeners on 445: {listening}; replies: {len(smb)}')
        rdp = probe(member.tcp_syn(B_IP, A_IP, 43389, 3389), lambda i: i['protocol'] == 6 and i['sport'] == 3389)
        check(not rdp, 'Remote Desktop (TCP 3389) is not reachable for room members', f'replies: {len(rdp)}')
        nb = probe(member.udp_packet(B_IP, A_IP, 40137, 137, b'\0' * 50), lambda i: i['sport'] == 137)
        check(not nb, 'NetBIOS (UDP 137) is not reachable for room members', f'replies: {len(nb)}')
        ak = probe(member.udp_packet(B_IP, A_IP, 40999, 51999, b'AKLP' + os.urandom(12)),
                   lambda i: i['protocol'] == 17 and i['sport'] == 51999 and i['body'][:4] == b'AKLR')
        check(ak, 'the AKENO latency probe is reachable for room members (explicitly allowed)')

        # A game server: unreachable until the user allows exactly that program, then reachable from the room.
        game = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        game.bind((A_IP, 3979))
        game.settimeout(0.2)
        serving = threading.Event()

        def serve(server):
            while not serving.is_set():
                try:
                    data, addr = server.recvfrom(2048)
                    server.sendto(b'GAME' + data, addr)
                except socket.timeout:
                    continue
                except OSError:
                    return
        threading.Thread(target=serve, args=(game,), daemon=True).start()
        other_port = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        other_port.bind((A_IP, 27015))
        other_port.settimeout(.2)
        threading.Thread(target=serve, args=(other_port,), daemon=True).start()
        before = probe(member.udp_packet(B_IP, A_IP, 41015, 3979, b'hello'), lambda i: i['sport'] == 3979)
        check(not before, 'game port is blocked before the user allows its reviewed profile')
        LanFirewall(name, '10.203.9.0/24').allow_program(sys.executable, 'openttd')
        after = probe(member.udp_packet(B_IP, A_IP, 41015, 3979, b'hello'),
                      lambda i: i['sport'] == 3979 and i['body'] == b'GAMEhello')
        check(after, 'after the user allows the game program, room members can reach its port')
        unrelated = probe(member.udp_packet(B_IP, A_IP, 41017, 27015, b'other'), lambda i: i['sport'] == 27015)
        check(not unrelated, 'the same allowed program remains unreachable on ports outside the game profile')
        spoof = probe(member.udp_packet('10.203.9.77', A_IP, 41016, 3979, b'spoof'), lambda i: i['sport'] == 3979)
        check(not spoof, 'a member cannot send with another address (WireGuard AllowedIPs /32)')
        serving.set()
        game.close()
        other_port.close()

        # -- B refuses any non-relay source; relay-only must continue without exposing a direct endpoint ----------
        member.accept_from = {coordinator.address}
        relayed = coordinator.stats['relayed']
        check(wait(lambda: mode() == 'relay', 90, 1), 'relay-only path remains active when direct packets are refused', mode())
        echoed = wgpeer.udp_echo(B_IP, count=32, size=800)
        check(echoed == 32 and coordinator.stats['relayed'] - relayed >= 64,
              'encrypted UDP echo through the AKENO relay', f'{echoed}/32, {coordinator.stats["relayed"] - relayed} relayed')

        # -- revocation: B is removed from the room -----------------------------------------------------------------
        grants.pop(b_pub)
        coordinator.revoke(b_pub)
        publish(peers=False)
        gone = wait(lambda: b_pub not in status.load().get('peers', {}) and
                    b_pub not in native._call('wg.exe', 'show', name, 'peers', check=False).stdout, 20)
        check(gone, 'removed member disappears from WireGuard within one heartbeat')
        check(wgpeer.udp_echo(B_IP, count=4, size=100, timeout=0.5) == 0, 'no traffic to the removed member')
    finally:
        stop.set()
        worker.join(60)
        stop_keepalive.set()
    check(status.load().get('state') == 'stopped' and service_state(name) is None, 'LAN session stopped, tunnel removed')
    leftover = ps(f"(Get-NetFirewallRule -Group '{GROUP}' -ErrorAction SilentlyContinue | Measure-Object).Count")
    check(leftover == '0', 'all AKENO LAN firewall rules removed', leftover)
    check(journal.load().get('phase') == 'disconnected', 'journal records a clean end')
    after_routes = ps('Get-NetRoute | Select-Object -ExpandProperty DestinationPrefix | Sort-Object | Out-String')
    check(after_routes == routes_before, 'route table identical to before the session')

    # -- crash: the session process is killed; the next start recovers -----------------------------------------------
    grants[b_pub] = Grant(ROOM, time.time() + 3600)
    publish()
    proc = subprocess.Popen([sys.executable, __file__, '--child', str(work)], env=os.environ)
    check(wait(lambda: status.load().get('state') == 'active', 120), 'a second session runs in its own process')
    subprocess.run(['taskkill', '/F', '/PID', str(proc.pid)], capture_output=True)
    proc.wait(30)
    stale = ps(f"(Get-NetFirewallRule -Group '{GROUP}' -ErrorAction SilentlyContinue | Measure-Object).Count")
    check(service_state(name) == 'running' and stale != '0', 'after the crash the tunnel and its rules are still there',
          f'rules: {stale}')
    recover(native, journal)
    remaining = ps(f"(Get-NetFirewallRule -Group '{GROUP}' -ErrorAction SilentlyContinue | Measure-Object).Count")
    check(service_state(name) is None and remaining == '0', 'recovery removes the stale tunnel and firewall rules')
    member.stop()
    coordinator.close()
    if original != 'True':
        ps('Set-NetFirewallProfile -Profile Public -Enabled False')
    args.report.write_text(json.dumps({'platform': 'real Windows', 'checks': checks, 'evidence': evidence}, indent=2),
                           encoding='utf-8')
    print(f"{sum(1 for c in checks if c['passed'])} real Windows LAN Play checks passed", flush=True)


def run(args):
    """Restore all firewall profile states and the owned tunnel even when a native assertion fails."""
    if sys.platform != 'win32' or not ctypes.windll.shell32.IsUserAnAdmin():
        raise SystemExit('Run elevated on a disposable Windows machine.')
    original = json.loads(ps("Get-NetFirewallProfile | Select-Object Name,@{Name='Enabled';"
                             "Expression={$_.Enabled.ToString()}} | ConvertTo-Json -Compress"))
    states = {item['Name']: item['Enabled'] for item in original}
    if set(states) != {'Domain', 'Private', 'Public'} or any(v not in ('True', 'False') for v in states.values()):
        raise RuntimeError('Cannot safely preserve the original firewall profile states.')
    try:
        ps('Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True')
        _run(args)
    except Exception as exc:
        # The production wrapper emits only safe cmdlet/error-code metadata. Keep it visible
        # as an annotation even when GitHub's detailed logs require additional access.
        from akeno.network.windows import NativeUnavailable
        if isinstance(exc,NativeUnavailable):
            print('::error title=Windows network operation::'+str(exc),flush=True)
        raise
    finally:
        try:
            if args.work and args.work.exists():
                from akeno.licensing.storage import SecureStore
                from akeno.network.lan_windows import recover
                from akeno.network.windows import WindowsWireGuard
                recover(WindowsWireGuard(), SecureStore(args.work / 'lan-journal'))
        finally:
            try:
                if hasattr(args, '_coordinator'):
                    args._coordinator.close()
            finally:
                try:
                    if hasattr(args, '_fixture'):
                        args._fixture.close(ps)
                finally:
                    try:
                        for profile, enabled in states.items():
                            ps(f'Set-NetFirewallProfile -Profile {profile} -Enabled {enabled}')
                    finally:
                        if hasattr(args, '_checks'):
                            args.report.write_text(json.dumps({'platform': 'real Windows', 'checks': args._checks,
                                                               'evidence': args._evidence}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--work', type=Path)
    p.add_argument('--report', type=Path, default=Path('lan-windows-report.json'))
    p.add_argument('--child', type=Path)
    p.add_argument('--peer-image', default='akeno-windows-peer:e2e')
    a = p.parse_args()
    if a.child:
        os.environ['AKENO_HOME'] = str(a.child / 'user')
        child(a.child)
    else:
        run(a)
