"""Exploration on CI runners for the free VPN (VPN Gate over OpenVPN). Development evidence, not a product test.

    python tools/free_vpn_explore.py catalog            # Linux: catalog structure, profile inventory, policy text
    python tools/free_vpn_explore.py openvpn-msi DIR    # Windows: locate/download the official OpenVPN MSI, describe it
    python tools/free_vpn_explore.py connect EXE        # Windows (admin): try real VPN Gate servers with AKENO configs

Prints structural facts only: directive names and counts, certificate algorithms, country counts. The VPN Gate
client keys inside the public catalog are the same published values for every user; they are never printed.
"""
from __future__ import annotations

import base64
import collections
import csv
import hashlib
import html
import io
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akeno.network import openvpn_profile  # noqa: E402
from akeno.network.vpngate import HEADER, URL  # noqa: E402


def get(url, limit=8 * 1024 * 1024, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'AKENO-free-vpn-review/1'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read(limit)


def rows():
    text = get(URL).decode('utf-8-sig')
    data = list(csv.reader(io.StringIO(text)))
    return [dict(zip(HEADER, r)) for r in data[2:] if len(r) == len(HEADER)]


def catalog():
    servers = rows()
    print(f'catalog rows: {len(servers)}')
    names, blocks, reasons, ciphers, auths, protos = (collections.Counter() for _ in range(6))
    ca_subjects, key_types, accepted, countries = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    from cryptography import x509
    for row in servers:
        countries[row['CountryShort']] += 1
        try:
            profile = base64.b64decode(row['OpenVPN_ConfigData_Base64']).decode('utf-8')
        except Exception:
            reasons['undecodable'] += 1
            continue
        inblock = False
        for line in profile.splitlines():
            s = line.strip()
            m = re.fullmatch(r'<(/?)([a-z0-9-]+)>', s.lower())
            if m:
                inblock = not m[1]
                if not m[1]:
                    blocks[m[2]] += 1
                continue
            if inblock or not s or s[0] in '#;':
                continue
            parts = s.split()
            names[parts[0].lower()] += 1
            if parts[0].lower() == 'cipher':
                ciphers[' '.join(parts[1:])] += 1
            if parts[0].lower() == 'auth':
                auths[' '.join(parts[1:])] += 1
            if parts[0].lower() == 'proto':
                protos[' '.join(parts[1:])] += 1
        ca = re.search(r'<ca>(.*?)</ca>', profile, re.S)
        if ca:
            for pem in re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', ca[1], re.S):
                cert = x509.load_pem_x509_certificate(pem.encode())
                ca_subjects[f'{cert.subject.rfc4514_string()[:90]} | sig={cert.signature_hash_algorithm.name if cert.signature_hash_algorithm else None} | until={cert.not_valid_after_utc.date()}'] += 1
        cert = re.search(r'<cert>(.*?)</cert>', profile, re.S)
        if cert:
            c = x509.load_pem_x509_certificate(cert[1].strip().encode())
            key_types[f'client cert {c.subject.rfc4514_string()[:60]} issuer={c.issuer.rfc4514_string()[:60]} '
                      f'key={type(c.public_key()).__name__}:{getattr(c.public_key(), "key_size", "-")} '
                      f'sig={c.signature_hash_algorithm.name if c.signature_hash_algorithm else None} until={c.not_valid_after_utc.date()}'] += 1
        try:
            p = openvpn_profile.parse(profile, row['IP'])
            accepted[f'{p.proto} fallback={p.fallback} digest={p.digest} static={p.static_mode}'] += 1
        except openvpn_profile.ProfileRejected as exc:
            reasons[str(exc)] += 1
        except ValueError as exc:
            reasons['value: ' + str(exc)[:80]] += 1
    for title, counter in (('countries', countries), ('directives', names), ('inline blocks', blocks),
                           ('proto', protos), ('cipher', ciphers), ('auth', auths), ('CA certificates', ca_subjects),
                           ('client certificates', key_types), ('AKENO accepted', accepted), ('AKENO rejected', reasons)):
        print(f'--- {title}')
        for key, count in counter.most_common(40):
            print(f'  {count:4d}  {key}')
    print('--- logging policy values', collections.Counter(r['LogType'] for r in servers).most_common(5))
    print('--- columns of the first row (non-secret)', {k: servers[0][k] for k in HEADER[:13]} if servers else None)
    for page in ('https://www.vpngate.net/en/', 'https://www.vpngate.net/en/about_overview.aspx',
                 'https://www.vpngate.net/en/about_faq.aspx', 'https://www.vpngate.net/en/howto_openvpn.aspx'):
        try:
            raw = get(page).decode('utf-8', 'replace')
        except Exception as exc:
            print('---', page, 'unavailable', exc)
            continue
        links = sorted(set(re.findall(r'href="([^"#]*(?:about|terms|policy|agree|howto|api)[^"#]*)"', raw, re.I)))
        text = html.unescape(re.sub(r'<[^>]+>', ' ', re.sub(r'(?is)<(script|style).*?</\1>', ' ', raw)))
        sentences = re.split(r'(?<=[.!?])\s+', re.sub(r'\s+', ' ', text))
        keep = [s for s in sentences if re.search(r'csv|own client|client app|api|commercial|academic|profit|'
                r'terms|agree|permission|redistribut|license|prohibit|research|experiment', s, re.I) and 20 < len(s) < 600]
        print('---', page, 'links:', links[:60])
        for sentence in keep[:60]:
            print('   ', sentence)


def openvpn_msi(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    found = []
    try:
        releases = json.loads(get('https://api.github.com/repos/OpenVPN/openvpn/releases?per_page=10'))
        for release in releases:
            for asset in release.get('assets', []):
                if re.fullmatch(r'OpenVPN-2\.\d+\.\d+-I\d+-amd64\.msi', asset['name']):
                    found.append((release['tag_name'], asset['name'], asset['browser_download_url']))
    except Exception as exc:
        print('github releases unavailable', exc)
    try:
        page = get('https://openvpn.net/community-downloads/').decode('utf-8', 'replace')
        for url in sorted(set(re.findall(r'https://swupdate\.openvpn\.(?:org|net)/community/releases/OpenVPN-2\.\d+\.\d+-I\d+-amd64\.msi', page))):
            found.append(('openvpn.net', url.rsplit('/', 1)[1], url))
    except Exception as exc:
        print('community downloads page unavailable', exc)
    for item in found[:12]:
        print('candidate', item)
    if not found:
        raise SystemExit('no MSI found')
    tag, name, url = found[0]
    data = get(url, limit=64 * 1024 * 1024, timeout=120)
    path = folder / name
    path.write_bytes(data)
    print('downloaded', name, len(data), 'sha256', hashlib.sha256(data).hexdigest(), 'from', url)
    for other in ('https://swupdate.openvpn.org/community/releases/' + name,
                  'https://swupdate.openvpn.net/community/releases/' + name):
        try:
            print('mirror', other, hashlib.sha256(get(other, limit=64 * 1024 * 1024, timeout=120)).hexdigest())
        except Exception as exc:
            print('mirror unavailable', other, exc)
    ps = (f"$s=Get-AuthenticodeSignature '{path}'; $s.Status; $s.SignerCertificate.Subject; $s.SignerCertificate.Issuer;"
          f"$i=New-Object -ComObject WindowsInstaller.Installer; $d=$i.GetType().InvokeMember('OpenDatabase','InvokeMethod',$null,$i,@('{path}',0));"
          "$v=$d.GetType().InvokeMember('OpenView','InvokeMethod',$null,$d,@('SELECT Feature, Feature_Parent, Title, Level FROM Feature'));"
          "$v.GetType().InvokeMember('Execute','InvokeMethod',$null,$v,$null);"
          "while($r=$v.GetType().InvokeMember('Fetch','InvokeMethod',$null,$v,$null)){ 'feature: ' + $r.GetType().InvokeMember('StringData','GetProperty',$null,$r,1) + ' parent=' + $r.GetType().InvokeMember('StringData','GetProperty',$null,$r,2) + ' level=' + $r.GetType().InvokeMember('IntegerData','GetProperty',$null,$r,4) + ' title=' + $r.GetType().InvokeMember('StringData','GetProperty',$null,$r,3) }")
    print(subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True).stdout)
    (folder / 'msi.txt').write_text(str(path))


def public_ip():
    out = {}
    for name, url in (('ipify', 'https://api.ipify.org?format=json'), ('ipinfo', 'https://ipinfo.io/json'),
                      ('country.is', 'https://api.country.is/'), ('ifconfig.co', 'https://ifconfig.co/json')):
        try:
            data = json.loads(get(url, limit=16384, timeout=10))
            out[name] = {k: data.get(k) for k in ('ip', 'country', 'country_iso') if k in data}
        except Exception as exc:
            out[name] = f'unavailable: {type(exc).__name__}'
    return out


def connect(exe):
    exe = Path(exe)
    before = public_ip()
    print('before', before)
    servers = rows()
    work = Path('free-vpn-explore')
    work.mkdir(exist_ok=True)
    tapctl = exe.parent / 'tapctl.exe'
    made = subprocess.run([str(tapctl), 'create', '--hwid', 'root\\tap0901', '--name', openvpn_profile.ADAPTER],
                          capture_output=True, text=True)
    print('tapctl create', made.returncode, made.stdout.strip()[:200], made.stderr.strip()[:200])
    tried = 0
    for country in ('JP', 'KR', 'US'):
        for row in [r for r in servers if r['CountryShort'] == country][:6]:
            profile = base64.b64decode(row['OpenVPN_ConfigData_Base64']).decode('utf-8')
            try:
                safe = openvpn_profile.parse(profile, row['IP'])
            except ValueError as exc:
                print(country, row['IP'], 'rejected', exc)
                continue
            if tried >= 6:
                return
            tried += 1
            log = work / f'{tried}.log'
            conf = work / f'{tried}.ovpn'
            pinned = True
            name = 'opengw.net' if pinned else None
            variant = 'full' if tried % 2 == 1 else 'no-ipv6-block'
            text_conf = openvpn_profile.render(safe, server_name=name).replace('mute 20', 'verb 4')
            if variant == 'no-ipv6-block':
                text_conf = text_conf.replace('redirect-gateway def1 ipv6', 'redirect-gateway def1').replace('block-ipv6\n', '')
            conf.write_text(text_conf)
            start = time.monotonic()
            process = subprocess.Popen([str(exe), '--config', str(conf.resolve()), '--log', str(log.resolve())])
            state = 'timeout'
            while time.monotonic() - start < 50:
                text = log.read_text(errors='replace') if log.exists() else ''
                if 'Initialization Sequence Completed' in text:
                    state = 'connected'
                    break
                if process.poll() is not None:
                    state = f'exited {process.returncode}'
                    break
                time.sleep(1)
            took = round(time.monotonic() - start, 1)
            after = public_ip()
            route = subprocess.run(['powershell', '-NoProfile', '-Command',
                                    "Get-NetRoute -AddressFamily IPv4 | Where-Object DestinationPrefix -in '0.0.0.0/1','128.0.0.0/1','0.0.0.0/0' | ForEach-Object { $_.DestinationPrefix + ' ' + $_.InterfaceAlias }"],
                                   capture_output=True, text=True).stdout.strip().replace('\n', '; ')
            process.terminate()
            try:
                process.wait(20)
            except subprocess.TimeoutExpired:
                process.kill()
            text = log.read_text(errors='replace') if log.exists() else ''
            after_push = [l for l in text.split('PUSH_REPLY', 1)[1].splitlines()[1:400]
                          if not re.search(r'UDPv4|TCPv4|read|write|\[R\]|\[W\]|Data Channel|ACK', l)][:90] if 'PUSH_REPLY' in text else []
            interesting = [l[25:200] for l in text.splitlines() if re.search(
                r'error|fail|warn|cipher|tls|peer|ncp|initializ|PUSH|route|dns|ipv6|wintun|block|verify|certificate',
                l, re.I)][-30:]
            dns = subprocess.run(['powershell', '-NoProfile', '-Command',
                                  "Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object ServerAddresses | ForEach-Object { $_.InterfaceAlias + '=' + ($_.ServerAddresses -join ',') }"],
                                 capture_output=True, text=True).stdout.strip().replace('\n', '; ') if state == 'connected' else ''
            print(f'=== {country} {row["IP"]} {safe.proto}:{safe.port} host={row["HostName"]} pinned={name} variant={variant} '
                  f'catalog-ping={row["Ping"]} -> {state} in {took}s')
            print('    dns while up:', dns)
            print('    routes while up:', route)
            print('    exit:', after)
            for line in interesting:
                print('    |', line)
            for line in after_push:
                print('    >', line[:200])
            time.sleep(3)


if __name__ == '__main__':
    {'catalog': lambda: catalog(), 'openvpn-msi': lambda: openvpn_msi(sys.argv[2]),
     'connect': lambda: connect(sys.argv[2])}[sys.argv[1]]()
