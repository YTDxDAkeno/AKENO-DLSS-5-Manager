"""Actual encrypted WireGuard packet tests in isolated Linux containers (not Windows/NAT evidence).

Build tools/network_e2e/Dockerfile as akeno-network-e2e first, with the managed proxy CA. Uses an internal
Docker network, no host routes, no public ports and no Internet exit. Private keys stay inside containers.
"""
import argparse, hashlib, json, os, subprocess, time, uuid
from pathlib import Path


def main(report):
    prefix='akeno-wg-'+uuid.uuid4().hex[:10]
    net=prefix+'-net';a=prefix+'-a';b=prefix+'-b'
    env=os.environ.copy()
    for k in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):env.pop(k,None)
    def docker(*args,input=None,check=True):
        p=subprocess.run(['docker','--host=unix:///var/run/docker.sock',*args],input=input,
                         text=True,capture_output=True,env=env,timeout=60)
        if check and p.returncode:raise RuntimeError(p.stderr.strip()[:300])
        return p
    def shell(name,script,check=True):return docker('exec',name,'sh','-ec',script,check=check)
    checks=[]
    def record(name,ok):
        checks.append({'name':name,'passed':bool(ok)})
        if not ok:raise AssertionError(name)
    try:
        docker('network','create','--internal',net)
        for n in (a,b):
            docker('run','-d','--name',n,'--network',net,'--cap-add','NET_ADMIN','--device-cgroup-rule','c 10:200 rwm','akeno-network-e2e')
            shell(n,'umask 077; wg genkey > /run/key; wg pubkey < /run/key > /run/public; mkdir -p /dev/net; mknod /dev/net/tun c 10 200; wireguard-go wg0 > /run/wireguard.log 2>&1')
        def ip(n):return next(iter(json.loads(docker('inspect',n).stdout)[0]['NetworkSettings']['Networks'].values()))['IPAddress']
        ap,bp=shell(a,'cat /run/public').stdout.strip(),shell(b,'cat /run/public').stdout.strip()
        def configure(n,own,peer,peer_public,endpoint):
            script='''from pathlib import Path
p=Path('/run/key').read_text().strip()
Path('/run/wg.conf').write_text('[Interface]\\nPrivateKey = '+p+'\\nListenPort = 51820\\n[Peer]\\nPublicKey = '+PEER+'\\nAllowedIPs = '+IP+'/32\\nEndpoint = '+ENDPOINT+':51820\\nPersistentKeepalive = 1\\n')
'''
            # The substitution contains public keys and allocated addresses only, never private key material.
            script=f'PEER={peer_public!r}\nIP={peer!r}\nENDPOINT={endpoint!r}\n'+script
            docker('exec','-i',n,'python3','-',input=script)
            shell(n,f'wg setconf wg0 /run/wg.conf; ip address add {own}/32 dev wg0; ip link set wg0 mtu 1280 up; ip route add {peer}/32 dev wg0')
            shell(n,'iptables -A INPUT -i wg0 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT; '
                    'iptables -A INPUT -i wg0 -p tcp --dport 43000 -j ACCEPT; iptables -A INPUT -i wg0 -j DROP')
        configure(a,'10.203.250.2','10.203.250.3',bp,ip(b))
        configure(b,'10.203.250.3','10.203.250.2',ap,ip(a))
        echo='''import socket
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('10.203.250.2',43000));s.listen()
while True:
 c,_=s.accept()
 try:
  while True:
   data=c.recv(65536)
   if not data:break
   c.sendall(data)
 finally:c.close()
'''
        docker('exec','-i',a,'sh','-c','cat > /run/echo.py',input=echo)
        docker('exec','-d',a,'python3','/run/echo.py')
        send='''import hashlib,json,os,socket
payload=os.urandom(65536)
s=socket.create_connection(('10.203.250.2',43000),timeout=3);s.settimeout(3);s.sendall(payload);data=b''
while len(data)<len(payload):data+=s.recv(len(payload)-len(data))
assert data==payload
print(json.dumps({'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}))
'''
        time.sleep(0.5)
        p=docker('exec','-i',b,'python3','-',input=send)
        record('65536 bytes echoed over actual virtual IPs',json.loads(p.stdout)['bytes']==65536)
        handshake=int(shell(b,'wg show wg0 latest-handshakes').stdout.split()[1])
        record('actual WireGuard handshake',handshake>0)
        record('encrypted transfer counters',sum(map(int,shell(b,'wg show wg0 transfer').stdout.split()[1:]))>65536)
        shell(a,'wg set wg0 peer '+bp+' remove')
        blocked=docker('exec','-i',b,'python3','-',input=send,check=False)
        record('peer removal blocks actual traffic',blocked.returncode!=0)
        shell(a,'wg setconf wg0 /run/wg.conf')
        time.sleep(0.5)
        p=docker('exec','-i',b,'python3','-',input=send)
        record('actual encrypted reconnection after peer restore',json.loads(p.stdout)['bytes']==65536)
    finally:
        for n in (a,b):docker('rm','-f',n,check=False)
        docker('network','rm',net,check=False)
        report.write_text(json.dumps({'platform':'real Linux wireguard-go/TUN containers','checks':checks,
            'not_tested':['Windows drivers','different NATs','public P2P','relay fallback','LAN game discovery']},indent=2))
    print(f'{len(checks)} actual WireGuard checks passed')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--report',type=Path,default=Path('/tmp/akeno-wireguard-e2e.json'))
    main(p.parse_args().report)
