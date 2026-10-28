"""Real relay-only WireGuard/game traffic and outer packet captures in isolated Docker containers.

Build Dockerfile.relay with the managed CA first (tag akeno-relay-e2e:latest). Four independent wireguard-go/TUN
clients and the actual AKENO coordinator run on an internal Docker network, without host routes, published ports
or an Internet exit. Membership is a trusted local fixture, not an authentication/API test. Private keys remain
in the disposable containers. Captures contain only this artificial network and are written outside the checkout.
This does not establish Windows, two-ISP, public NAT or externally geolocated VPN behavior.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from akeno.network.lan import PathController  # noqa: E402


def pcap_udp(path):
    """Read this fixture's Ethernet/IPv4 UDP captures without another parser dependency."""
    raw = path.read_bytes()
    if len(raw) < 24 or raw[:4] not in (b'\xd4\xc3\xb2\xa1', b'\xa1\xb2\xc3\xd4'):
        raise AssertionError('Missing or unsupported tcpdump capture')
    endian = '<' if raw[:4] == b'\xd4\xc3\xb2\xa1' else '>'
    if struct.unpack(endian + 'I', raw[20:24])[0] != 1:
        raise AssertionError('Expected Ethernet capture')
    at, out = 24, []
    while at + 16 <= len(raw):
        length = struct.unpack(endian + 'IIII', raw[at:at + 16])[2]
        packet = raw[at + 16:at + 16 + length]
        at += 16 + length
        if len(packet) < 42 or packet[12:14] != b'\x08\x00' or packet[23] != 17:
            continue
        ihl = (packet[14] & 15) * 4
        sport, dport, size, _ = struct.unpack('!HHHH', packet[14 + ihl:22 + ihl])
        out.append({'source': socket.inet_ntoa(packet[26:30]), 'destination': socket.inet_ntoa(packet[30:34]),
                    'sport': sport, 'dport': dport, 'body': packet[22 + ihl:14 + ihl + size]})
    if at != len(raw):
        raise AssertionError('Truncated packet capture')
    return out


