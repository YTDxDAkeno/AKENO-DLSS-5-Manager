"""Measured Windows DNS checks for the official full-tunnel WireGuard firewall.

Success requires a valid DNS reply from the tunnel resolver AND WSAEACCES on
foreign DNS probes, including probes bound to every eligible non-tunnel NIC.
Timeout, lack of routing and missing interfaces are never evidence of blocking.
No firewall settings, DNS configuration or routes are changed by this observer.
"""
from __future__ import annotations

import ipaddress
import json
import re
import secrets
import socket
import struct
import sys


class DNSVerificationError(ValueError):
    pass


def question():
    ident=secrets.token_bytes(2)
    name=('akeno-'+secrets.token_hex(8)+'.example.com').split('.')
    labels=b''.join(bytes([len(label)])+label.encode('ascii') for label in name)+b'\0'
    return ident+b'\x01\x00\x00\x01'+b'\0'*6+labels+b'\x00\x01\x00\x01'


def valid_reply(query, reply):
    # The echoed question binds the random transaction to the requested name; no
    # IP/country claim is derived from DNS. NXDOMAIN is a valid resolver response.
    return (isinstance(reply,bytes) and len(query)<=len(reply)<=4096 and reply[:2]==query[:2]
            and reply[2]&0x80 and not reply[2]&0x02 and reply[3]&15 in (0,3)
            and reply[4:6]==b'\0\x01' and reply[12:len(query)]==query[12:])


def _socket(address, source=None, interface=None, tcp=False):
    ip=ipaddress.ip_address(address)
    s=socket.socket(socket.AF_INET if ip.version==4 else socket.AF_INET6,
                    socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM)
    try:
        s.settimeout(3)
        if source is not None:
            if ipaddress.ip_address(source).version!=ip.version or type(interface) is not int or interface<=0:
                raise DNSVerificationError('Invalid native DNS probe interface.')
            # Windows IP_UNICAST_IF expects the IPv4 index in network byte order;
            # IPV6_UNICAST_IF expects host byte order (both documented option 31).
            s.setsockopt(socket.IPPROTO_IP if ip.version==4 else socket.IPPROTO_IPV6,31,
                         socket.htonl(interface) if ip.version==4 else interface)
            s.bind((source,0))
        return s
    except BaseException:
        s.close()
        raise


def resolver_responds(address):
    packet=question()
    with _socket(address) as s:
        s.connect((address,53))
        s.send(packet)
        return valid_reply(packet,s.recv(4096))


def blocked(address, *, source=None, interface=None, tcp=False, port=53):
    # Bind/configuration errors happen outside the measured send/connect. Only
    # Windows' explicit network access denial is positive block evidence.
    with _socket(address,source,interface,tcp) as s:
        try:
            if tcp:s.connect((address,port))
            else:s.sendto(question(),(address,port))
        except OSError as exc:
            code=getattr(exc,'winerror',None)
            if code==10013:return True
            raise DNSVerificationError(f'Native DNS probe has no explicit block: IPv{ipaddress.ip_address(address).version}, '
                f'{"TCP" if tcp else "UDP"}, port={port}, bound={interface is not None}, winerror={code}.') from None
        raise DNSVerificationError(f'Native DNS send accepted: IPv{ipaddress.ip_address(address).version}, '
            f'{"TCP" if tcp else "UDP"}, port={port}, bound={interface is not None}.')


def inventory(tunnel_name, dns, run):
    if not re.fullmatch(r'AkenoVPN[0-9a-f]{24}',tunnel_name or ''):
        raise DNSVerificationError('Invalid native VPN interface.')
    targets=[str(ipaddress.ip_address(value)) for value in dns]
    if not 1<=len(targets)<=4 or len(set(targets))!=len(targets):
        raise DNSVerificationError('Invalid tunnel DNS configuration.')
    # Only validated fixed tunnel name and literal IPs enter the fixed script.
    script=f"$n='{tunnel_name}'; $adapter=Get-NetAdapter -Name $n -ErrorAction Stop; "
    script+="$d=@(Get-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ErrorAction Stop | ForEach-Object {$_.ServerAddresses}); "
    script+="$r=@(); "
    for address in targets:
        script+=f"$r+=@(Find-NetRoute -RemoteIPAddress '{address}' -ErrorAction Stop | Select-Object -ExpandProperty InterfaceAlias); "
    script+="$a=@(Get-NetIPAddress -AddressState Preferred -ErrorAction Stop | Select-Object InterfaceIndex,IPAddress,InterfaceAlias); "
    script+="@{index=[int]$adapter.ifIndex;dns=$d;routes=$r;addresses=$a} | ConvertTo-Json -Depth 4 -Compress"
    try:
        raw=json.loads(run(script,timeout=10))
        if type(raw['index']) is not int or raw['index']<=0 or set(raw['dns'])!=set(targets) or \
                not raw['routes'] or any(alias!=tunnel_name for alias in raw['routes']) or \
                not isinstance(raw['addresses'],list) or len(raw['addresses'])>256:
            raise ValueError()
        sources=set()
        for row in raw['addresses']:
            address=ipaddress.ip_address(row['IPAddress'])
            index=row['InterfaceIndex']
            if type(index) is not int or index<=0:raise ValueError()
            if index!=raw['index'] and not(address.is_loopback or address.is_link_local or address.is_unspecified
                                            or address.is_multicast):
                sources.add((index,str(address)))
        if not sources or len(sources)>32:raise ValueError()
        return {'index':raw['index'],'dns':sorted(targets),'sources':sorted(sources)}
    except (ValueError,TypeError,KeyError):
        raise DNSVerificationError('Native DNS routes, resolver or interface inventory is incomplete.') from None


def verify(tunnel_name,dns,*,run=None,resolve=resolver_responds,deny=blocked):
    if sys.platform!='win32':
        raise DNSVerificationError('DNS leak verification requires native Windows.')
    if run is None:
        from akeno.network.lan_windows import powershell
        run=powershell
    before=inventory(tunnel_name,dns,run)
    if not all(resolve(address) is True for address in before['dns']):
        raise DNSVerificationError('The configured tunnel DNS did not return a valid measured response.')
    candidates={4:('1.1.1.1','8.8.8.8','9.9.9.9'),
                6:('2606:4700:4700::1111','2001:4860:4860::8888','2620:fe::fe')}
    probes=0
    for family,choices in candidates.items():
        foreign=next(value for value in choices if value not in before['dns'])
        # Default-route probes check both stacks even if a physical NIC has no
        # global IPv6 address. NIC-bound probes catch Windows multi-homed DNS leaks.
        sources=[(None,None),*((index,address) for index,address in before['sources']
                              if ipaddress.ip_address(address).version==family)]
        for index,address in sources:
            # Encrypted DNS may legitimately use the full tunnel. Port 853 is
            # required to be denied only on a physical/non-tunnel source path.
            for tcp,port in ((False,53),(True,53),*(([(True,853)]) if index is not None else [])):
                if deny(foreign,source=address,interface=index,tcp=tcp,port=port) is not True:
                    raise DNSVerificationError('Windows did not explicitly deny a foreign DNS path.')
                probes+=1
    if inventory(tunnel_name,dns,run)!=before:
        raise DNSVerificationError('Network configuration changed during DNS verification.')
    return {'dns_verified':True,'resolver_responses':len(before['dns']),'denied_paths':probes,
            'source':'windows_wsaeacces_and_valid_tunnel_dns_response'}
