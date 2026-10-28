"""Dedicated-container setup only; never use on a host or join host/production network namespaces."""
import base64
import ipaddress
import json
import os
import re
import subprocess
from pathlib import Path

from agent import AgentError, private_read, public_key


def call(*argv, data=None):
    try:
        return subprocess.run(argv, input=data, text=True, capture_output=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        raise AgentError('Exit namespace setup failed; no public listener should be enabled.') from None


def rules(v4, v6):
    # Values have already been canonicalized as subnets, never arbitrary configuration text.
    return f'''
table inet akeno_vpn {{
  set leased4 {{ type ipv4_addr; flags timeout; timeout 30s; size 250; }}
  set leased6 {{ type ipv6_addr; flags timeout; timeout 30s; size 250; }}
  set blocked4 {{ type ipv4_addr; flags interval; elements = {{
    0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16,
    172.16.0.0/12, 192.0.0.0/24, 192.0.2.0/24, 192.168.0.0/16,
    198.18.0.0/15, 198.51.100.0/24, 203.0.113.0/24, 224.0.0.0/4, 240.0.0.0/4 }} }}
  set blocked6 {{ type ipv6_addr; flags interval; elements = {{
    ::/128, ::1/128, ::ffff:0:0/96, 64:ff9b:1::/48, 100::/64, 2001::/32,
    2001:db8::/32, 2002::/16, fc00::/7, fe80::/10, ff00::/8 }} }}
  chain input {{ type filter hook input priority filter; policy drop;
    iifname "lo" accept
    ct state established,related accept
    iifname "eth0" udp dport 51820 limit rate 1000/second burst 1000 packets accept
    iifname "eth0" meta l4proto {{ icmp, ipv6-icmp }} limit rate 20/second accept
  }}
  chain forward {{ type filter hook forward priority filter; policy drop;
    ct state invalid drop
    iifname "akenoexit0" ip daddr @blocked4 counter drop comment "akeno-denied-ipv4-destination"
    iifname "akenoexit0" ip6 daddr @blocked6 counter drop comment "akeno-denied-ipv6-destination"
    iifname "akenoexit0" tcp dport {{ 25, 135, 139, 445, 3389 }} drop
    iifname "akenoexit0" udp dport {{ 135, 137, 138, 139, 445, 3389 }} drop
    iifname "akenoexit0" limit rate over 8000000 bytes/second drop
    iifname "akenoexit0" oifname "eth0" ip saddr {v4} ip saddr @leased4 accept
    iifname "akenoexit0" oifname "eth0" ip6 saddr {v6} ip6 saddr @leased6 accept
    iifname "eth0" oifname "akenoexit0" ip daddr @leased4 ct state established,related accept
    iifname "eth0" oifname "akenoexit0" ip6 daddr @leased6 ct state established,related accept
  }}
  chain output {{ type filter hook output priority filter; policy accept; }}
}}
table ip akeno_exit_nat {{
  chain postrouting {{ type nat hook postrouting priority srcnat; policy accept;
    oifname "eth0" ip saddr {v4} masquerade
  }}
}}
table ip6 akeno_exit_nat6 {{
  chain postrouting {{ type nat hook postrouting priority srcnat; policy accept;
    oifname "eth0" ip6 saddr {v6} masquerade
  }}
}}
'''


def main():
    # The marker catches accidental host invocation. It cannot detect Docker host-network mode;
    # compose.yml deliberately has no such mode and operators must preserve its dedicated namespace.
    if not Path('/.dockerenv').exists():
        raise AgentError('This entrypoint is for a dedicated Docker network namespace only.')
    cfg = json.loads(Path('/run/operator.json').read_bytes())
    if set(cfg) != {'control_origin', 'server_id', 'public_key', 'ipv4_subnet', 'ipv6_subnet'}:
        raise AgentError('Invalid operator configuration.')
    v4, v6 = ipaddress.IPv4Network(cfg['ipv4_subnet']), ipaddress.IPv6Network(cfg['ipv6_subnet'])
    if v4.prefixlen != 24 or not v4.subnet_of(ipaddress.IPv4Network('10.204.0.0/16')) or \
            v6.prefixlen != 64 or not v6.subnet_of(ipaddress.IPv6Network('fd42:204::/48')):
        raise AgentError('Use a dedicated registered AKENO exit subnet.')
    public_key(cfg['public_key'])
    if not re.fullmatch('[a-f0-9]{32}', cfg['server_id']):
        raise AgentError('Invalid registered server identity.')
    # Validate control origin/credential before creating even a local interface.
    from agent import Control
    Control(cfg['control_origin'], cfg['server_id'], '/run/agent.token')
    try:
        key = private_read('/run/wireguard.key', 64).decode('ascii').strip()
        raw = base64.b64decode(key, validate=True)
        if len(raw) != 32:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise AgentError('Invalid protected WireGuard private key file.') from None
    # The private key only enters wg via its protected file path; never process args or captured stdout.
    # Apply a single atomic nft transaction before the interface can accept any peer.
    call('/usr/sbin/nft', '-c', '-f', '-', data=rules(str(v4), str(v6)))
    call('/usr/sbin/nft', '-f', '-', data=rules(str(v4), str(v6)))
    created = False
    try:
        call('/usr/sbin/ip', 'link', 'add', 'akenoexit0', 'type', 'wireguard')
        created = True
        call('/usr/bin/wg', 'set', 'akenoexit0', 'private-key', '/run/wireguard.key', 'listen-port', '51820')
        actual = call('/usr/bin/wg', 'show', 'akenoexit0', 'public-key').strip()
        if actual != cfg['public_key']:
            raise AgentError('Exit private key does not match its registered public identity.')
        call('/usr/sbin/ip', 'address', 'add', f'{v4.network_address + 1}/24', 'dev', 'akenoexit0')
        call('/usr/sbin/ip', '-6', 'address', 'add', f'{v6.network_address + 1}/64', 'dev', 'akenoexit0')
        call('/usr/sbin/ip', 'link', 'set', 'dev', 'akenoexit0', 'mtu', '1380', 'up')
        args = ['python', '/opt/akeno-exit/agent.py', '--control-origin', cfg['control_origin'],
                '--server-id', cfg['server_id'], '--public-key', cfg['public_key'],
                '--secret-file', '/run/agent.token', '--ipv4-pool', str(v4), '--ipv6-pool', str(v6)]
        os.execvp('python', args)
    except Exception:
        if created:
            call('/usr/sbin/ip', 'link', 'delete', 'akenoexit0')
        raise


if __name__ == '__main__':
    main()
