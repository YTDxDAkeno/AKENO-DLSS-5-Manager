"""Real NAT traversal lab for AKENO LAN Play on one Linux machine (root, iproute2, iptables, conntrack, WireGuard).

Five network namespaces, each with its own IP stack: an "Internet" with the AKENO coordinator (unchanged server
code), two NAT routers (Linux netfilter MASQUERADE = endpoint-independent mapping, or --random-fully = endpoint-
dependent "symmetric" mapping) and two member PCs running real WireGuard (kernel module if present, else
wireguard-go) whose peers are driven by AKENO's own PathController. The host's own namespace, routes and firewall
are not touched; everything is deleted afterwards. The "public" addresses are inside 11.203.0.0/24, which exists
only inside the lab namespaces.

The control plane (accounts, signed credentials) is not part of this lab: its tests are in server/tests and
tests/test_network_lan.py. Here the coordinator's real endpoint proofs feed the controller directly.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'server'))

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: E402

from akeno.network.lan import PathController  # noqa: E402
from akeno_server.network.coordinator import Coordinator, Grant  # noqa: E402

COORD = ('11.203.0.1', 3478)
SUBNET = '10.203.5.0/24'
ROOM = 'lab-room'


def sh(*args, check=True, ns=None, input=None):
    cmd = (['ip', 'netns', 'exec', ns] if ns else []) + [str(a) for a in args]
    p = subprocess.run(cmd, capture_output=True, text=True, input=input, timeout=30)
    if check and p.returncode:
        raise RuntimeError(f'{" ".join(cmd)}: {p.stderr.strip()[:300]}')
    return p.stdout


@contextlib.contextmanager
def inside(ns):
    """Run the block in namespace ``ns`` (this thread only); sockets created here stay in that namespace."""
    own = os.open('/proc/self/ns/net', os.O_RDONLY)
    target = os.open(f'/var/run/netns/{ns}', os.O_RDONLY)
    try:
        os.setns(target, os.CLONE_NEWNET)
        yield
    finally:
        os.setns(own, os.CLONE_NEWNET)
        os.close(own)
        os.close(target)


def kernel_wireguard() -> bool:
    probe = 'akwgprobe' + uuid.uuid4().hex[:4]
    ok = subprocess.run(['ip', 'link', 'add', probe, 'type', 'wireguard'], capture_output=True).returncode == 0
    if ok:
        subprocess.run(['ip', 'link', 'del', probe], capture_output=True)
    return ok


class NsWireGuard:
    """PathController backend for one lab PC: the real ``wg`` tool against the real interface."""

    def __init__(self, ns, interface):
        self.ns, self.interface = ns, interface

    def set_peer(self, key, endpoint, virtual_ip):
        sh('wg', 'set', self.interface, 'peer', key, 'endpoint', f'{endpoint[0]}:{endpoint[1]}', 'allowed-ips',
           f'{virtual_ip}/32', 'persistent-keepalive', 25, ns=self.ns)

    def remove_peer(self, key):
        sh('wg', 'set', self.interface, 'peer', key, 'remove', ns=self.ns)

    def peers(self):
        out = {}
        for line in sh('wg', 'show', self.interface, 'dump', ns=self.ns).splitlines()[1:]:
            f = line.split('\t')
            endpoint = None
            if f[2] != '(none)':
                host, port = f[2].rsplit(':', 1)
                endpoint = (host.strip('[]'), int(port))
            out[f[0]] = {'endpoint': endpoint, 'latest_handshake': int(f[4]) or None, 'rx': int(f[5]), 'tx': int(f[6])}
        return out


class Lab:
    def __init__(self, symmetric_b=False):
        tag = uuid.uuid4().hex[:5]
        self.ns = {k: f'akl{tag}-{k}' for k in ('inet', 'nata', 'natb', 'pca', 'pcb')}
        # wireguard-go's control socket lives in /var/run/wireguard, which all namespaces share: unique names.
        self.wg = {side: f'akw{tag}{side}' for side in 'ab'}
        self.symmetric_b = symmetric_b
        self.procs, self.threads, self.stop = [], [], threading.Event()
        self.kernel = kernel_wireguard()
        self.grants = {}
        self.keys = {}

    # -- topology -----------------------------------------------------------------------------------------------
    def up(self):
        n = self.ns
        for name in n.values():
            sh('ip', 'netns', 'add', name)
            sh('ip', 'link', 'set', 'lo', 'up', ns=name)
        sh('ip', 'link', 'add', 'br0', 'type', 'bridge', ns=n['inet'])
        sh('ip', 'address', 'add', f'{COORD[0]}/24', 'dev', 'br0', ns=n['inet'])
        sh('ip', 'link', 'set', 'br0', 'up', ns=n['inet'])
        for side, wan, lan_router, lan_pc in (('a', '11.203.0.10', '10.10.1.1', '10.10.1.2'),
                                              ('b', '11.203.0.20', '10.10.2.1', '10.10.2.2')):
            router, pc = n['nat' + side], n['pc' + side]
            sh('ip', 'link', 'add', f'w{side}i', 'netns', n['inet'], 'type', 'veth', 'peer', 'name', f'w{side}n',
               'netns', router)
            sh('ip', 'link', 'set', f'w{side}i', 'master', 'br0', 'up', ns=n['inet'])
            sh('ip', 'address', 'add', f'{wan}/24', 'dev', f'w{side}n', ns=router)
            sh('ip', 'link', 'set', f'w{side}n', 'up', ns=router)
            sh('ip', 'link', 'add', f'l{side}n', 'netns', router, 'type', 'veth', 'peer', 'name', f'l{side}p',
               'netns', pc)
            sh('ip', 'address', 'add', f'{lan_router}/24', 'dev', f'l{side}n', ns=router)
            sh('ip', 'link', 'set', f'l{side}n', 'up', ns=router)
            sh('ip', 'address', 'add', f'{lan_pc}/24', 'dev', f'l{side}p', ns=pc)
            sh('ip', 'link', 'set', f'l{side}p', 'up', ns=pc)
            sh('ip', 'route', 'add', 'default', 'via', lan_router, ns=pc)
            sh('sysctl', '-q', 'net.ipv4.ip_forward=1', ns=router)
            self.masquerade(side)
        self.coordinator_up()
        for side, ip in (('a', '10.203.5.2'), ('b', '10.203.5.3')):
            self.member_up(side, ip)

    def masquerade(self, side, ports=None):
        router = self.ns['nat' + side]
        sh('iptables', '-t', 'nat', '-F', 'POSTROUTING', ns=router)
        extra = ['--random-fully'] if side == 'b' and self.symmetric_b else []
        if ports:
            sh('iptables', '-t', 'nat', '-A', 'POSTROUTING', '-o', f'w{side}n', '-p', 'udp', '-j', 'MASQUERADE',
               '--to-ports', ports, *extra, ns=router)
        sh('iptables', '-t', 'nat', '-A', 'POSTROUTING', '-o', f'w{side}n', '-j', 'MASQUERADE', *extra, ns=router)
        # Router policy: replies only. Nothing unsolicited from the Internet reaches the PC.
        sh('iptables', '-P', 'FORWARD', 'DROP', ns=router)
        sh('iptables', '-F', 'FORWARD', ns=router)
        sh('iptables', '-A', 'FORWARD', '-i', f'l{side}n', '-j', 'ACCEPT', ns=router)
        sh('iptables', '-A', 'FORWARD', '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT', ns=router)
        # Like a home router, drop unsolicited traffic to the router itself. (Accepting it would confirm a conntrack
        # entry that collides with the hole-punching mapping and makes netfilter pick another source port.)
        sh('iptables', '-F', 'INPUT', ns=router)
        sh('iptables', '-A', 'INPUT', '-i', f'w{side}n', '-m', 'conntrack', '--ctstate', 'NEW,INVALID', '-j', 'DROP',
           ns=router)

    def coordinator_up(self):
        self.coordinator_key = X25519PrivateKey.generate()
        with inside(self.ns['inet']):
            self.coordinator = Coordinator(self.coordinator_key, lambda key: self.grants.get(key), bind=COORD)
        self.coordinator.start()

    def member_up(self, side, virtual_ip):
        pc, wg = self.ns['pc' + side], self.wg[side]
        if self.kernel:
            sh('ip', 'link', 'add', wg, 'type', 'wireguard', ns=pc)
        else:
            env = dict(os.environ, WG_PROCESS_FOREGROUND='1')
            env.pop('WG_TUN_FD', None)
            self.procs.append(subprocess.Popen(['ip', 'netns', 'exec', pc, 'wireguard-go', '-f', wg], env=env,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and subprocess.run(['ip', 'netns', 'exec', pc, 'wg', 'show', wg],
                                                                 capture_output=True).returncode:
                time.sleep(0.1)
        private = X25519PrivateKey.generate()
        from cryptography.hazmat.primitives import serialization
        import base64
        raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                    serialization.NoEncryption())
        public = base64.b64encode(private.public_key().public_bytes(serialization.Encoding.Raw,
                                                                    serialization.PublicFormat.Raw)).decode()
        with tempfile.NamedTemporaryFile('w', delete=False) as f:
            f.write(base64.b64encode(raw).decode())
        try:
            coord_public = base64.b64encode(self.coordinator.responder.public).decode()
            sh('wg', 'set', wg, 'private-key', f.name, 'listen-port', 51820, 'peer', coord_public, 'endpoint',
               f'{COORD[0]}:{COORD[1]}', 'allowed-ips', '10.203.0.1/32', 'persistent-keepalive', 25, ns=pc)
        finally:
            os.unlink(f.name)
        sh('ip', 'address', 'add', f'{virtual_ip}/24', 'dev', wg, ns=pc)
        sh('ip', 'link', 'set', wg, 'mtu', 1380, 'up', ns=pc)
        self.keys[side] = {'public': public, 'ip': virtual_ip}
        self.grants[public] = Grant(ROOM, time.time() + 3600)

    def down(self):
        self.stop.set()
        with contextlib.suppress(Exception):
            self.coordinator.close()
        for p in self.procs:
            p.terminate()
            with contextlib.suppress(Exception):
                p.wait(5)
        for name in self.ns.values():
            subprocess.run(['ip', 'netns', 'del', name], capture_output=True)

    # -- helpers ----------------------------------------------------------------------------------------------------
    def echo_server(self, side, port=9000):
        with inside(self.ns['pc' + side]):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind((self.keys[side]['ip'], port))
        s.settimeout(0.2)

        def serve():
            while not self.stop.is_set():
                try:
                    data, addr = s.recvfrom(65535)
                    s.sendto(data, addr)
                except socket.timeout:
                    continue
                except OSError:
                    return
        t = threading.Thread(target=serve, daemon=True)
        t.start()
        self.threads.append(t)

    def echo(self, side, target, count=100, size=900, timeout=1.0, port=9000):
        with inside(self.ns['pc' + side]):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        with s:
            s.settimeout(timeout)
            ok = 0
            for _ in range(count):
                payload = os.urandom(size)
                try:
                    s.sendto(payload, (self.keys[target]['ip'], port))
                    while s.recvfrom(65535)[0] != payload:
                        pass
                    ok += 1
                except (socket.timeout, OSError):
                    pass
            return ok

    def proven(self, side):
        return self.coordinator.endpoint(self.keys[side]['public'])

    def peers_for(self, side, expires=None):
        """What a verified heartbeat would give this member: every other live member and its proven endpoint."""
        out = []
        for other, k in self.keys.items():
            if other == side or k['public'] not in self.grants:
                continue
            out.append({'public_key': k['public'], 'virtual_ip': k['ip'], 'endpoint': self.proven(other),
                        'expires': expires or time.time() + 45})
        return out


def wait(predicate, limit, step=0.5):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def run_lab(symmetric_b, record):
    lab = Lab(symmetric_b)
    label = 'cone/symmetric' if symmetric_b else 'cone/cone'
    try:
        lab.up()
        record(True, f'[{label}] lab up ({"kernel WireGuard" if lab.kernel else "wireguard-go"})')
        a_end = wait(lambda: lab.proven('a'), 15)
        b_end = wait(lambda: lab.proven('b'), 15)
        record(bool(a_end and a_end[0] == '11.203.0.10' and b_end and b_end[0] == '11.203.0.20'),
               f'[{label}] coordinator proved both NAT-mapped WireGuard endpoints', f'{a_end} {b_end}')
        ca = PathController(NsWireGuard(lab.ns['pca'], lab.wg['a']), COORD, SUBNET)
        cb = PathController(NsWireGuard(lab.ns['pcb'], lab.wg['b']), COORD, SUBNET)
        a_pub, b_pub = lab.keys['a']['public'], lab.keys['b']['public']
        lab.echo_server('b')

        def refresh():
            ca.apply(lab.peers_for('a'))
            cb.apply(lab.peers_for('b'))

        def settled():
            refresh()
            sa, sb = ca.tick(), cb.tick()
            if b_pub not in sa or a_pub not in sb:
                return None
            ma, mb = sa[b_pub]['mode'], sb[a_pub]['mode']
            return (ma, mb) if ma in ('direct', 'relay') and mb in ('direct', 'relay') else None
        expected = ('relay', 'relay') if symmetric_b else ('direct', 'direct')

        def path_check(name):
            via_relay = expected == ('relay', 'relay')
            modes = wait(settled, 75, 1)
            record(modes == expected, f'[{label}] {name}: path chosen from real handshakes', modes)
            before = lab.coordinator.stats['relayed']
            ok = lab.echo('a', 'b', count=200)
            relayed = lab.coordinator.stats['relayed'] - before
            record(ok == 200, f'[{label}] {name}: 200 encrypted UDP echoes between the members', f'{ok}/200')
            if via_relay:
                record(relayed >= 400, f'[{label}] {name}: traffic went through the AKENO relay', f'{relayed} relayed')
            else:
                record(relayed == 0, f'[{label}] {name}: direct P2P, nothing went through the relay',
                       f'{relayed} relayed')

        refresh()
        path_check('first connection')

        # Network outage at A's router (WAN link down 20 s), then recovery without any user action.
        sh('ip', 'link', 'set', 'wan', 'down', ns=lab.ns['nata'])
        record(lab.echo('a', 'b', count=5, timeout=0.5) == 0, f'[{label}] outage: no traffic while A is offline')
        time.sleep(20)
        sh('ip', 'link', 'set', 'wan', 'up', ns=lab.ns['nata'])
        recovered = wait(lambda: lab.echo('a', 'b', count=3, timeout=1) == 3, 90, 1)
        record(recovered, f'[{label}] outage: traffic resumes automatically after the network returns')

        # Network change on A (new local WireGuard port, as after a Wi-Fi switch or restart): re-proof, reconnect.
        old = lab.proven('a')
        sh('wg', 'set', lab.wg['a'], 'listen-port', 51821, ns=lab.ns['pca'])
        moved = wait(lambda: (lab.proven('a') or old) != old and lab.proven('a'), 60, 1)
        record(bool(moved and moved[1] != old[1]), f'[{label}] network change: coordinator re-proved A',
               f'{old} -> {moved}')
        path_check('after the network change')

        if not symmetric_b:
            # A's router starts allocating a port per destination (endpoint-dependent mapping): the endpoint the
            # coordinator proved no longer works for B, so AKENO must fall back to the relay on its own.
            lab.masquerade('a', ports='45000-45100')
            sh('conntrack', '-F', check=False, ns=lab.ns['nata'])
            remapped = wait(lambda: (lab.proven('a') or (None, 0))[1] in range(45000, 45101) and lab.proven('a'), 60, 1)
            record(bool(remapped), f'[{label}] router remapped A and the coordinator re-proved it', remapped)
            expected = ('relay', 'relay')
            path_check('router became endpoint-dependent')

        # Revocation while traffic flows: B is removed from the room.
        lab.grants.pop(b_pub)
        lab.coordinator.revoke(b_pub)
        if symmetric_b:
            # The relay is the only path: it must stop at once, before any client reacts.
            record(lab.echo('a', 'b', count=5, timeout=0.5) == 0,
                   f'[{label}] revocation: the relay stops forwarding immediately')
        refresh()
        ca.tick()
        record(b_pub not in ca.state and b_pub not in NsWireGuard(lab.ns['pca'], lab.wg['a']).peers(),
               f'[{label}] revocation: A removed B from WireGuard')
        record(lab.echo('a', 'b', count=5, timeout=0.5) == 0, f'[{label}] revocation: no traffic to B any more')
        record(wait(lambda: lab.proven('b') is None, 5), f'[{label}] revocation: B can no longer prove an endpoint')

        # Lease expiry: a member whose credential is not renewed disappears without any server message.
        lab.grants[b_pub] = Grant(ROOM, time.time() + 3600)
        refresh()
        ca.apply([dict(p, expires=time.time() + 2) for p in lab.peers_for('a')])
        time.sleep(3)
        ca.tick()
        record(b_pub not in ca.state, f'[{label}] lease: unrenewed peer removed on expiry')
    finally:
        lab.down()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--report', type=Path, default=Path('nat-lab-report.json'))
    p.add_argument('--only', choices=('cone', 'symmetric'))
    args = p.parse_args()
    if os.geteuid() != 0 or not shutil.which('ip') or not shutil.which('iptables') or not shutil.which('wg'):
        raise SystemExit('Needs root, iproute2, iptables, conntrack and wireguard-tools.')
    checks = []

    def record(ok, name, detail=''):
        checks.append({'name': name, 'passed': bool(ok), 'detail': str(detail)[:300]})
        print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f': {detail}' if detail != '' else ''), flush=True)
    try:
        for symmetric in (False, True):
            if args.only and args.only != ('symmetric' if symmetric else 'cone'):
                continue
            run_lab(symmetric, record)
    finally:
        args.report.write_text(json.dumps({'checks': checks}, indent=2), encoding='utf-8')
    failed = [c for c in checks if not c['passed']]
    print(f'{len(checks) - len(failed)} of {len(checks)} NAT lab checks passed', flush=True)
    raise SystemExit(1 if failed else 0)


if __name__ == '__main__':
    main()
