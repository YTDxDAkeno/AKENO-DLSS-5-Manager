"""Read-only report on the public VPN Gate catalog, run from a CI runner. No VPN connection is made.

* the official catalog through AKENO's own parser (akeno.network.vpngate): countries and server counts;
* for USA, Turkey and the largest countries: whether a few servers' OpenVPN TCP ports accept a TCP connection from
  the runner (reachability from that runner only, not from a player's network; UDP servers are not probed);
* the sentences about logging, prohibited use and law enforcement from VPN Gate's own policy pages, for the terms
  and privacy review that must happen before AKENO may connect to these servers.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import socket
import sys
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PAGES = ('https://www.vpngate.net/en/about_overview.aspx', 'https://www.vpngate.net/en/about_abuse.aspx',
         'https://www.vpngate.net/en/about_faq.aspx')
KEYWORDS = re.compile(r'\b(log|logs|logging|record|prohibit|forbid|illegal|law|police|authorit|abuse|privacy|'
                      r'terms|disclaim|commercial)\w*', re.I)


def remote_of(profile: str):
    proto, remote = None, None
    for line in profile.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        if parts[0] == 'proto' and len(parts) > 1:
            proto = parts[1].lower()
        elif parts[0] == 'remote' and len(parts) > 2 and remote is None:
            remote = (parts[1], int(parts[2]) if parts[2].isdigit() else None)
    return proto, remote


def tcp_reachable(host, port, timeout=4.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return 'accepts TCP'
    except socket.timeout:
        return 'timed out'
    except OSError as exc:
        return type(exc).__name__


def policy_sentences(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'AKENO-review/1'})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(2_000_000).decode('utf-8', 'replace')
    text = html.unescape(re.sub(r'<[^>]+>', ' ', re.sub(r'(?is)<(script|style).*?</\1>', ' ', raw)))
    sentences = re.split(r'(?<=[.!?])\s+', re.sub(r'\s+', ' ', text))
    return [s.strip() for s in sentences if KEYWORDS.search(s) and 30 < len(s) < 500][:40]


def main(report: Path):
    from akeno.network.vpngate import fetch_catalog
    servers = fetch_catalog()
    counts = Counter(s.country for s in servers)
    names = {s.country: s.country_name for s in servers}
    print(f'catalog: {len(servers)} usable servers in {len(counts)} countries', flush=True)
    for code, n in counts.most_common():
        print(f'  {code} {names[code]}: {n}', flush=True)
    focus = ['US', 'TR'] + [c for c, _ in counts.most_common(5) if c not in ('US', 'TR')]
    probes = {}
    for code in focus:
        rows = []
        for s in [s for s in servers if s.country == code][:6]:
            proto, remote = remote_of(s.profile)
            if proto and proto.startswith('tcp') and remote and remote[1]:
                state = tcp_reachable(remote[0], remote[1])
            else:
                state = f'not probed ({proto or "unknown"} transport)'
            rows.append({'ip': s.ip, 'proto': proto, 'port': remote[1] if remote else None, 'state': state,
                         'logging': s.logging, 'ping_ms': s.ping_ms})
        probes[code] = rows
        reachable = sum(1 for r in rows if r['state'] == 'accepts TCP')
        print(f'{code} {names.get(code, "not in catalog")}: {counts.get(code, 0)} in catalog, '
              f'{reachable}/{len(rows)} probed TCP servers accept a connection from this runner', flush=True)
        for r in rows:
            print(f"    {r['ip']} {r['proto']}:{r['port']} -> {r['state']} (logging policy: {r['logging']!r})", flush=True)
    policies = {}
    for url in PAGES:
        try:
            policies[url] = policy_sentences(url)
        except OSError as exc:
            policies[url] = [f'not readable: {type(exc).__name__}']
        print(f'--- {url}', flush=True)
        for line in policies[url]:
            print('  ' + line, flush=True)
    report.write_text(json.dumps({'servers': len(servers), 'countries': dict(counts), 'probes': probes,
                                  'policies': policies}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--report', type=Path, default=Path('vpngate-report.json'))
    main(p.parse_args().report)
