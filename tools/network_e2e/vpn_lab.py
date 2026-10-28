"""Actual managed WireGuard exit/NAT and kernel lease-expiry checks in isolated internal Docker containers.

Uses the production ExitAgent, fixed-argument wg peer reconciler, NftLeaseFence and deployment firewall rules.
The control manifest is a trusted local fixture; account authentication, independent country/exit-IP attestation,
Internet egress and Windows are NOT tested here. Globally shaped destination literals are aliases inside the
target container only, reached through explicit routes on the internal Docker network. No public port, Internet
exit, host route, production service or real country server is enabled. Private keys stay in the containers.
Build Dockerfile.relay with the managed CA first (tag akeno-relay-e2e:latest).
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET6 = '2600:204:ffff::100'
PRIVATE4 = '10.205.250.100'
PEER4, PEER6 = '10.204.1.2', 'fd42:204:0:1::2'


def run(args):
    prefix = 'akeno-vpn-lab-' + uuid.uuid4().hex[:8]
    network = prefix + '-net'
    client, exit_node, target = (prefix + '-' + role for role in ('client', 'exit', 'target'))
    env, checks = os.environ.copy(), []
    local_ipv6 = {}
    # An internal Docker bridge drops IPv4 destinations outside its declared pool.
    # These globally shaped addresses are confined to this disposable internal network,
    # never public endpoints. Keep the target alias in that pool so bridge isolation
    # does not discard a correctly forwarded packet before it reaches the target.
    subnet4 = '11.204.' + str(uuid.uuid4().int % 254 + 1) + '.0/24'
    destination4 = str(ipaddress.ip_network(subnet4).network_address + 100)
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        env.pop(key, None)
    args.evidence.mkdir(parents=True, exist_ok=False)

    def docker(*command, input=None, check=True):
        result = subprocess.run(['docker', '--host=unix:///var/run/docker.sock', *command], input=input,
                                text=True, capture_output=True, env=env, timeout=60)
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip()[:500])
        return result

    def sh(name, source, check=True):
        return docker('exec', name, 'sh', '-ec', source, check=check).stdout

    def py(name, source, check=True):
        return docker('exec', '-i', name, 'python3', '-', input=source, check=check)

    def write(name, path, source):
        docker('exec', '-i', name, 'sh', '-ec', 'umask 077; cat > ' + path, input=source)

    def addresses(name):
        info = json.loads(docker('inspect', name).stdout)[0]
        address = next(iter(info['NetworkSettings']['Networks'].values()))
        return address['IPAddress'], local_ipv6[name]

    def record(ok, name, detail=''):
        checks.append({'name': name, 'passed': bool(ok), 'detail': str(detail)[:300]})
        print(('PASS ' if ok else 'FAIL ') + name + (': ' + str(detail) if detail else ''), flush=True)
        if not ok:
            raise AssertionError(name)

    def wait(predicate, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.2)
        return predicate()

    def echo(version, count=3, target_ip=None, source_node=None):
        destination = target_ip or (destination4 if version == 4 else TARGET6)
        source = f'''import base64,json,os,socket,time
s=socket.socket(socket.AF_INET{'' if version == 4 else '6'},socket.SOCK_DGRAM);s.settimeout(.2)
ok=0;seen=[]
for n in range({count}):
 payload=b'AKENO-VPN-PRIVATE-PAYLOAD:'+os.urandom(400)
 try:
  s.sendto(payload,({destination!r},43000))
  deadline=time.monotonic()+.6
  while time.monotonic()<deadline:
   try:answer=json.loads(s.recvfrom(4096)[0])
   except socket.timeout:continue
   if base64.b64decode(answer['payload'])==payload:
    ok+=1;seen.append(answer['source']);break
 except (OSError,ValueError,KeyError):pass
print(json.dumps({{'ok':ok,'seen':seen}}))
'''
        return json.loads(py(source_node or client, source).stdout)

    def peers():
        return sh(exit_node, 'wg show akenoexit0 peers').split()

    def denied_destination_count(version):
        document = json.loads(sh(exit_node, 'nft -j list chain inet akeno_vpn forward'))
        comment = f'akeno-denied-ipv{version}-destination'
        matching = [row['rule'] for row in document['nftables']
                    if row.get('rule', {}).get('comment') == comment]
        if len(matching) != 1 or not any('drop' in expr for expr in matching[0]['expr']):
            raise AssertionError('Missing actual destination drop rule')
        return next(expr['counter']['packets'] for expr in matching[0]['expr'] if 'counter' in expr)

    def publish(generation, public, fixed_deadline=None):
        data = {'generation': generation, 'peers': [] if public is None else [public],
                'fixed_deadline': fixed_deadline}
        write(exit_node, '/run/desired.next', json.dumps(data))
        sh(exit_node, 'mv /run/desired.next /run/desired.json')

    def agent_signal(signum):
        py(exit_node, "import os;from pathlib import Path;os.kill(int(Path('/run/agent.pid').read_text())," + str(signum) + ')')

    try:
        ula = 'fd75:' + uuid.uuid4().hex[:4] + ':204::/64'
        # Docker's IPv6 network driver may require host ip6tables-legacy modules absent in managed workers.
        # The dedicated bridge remains IPv4/internal; fixture-only IPv6 addresses/routes are set inside its
        # three isolated container namespaces. No host IPv6 route, firewall change or external route is needed.
        docker('network', 'create', '--internal', '--subnet', subnet4, network)
        for index, name in enumerate((client, exit_node, target), 2):
            attachment = 'name=' + network + ',driver-opt=com.docker.network.endpoint.sysctls=net.ipv6.conf.IFNAME.disable_ipv6=0'
            options = ['run', '-d', '--name', name, '--network', attachment, '--cap-add', 'NET_ADMIN',
                       '--device-cgroup-rule', 'c 10:200 rwm', '--sysctl', 'net.ipv6.conf.all.disable_ipv6=0',
                       '--sysctl', 'net.ipv6.conf.default.disable_ipv6=0']
            if name == exit_node:
                options += ['--sysctl', 'net.ipv4.ip_forward=1', '--sysctl', 'net.ipv6.conf.all.forwarding=1']
            docker(*options, args.image)
            local_ipv6[name] = ula.removesuffix('/64') + str(index)
            sh(name, f'ip -6 address add {local_ipv6[name]}/64 dev eth0 nodad')
            sh(name, 'tcpdump -U -n -i any -w /run/lab.pcap >/run/capture.log 2>&1 &')
        exit4, exit6 = addresses(exit_node)
        target4, target6 = addresses(target)
        for name, interface, own4, own6 in ((client, 'wg0', PEER4, PEER6),
                                           (exit_node, 'akenoexit0', '10.204.1.1', 'fd42:204:0:1::1')):
            sh(name, f'umask 077; wg genkey > /run/key; wg pubkey < /run/key > /run/public; '
                     f'mkdir -p /dev/net; mknod /dev/net/tun c 10 200; wireguard-go {interface} >/run/wg.log 2>&1; '
                     f'wg set {interface} private-key /run/key listen-port 51820; '
                     f'ip address add {own4}/24 dev {interface}; ip -6 address add {own6}/64 dev {interface}; '
                     f'ip link set {interface} mtu 1280 up')
        client_public = sh(client, 'cat /run/public').strip()
        exit_public = sh(exit_node, 'cat /run/public').strip()
        # Production rules are used verbatim; only route destinations and network namespace are fixture-local.
        write(exit_node, '/run/agent.py', (ROOT / 'server/akeno_server/vpn/agent.py').read_text())
        write(exit_node, '/run/entrypoint.py', (ROOT / 'deploy/vpn-exit/entrypoint.py').read_text())
        py(exit_node, "import sys;sys.path.insert(0,'/run');from pathlib import Path;from entrypoint import rules;Path('/run/exit.nft').write_text(rules('10.204.1.0/24','fd42:204:0:1::/64'))")
        sh(exit_node, 'nft -c -f /run/exit.nft; nft -f /run/exit.nft')
        record(True, 'actual deployment nftables policy validates and installs in the dedicated exit namespace')
        sh(target, f'ip address add {destination4}/32 dev lo; ip address add {PRIVATE4}/32 dev lo; '
                   f'ip -6 address add {TARGET6}/128 dev lo')
        sh(exit_node, f'ip route add {destination4}/32 via {target4} dev eth0; '
                      f'ip route add {PRIVATE4}/32 via {target4} dev eth0; '
                      f'ip -6 route add {TARGET6}/128 via {target6} dev eth0')
        write(target, '/run/echo.py', '''import base64,json,socket,threading
def serve(family,address):
 s=socket.socket(family,socket.SOCK_DGRAM)
 if family==socket.AF_INET6:s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
 s.bind((address,43000))
 while True:
  payload,peer=s.recvfrom(4096)
  s.sendto(json.dumps({'payload':base64.b64encode(payload).decode(),'source':peer[0]}).encode(),peer)
'''+f'''
# Bind each destination explicitly: a wildcard UDP socket can reply from the private
# uplink address instead of the global alias, breaking the NAT conntrack tuple.
# Separate private listeners keep the blocked-destination checks meaningful.
for family,address in ((socket.AF_INET,{destination4!r}),(socket.AF_INET,{PRIVATE4!r}),
                       (socket.AF_INET6,{TARGET6!r})):
 threading.Thread(target=serve,args=(family,address),daemon=True).start()
serve(socket.AF_INET6,{target6!r})
''')
        docker('exec', '-d', target, 'python3', '/run/echo.py')
        # Literal host routes keep only this client's encrypted outer socket outside its full-tunnel routes.
        sh(client, f'wg set wg0 peer {exit_public} endpoint {exit4}:51820 allowed-ips 0.0.0.0/0,::/0 '
                   'persistent-keepalive 2; '
                   f'ip route add {exit4}/32 dev eth0; ip -6 route add {exit6}/128 dev eth0; '
                   'ip route replace default dev wg0; ip -6 route replace default dev wg0')
        for row in json.loads(sh(client, 'ip -j route show dev eth0')):
            # iproute2 renders /32 routes as a bare address. Compare networks so the
            # outer WireGuard endpoint route survives removal of the bridge subnet.
            destination = ipaddress.ip_network(row['dst'], strict=False)
            if row.get('scope') == 'link' and destination != ipaddress.ip_network(exit4 + '/32'):
                sh(client, 'ip route del ' + row['dst'] + ' dev eth0')
        for row in json.loads(sh(client, 'ip -j -6 route show dev eth0')):
            if row.get('dst') == ula:
                sh(client, 'ip -6 route del ' + row['dst'] + ' dev eth0')
        record(json.loads(sh(client, f'ip -j route get {exit4}'))[0]['dev'] == 'eth0' and
               json.loads(sh(client, f'ip -j route get {destination4}'))[0]['dev'] == 'wg0',
               'client routes only the outer WireGuard endpoint outside its full tunnel')
        publish(1, client_public)
        write(exit_node, '/run/manage.py', f'''import json,os,time
from pathlib import Path
from agent import AgentError,ExitAgent,Journal,NftLeaseFence,WireGuard
class FixtureControl:
 def fetch(self):
  desired=json.loads(Path('/run/desired.json').read_text());now=int(time.time())
  return {{'generation':desired['generation'],'expires_at':now+6,'server_public_key':{exit_public!r},
   'peers':[{{'public_key':key,'addresses':[{PEER4 + '/32'!r},{PEER6 + '/128'!r}],
    'expires_at':desired['fixed_deadline'] or now+30}} for key in desired['peers']]}}
 def heartbeat(self,generation,key):
  Path('/run/ack.json').write_text(json.dumps({{'generation':generation,'public_key':key}}));return {{}}
agent=ExitAgent(WireGuard(),FixtureControl(),Journal(Path('/run/state/peers.json')),{exit_public!r},
 fence=NftLeaseFence(),ipv4_pool='10.204.1.0/24',ipv6_pool='fd42:204:0:1::/64')
Path('/run/agent.pid').write_text(str(os.getpid()))
while True:
 try:agent.tick()
 except AgentError:pass
 time.sleep(.5)
''')
        docker('exec', '-d', exit_node, 'python3', '/run/manage.py')
        record(wait(lambda: client_public in peers()), 'actual fixed-argument agent adds a native WireGuard peer')
        record(wait(lambda: echo(4)['ok'] == 3), 'actual WireGuard client forwards IPv4 datagrams through the managed exit')
        record(wait(lambda: echo(6)['ok'] == 3), 'actual WireGuard client forwards IPv6 datagrams through the managed exit')
        record(all(ip == exit4 for ip in echo(4)['seen']), 'target observes actual namespace exit IPv4 after NAT', exit4)
        record(all(ip == exit6 for ip in echo(6)['seen']), 'target observes actual namespace exit IPv6 after NAT66', exit6)
        handshake = sh(client, 'wg show wg0 latest-handshakes').split()
        record(len(handshake) == 2 and int(handshake[1]) > 0, 'actual WireGuard handshake confirmed by the native tool')
        # Prove these services respond before checking the VPN firewall denies access to them.
        record(echo(4, target_ip=PRIVATE4, source_node=target)['ok'] == 3 and
               echo(6, target_ip=target6, source_node=target)['ok'] == 3,
               'private destination controls have live IPv4 and IPv6 echo listeners')
        for version, address in ((4, PRIVATE4), (6, target6)):
            before = denied_destination_count(version)
            record(echo(version, target_ip=address)['ok'] == 0 and denied_destination_count(version) > before,
                   f'deployment IPv{version} drop rule rejects actual packets to a private destination')
        publish(2, None)
        record(wait(lambda: client_public not in peers()), 'revoked control manifest removes the real native peer')
        record(echo(4)['ok'] == 0 and echo(6)['ok'] == 0, 'revocation blocks actual IPv4 and IPv6 forwarding')
        publish(3, client_public)
        record(wait(lambda: echo(4)['ok'] == 3), 'peer reauthorization restores actual encrypted traffic')
        # Stop all agent execution while preserving its native peer. Kernel timeout sets must enforce expiration.
        agent_signal(19)  # SIGSTOP: no Python finally handler, heartbeat or peer-removal code can execute.
        time.sleep(8)
        record(client_public in peers(), 'paused agent leaves its native WireGuard peer installed for the fence test')
        record(echo(4)['ok'] == 0 and echo(6)['ok'] == 0,
               'kernel lease expiry blocks both IP families while the actual agent is frozen')
        fence = json.loads(sh(exit_node, 'nft -j list set inet akeno_vpn leased4'))
        record(not any(row.get('set', {}).get('elem') for row in fence['nftables']), 'kernel leased IPv4 set actually expires')
        fence6 = json.loads(sh(exit_node, 'nft -j list set inet akeno_vpn leased6'))
        record(not any(row.get('set', {}).get('elem') for row in fence6['nftables']), 'kernel leased IPv6 set actually expires')
        agent_signal(18)  # SIGCONT
        record(wait(lambda: echo(4)['ok'] == 3 and echo(6)['ok'] == 3), 'resuming the agent renews fenced dual-stack forwarding')
        agent_signal(9)  # SIGKILL: an unhandled crash cannot clean up WireGuard, yet forwarding must still expire.
        time.sleep(8)
        record(client_public in peers() and echo(4)['ok'] == 0 and echo(6)['ok'] == 0,
               'agent crash leaves native peer but kernel expiry independently blocks actual traffic')
        docker('exec', '-d', exit_node, 'python3', '/run/manage.py')
        record(wait(lambda: echo(4)['ok'] == 3 and echo(6)['ok'] == 3),
               'journal recovery cleans owned state and restores forwarding after agent restart')
        publish(4, client_public, int(time.time()) + 3)
        record(wait(lambda: client_public not in peers(), seconds=10), 'expired device lease removes the actual native peer')
        record(echo(4)['ok'] == 0 and echo(6)['ok'] == 0, 'expired lease blocks real dual-stack traffic')
        docker('cp', exit_node + ':/run/ack.json', str(args.evidence / 'agent-ack.json'))
    except Exception:
        # Preserve only public native state from these disposable namespaces before cleanup.
        # Never use wg dump/showconf: those include private interface keys.
        diagnostics = {}
        for role, name in (('client', client), ('exit', exit_node), ('target', target)):
            commands = {
                'routes4': ['ip', '-j', 'route'], 'routes6': ['ip', '-j', '-6', 'route'],
                'firewall': ['nft', '-j', 'list', 'ruleset'],
                'packets': ['tcpdump', '-nn', '-r', '/run/lab.pcap', '-c', '60'],
                'forwarding4': ['cat', '/proc/sys/net/ipv4/ip_forward'],
                'reverse_path_filter': ['cat', '/proc/sys/net/ipv4/conf/all/rp_filter'],
            }
            if role != 'target':
                interface = 'wg0' if role == 'client' else 'akenoexit0'
                commands.update({field: ['wg', 'show', interface, field] for field in
                                 ('public-key', 'peers', 'latest-handshakes', 'transfer', 'allowed-ips', 'endpoints')})
            diagnostics[role] = {field: docker('exec', name, *argv, check=False).stdout
                                 for field, argv in commands.items()}
        (args.evidence / 'failure-native-state.json').write_text(json.dumps(diagnostics, indent=2))
        raise
    finally:
        for name in (client, exit_node, target):
            docker('rm', '-f', name, check=False)
        docker('network', 'rm', network, check=False)
        args.report.write_text(json.dumps({'platform': 'real Linux wireguard-go/TUN, nftables and NAT/NAT66',
            'checks': checks, 'control_plane': 'trusted synthetic local manifest; no real account/country proof',
            'exit_ips': 'observed Docker namespace uplink IPs only; never a public or geolocated country exit',
            'not_tested': ['Windows', 'Internet VPN exit', 'independent real country attestation',
                           'production API authentication in this fixture', 'public provider', 'two ISP NATs',
                           'Windows DNS restoration/standby/network change']}, indent=2))
    print(f'{len(checks)} real managed-exit checks passed', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='akeno-relay-e2e:latest')
    parser.add_argument('--evidence', type=Path, default=Path('/tmp/akeno-vpn-exit-evidence-' + uuid.uuid4().hex[:8]))
    parser.add_argument('--report', type=Path, default=Path('/tmp/akeno-vpn-exit-lab.json'))
    run(parser.parse_args())