def run(args):
    env = os.environ.copy()
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        env.pop(key, None)
    prefix = 'akeno-relay-' + uuid.uuid4().hex[:8]
    network, relay = prefix + '-net', prefix + '-relay'
    names = {side: prefix + '-' + side for side in 'abcd'}
    checks, controllers, clients = [], {}, {}
    args.evidence.mkdir(parents=True, exist_ok=False)

    def docker(*command, input=None, check=True):
        result = subprocess.run(['docker', '--host=unix:///var/run/docker.sock', *command], input=input,
                                text=True, capture_output=True, env=env, timeout=60)
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip()[:500])
        return result

    def sh(name, script, check=True):
        return docker('exec', name, 'sh', '-ec', script, check=check).stdout

    def py(name, source, check=True):
        return docker('exec', '-i', name, 'python3', '-', input=source, check=check)

    def write(name, path, source):
        docker('exec', '-i', name, 'sh', '-ec', 'umask 077; cat > ' + path, input=source)

    def address(name):
        info = json.loads(docker('inspect', name).stdout)[0]
        return next(iter(info['NetworkSettings']['Networks'].values()))['IPAddress']

    def record(ok, name, detail=''):
        checks.append({'name': name, 'passed': bool(ok), 'detail': str(detail)[:300]})
        print(('PASS ' if ok else 'FAIL ') + name + (': ' + str(detail) if detail else ''), flush=True)
        if not ok:
            raise AssertionError(name)

    def wait(predicate, seconds=15):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            value = predicate()
            if value:
                return value
            time.sleep(.2)
        return predicate()

    class Backend:
        def __init__(self, name):
            self.name = name

        def set_peer(self, public, endpoint, virtual_ip):
            sh(self.name, f'wg set wg0 peer {public} endpoint {endpoint[0]}:{endpoint[1]} '
                          f'allowed-ips {virtual_ip}/32 persistent-keepalive 2')

        def remove_peer(self, public):
            sh(self.name, f'wg set wg0 peer {public} remove')

        def peers(self):
            # The interface header contains the private key; discard it here and never log the dump.
            out = {}
            for row in sh(self.name, 'wg show wg0 dump').splitlines()[1:]:
                fields = row.split('\t')
                host, port = fields[2].rsplit(':', 1) if fields[2] != '(none)' else (None, 0)
                out[fields[0]] = {'endpoint': (host, int(port)) if host else None,
                                  'latest_handshake': int(fields[4]), 'rx': int(fields[5]), 'tx': int(fields[6])}
            return out

    def echo(side, target, count=8, port=43000):
        source = f'''import os,socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(.5)
ok=0
for n in range({count}):
 payload=b'AKENO-RELAY-PRIVATE-PAYLOAD:'+os.urandom(900)
 try:
  s.sendto(payload,({clients[target]['virtual_ip']!r},{port}))
  if s.recvfrom(4096)[0]==payload:ok+=1
 except OSError:pass
print(ok)
'''
        return int(py(names[side], source).stdout.strip())

    try:
        docker('network', 'create', '--internal', network)
        docker('run', '-d', '--name', relay, '--network', network, args.image)
        relay_ip = address(relay)
        for side, name in names.items():
            docker('run', '-d', '--name', name, '--network', network, '--cap-add', 'NET_ADMIN',
                   '--device-cgroup-rule', 'c 10:200 rwm', args.image)
            sh(name, 'umask 077; wg genkey > /run/key; wg pubkey < /run/key > /run/public; '
                     'mkdir -p /dev/net; mknod /dev/net/tun c 10 200; wireguard-go wg0 >/run/wg.log 2>&1')
            clients[side] = {'public': sh(name, 'cat /run/public').strip(), 'outer_ip': address(name),
                             'virtual_ip': '10.203.250.' + str(2 + 'abcd'.index(side)),
                             'room': 'other-room' if side == 'd' else 'three-player-room'}
        # Copy only the production coordinator protocol implementation, not the licensing database or any keys.
        for path in ('/run/akeno_server', '/run/akeno_server/network'):
            sh(relay, 'mkdir -p ' + path + '; touch ' + path + '/__init__.py')
        for module in ('coordinator.py', 'wireguard.py'):
            write(relay, '/run/akeno_server/network/' + module,
                  (ROOT / 'server' / 'akeno_server' / 'network' / module).read_text())
        grants = {c['public']: {'room': c['room'], 'expires': time.time() + 600} for c in clients.values()}
        write(relay, '/run/grants.json', json.dumps(grants))
        write(relay, '/run/coordinator.py', '''import json,time
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from akeno_server.network.coordinator import Coordinator,Grant
def authority(key):
 value=json.loads(Path('/run/grants.json').read_text()).get(key)
 return Grant(value['room'],value['expires']) if value else None
c=Coordinator(X25519PrivateKey.generate(),authority,bind=('0.0.0.0',3478)).start()
Path('/run/public.json').write_text(json.dumps({'public_key':c.public_key}))
while True:
 command=Path('/run/revoke.json')
 if command.exists():
  for key in json.loads(command.read_text()):c.revoke(key)
  command.unlink();Path('/run/revoked').touch()
 Path('/run/stats.json').write_text(json.dumps(c.stats))
 time.sleep(.05)
''')
        docker('exec', '-d', relay, 'python3', '/run/coordinator.py')
        wait(lambda: sh(relay, 'test -f /run/public.json && cat /run/public.json', check=False))
        public = json.loads(sh(relay, 'cat /run/public.json'))['public_key']
        for side, name in names.items():
            config = f'''import os
from pathlib import Path
key=Path('/run/key').read_text().strip()
fd=os.open('/run/wg.conf',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:
 f.write('[Interface]\\nPrivateKey = '+key+'\\nListenPort = 51820\\n[Peer]\\nPublicKey = {public}\\nEndpoint = {relay_ip}:3478\\nAllowedIPs = 10.203.0.1/32\\nPersistentKeepalive = 2\\n')
'''
            py(name, config)
            # Outer socket is relay-only even if a peer attempts authenticated roaming. No direct fallback.
            sh(name, f'iptables -A OUTPUT -o eth0 -p udp --sport 51820 -d {relay_ip} --dport 3478 -j ACCEPT; '
                     'iptables -A OUTPUT -o eth0 -p udp --sport 51820 -j DROP; '
                     f'iptables -A INPUT -i eth0 -p udp --dport 51820 -s {relay_ip} --sport 3478 -j ACCEPT; '
                     'iptables -A INPUT -i eth0 -p udp --dport 51820 -j DROP')
            py(name, '''import subprocess
from pathlib import Path
p=subprocess.Popen(['tcpdump','-i','eth0','-U','-w','/run/outer.pcap','udp'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
Path('/run/capture.pid').write_text(str(p.pid))
''')
            sh(name, f'wg setconf wg0 /run/wg.conf; ip address add {clients[side]["virtual_ip"]}/24 dev wg0; '
                     'ip link set wg0 mtu 1280 up; iptables -A INPUT -i wg0 -p udp --dport 43000 -j ACCEPT; '
                     'iptables -A INPUT -i wg0 -p udp --dport 3979 -j ACCEPT; '
                     'iptables -A INPUT -i wg0 -p tcp --dport 3979 -j ACCEPT; '
                     'iptables -A INPUT -i wg0 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT; '
                     'iptables -A INPUT -i wg0 -j DROP')
            write(name, '/run/echo.py', f'''import socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(({clients[side]['virtual_ip']!r},43000))
while True:
 data,peer=s.recvfrom(4096);s.sendto(data,peer)
''')
            docker('exec', '-d', name, 'python3', '/run/echo.py')
            controllers[side] = PathController(Backend(name), (relay_ip, 3478), '10.203.250.0/24')
            peers = [{'public_key': c['public'], 'virtual_ip': c['virtual_ip'], 'expires': time.time() + 45}
                     for s, c in clients.items() if s != side and c['room'] == clients[side]['room']]
            controllers[side].apply(peers)
            record(all('endpoint' not in p for p in peers), f'{side}: no peer NAT endpoint in relay control data')
        def settled():
            return all(all(p['mode'] == 'relay' for p in controllers[s].tick().values()) for s in 'abc')
        record(wait(settled, 30), 'three independent WireGuard clients establish relay-only handshakes')
        for side, target in (('a', 'b'), ('b', 'c'), ('c', 'a')):
            record(echo(side, target) == 8, f'{side}->{target}: encrypted virtual-IP datagrams echoed via actual relay')
        # A member cannot turn the relay into a cross-room proxy even if it knows a foreign public key/IP.
        controllers['d'].apply([{'public_key': clients['a']['public'], 'virtual_ip': clients['a']['virtual_ip'],
                                  'expires': time.time() + 45}])
        record(echo('d', 'a', count=3) == 0, 'different room cannot establish or pass traffic through relay')
        # Real OpenTTD dedicated server, not a game-protocol mock. Queries use its virtual address.
        sh(names['b'], 'mkdir -p /run/openttd; printf "[network]\\nserver_name = AKENO Relay Lab\\n" > /run/openttd/openttd.cfg')
        docker('exec', '-d', names['b'], '/usr/games/openttd', '-D', clients['b']['virtual_ip'] + ':3979',
               '-c', '/run/openttd/openttd.cfg')
        game_query = f'''import json,socket
ip={clients['b']['virtual_ip']!r}
u=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);u.settimeout(3);u.sendto(b'\\x03\\x00\\x00',(ip,3979));found=u.recvfrom(64)[0]
t=socket.create_connection((ip,3979),timeout=3);t.sendall(b'\\x03\\x00\\x07');info=b''
while len(info)<2 or len(info)<int.from_bytes(info[:2],'little'):
 part=t.recv(4096)
 if not part:break
 info+=part
assert found[2:3]==b'\\x01' and info[2:3]==b'\\x06' and b'AKENO Relay Lab' in info
print(json.dumps({{'udp_discovery':True,'tcp_game_info':True}}))
'''
        game = wait(lambda: py(names['a'], game_query, check=False).returncode == 0, 45)
        record(game, 'real OpenTTD UDP discovery response and TCP game information across relay')
        # Force a direct endpoint as an authenticated-roaming/configuration fault; the outer filter must stop it.
        sh(names['a'], f'wg set wg0 peer {clients["b"]["public"]} endpoint {clients["b"]["outer_ip"]}:51820')
        record(echo('a', 'b', count=2) == 0, 'forced direct endpoint cannot send traffic past outer filter')
        controllers['a'].tick()
        record(wait(lambda: echo('a', 'b', count=2) == 2, 20), 'controller restores relay after endpoint perturbation')
        # Revoke C at the real coordinator before either remaining controller receives a new peer list.
        grants.pop(clients['c']['public'])
        write(relay, '/run/grants.json', json.dumps(grants))
        write(relay, '/run/revoke.json', json.dumps([clients['c']['public']]))
        wait(lambda: sh(relay, 'test -f /run/revoked && echo yes', check=False))
        record(echo('b', 'c', count=3) == 0, 'server revocation stops active encrypted relay traffic before client refresh')
        # No control-plane refresh: local signed lease expiry still removes the peer from real WireGuard.
        live = [{'public_key': clients['b']['public'], 'virtual_ip': clients['b']['virtual_ip'],
                 'expires': time.time() + .5}]
        controllers['a'].apply(live)
        time.sleep(.7)
        controllers['a'].tick()
        record(clients['b']['public'] not in Backend(names['a']).peers(), 'unrenewed lease removes real native peer')
        for side, name in names.items():
            py(name, "import os,signal,time;from pathlib import Path;os.kill(int(Path('/run/capture.pid').read_text()),signal.SIGINT);time.sleep(.3)")
            capture = args.evidence / (side + '-outer.pcap')
            docker('cp', name + ':/run/outer.pcap', str(capture))
            packets = pcap_udp(capture)
            outgoing = [p for p in packets if p['source'] == clients[side]['outer_ip'] and p['sport'] == 51820]
            incoming = [p for p in packets if p['destination'] == clients[side]['outer_ip'] and p['dport'] == 51820]
            record(bool(outgoing) and all(p['destination'] == relay_ip and p['dport'] == 3478 for p in outgoing),
                   f'{side}: packet capture contains no outgoing direct peer path', f'{len(outgoing)} UDP packets')
            record(bool(incoming) and all(p['source'] == relay_ip and p['sport'] == 3478 for p in incoming),
                   f'{side}: captured inbound tunnel packets came only from controlled relay')
            record(all(b'AKENO-RELAY-PRIVATE-PAYLOAD' not in p['body'] for p in packets),
                   f'{side}: application plaintext absent from outer packet capture')
        stats = json.loads(sh(relay, 'cat /run/stats.json'))
        # Three pairs exchanged eight datagrams plus their echoes: at least 48 relayed application datagrams.
        # The count of handshakes/keepalives varies with timing and is not a meaningful fixed threshold.
        record(stats['relayed'] >= 3 * 8 * 2, 'actual production coordinator relayed encrypted packets', stats)
    finally:
        for name in (*names.values(), relay):
            docker('rm', '-f', name, check=False)
        docker('network', 'rm', network, check=False)
        args.report.write_text(json.dumps({'platform': 'real Linux wireguard-go/TUN Docker containers',
                                          'checks': checks, 'control_plane': 'trusted local membership fixture',
                                          'not_tested': ['Windows/WireGuardNT/UAC', 'two physical PCs', 'two ISP NATs',
                                                         'public Internet reachability', 'external VPN exit country',
                                                         'broadcast forwarding (game queried by virtual IP)']}, indent=2))
    print(f'{len(checks)} real relay-only checks passed', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='akeno-relay-e2e:latest')
    parser.add_argument('--evidence', type=Path, default=Path('/tmp/akeno-relay-pcap-' + uuid.uuid4().hex[:8]))
    parser.add_argument('--report', type=Path, default=Path('/tmp/akeno-relay-lab.json'))
    run(parser.parse_args())
