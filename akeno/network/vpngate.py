"""Experimental official VPN Gate CSV catalog; connections remain disabled pending terms and transport review."""
from __future__ import annotations
import base64,csv,io,ipaddress,re,urllib.request
from dataclasses import dataclass,field

URL='https://www.vpngate.net/api/iphone/'
HEADER=['HostName','IP','Score','Ping','Speed','CountryLong','CountryShort','NumVpnSessions','Uptime',
        'TotalUsers','TotalTraffic','LogType','Operator','Message','OpenVPN_ConfigData_Base64']
MAX_BYTES=4*1024*1024


@dataclass(frozen=True)
class Server:
    host:str
    ip:str
    country:str
    country_name:str
    ping_ms:int|None
    speed_bps:int
    sessions:int
    logging:str
    profile:str=field(repr=False)
    connectable:bool=False


def parse_catalog(raw:bytes)->list[Server]:
    if len(raw)>MAX_BYTES:raise ValueError('VPN Gate catalog is too large.')
    text=raw.decode('utf-8-sig')
    rows=list(csv.reader(io.StringIO(text)))
    if not rows or rows[0]!=['*vpn_servers']:raise ValueError('Invalid VPN Gate catalog marker.')
    if len(rows)<2 or [v.lstrip('#') for v in rows[1]]!=HEADER:raise ValueError('Unsupported VPN Gate catalog schema.')
    if len(rows)>1003:raise ValueError('VPN Gate catalog has too many servers.')
    result=[];seen=set()
    for row in rows[2:]:
        if row==['*'] or not row:continue
        try:
            if len(row)!=len(HEADER):raise ValueError()
            p=dict(zip(HEADER,row));addr=ipaddress.ip_address(p['IP'])
            if not addr.is_global or not re.fullmatch('[A-Z]{2}',p['CountryShort']):raise ValueError()
            if len(p['HostName'])>253 or len(p['CountryLong'])>80 or len(p['LogType'])>200:raise ValueError()
            ping=None if p['Ping']=='-' else int(p['Ping'])
            speed,sessions=int(p['Speed']),int(p['NumVpnSessions'])
            if not 0<=speed<2**63 or not 0<=sessions<1000000 or ping is not None and not 0<=ping<600000:raise ValueError()
            blob=base64.b64decode(p['OpenVPN_ConfigData_Base64'],validate=True)
            if len(blob)>65536:raise ValueError()
            profile=blob.decode('utf-8')
            # Never execute profiles. Reject obvious executable/profile-file directives even for preview.
            forbidden={'up','down','route-up','route-pre-down','ipchange','plugin','script-security','config',
                       'management','client-connect','client-disconnect','learn-address','tls-verify','auth-user-pass-verify'}
            for line in profile.splitlines():
                stripped=line.strip()
                if stripped and not stripped.startswith(('#',';','<')) and stripped.split()[0].lstrip('-').lower() in forbidden:
                    raise ValueError()
            if '\0' in profile or not any(x.strip()=='client' for x in profile.splitlines()):raise ValueError()
            identity=(str(addr),p['HostName'])
            if identity in seen:continue
            seen.add(identity)
            result.append(Server(p['HostName'],str(addr),p['CountryShort'],p['CountryLong'],ping,speed,sessions,p['LogType'],profile))
        except (ValueError,UnicodeError,KeyError):
            continue
    return result


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def fetch_catalog():
    opener=urllib.request.build_opener(NoRedirects())
    with opener.open(urllib.request.Request(URL,headers={'User-Agent':'AKENO-experimental-catalog/1'}),timeout=15) as response:
        raw=response.read(MAX_BYTES+1)
    return parse_catalog(raw)


def transport(server):
    """(protocol, host, port) of a catalog profile's first remote, or (None, None, None)."""
    proto,remote=None,None
    for line in server.profile.splitlines():
        parts=line.strip().split()
        if len(parts)>1 and parts[0]=='proto':proto=parts[1].lower()
        elif len(parts)>2 and parts[0]=='remote' and remote is None and parts[2].isdigit():remote=(parts[1],int(parts[2]))
    return (proto,)+(remote or (None,None))


def reachable(server,timeout=3.0):
    """Whether this PC reaches the server's OpenVPN TCP port: 'reachable', 'unreachable' or 'unknown' (UDP servers
    are not probed). A plain TCP connection, closed at once: no VPN protocol, no data, no connection to the VPN."""
    import socket
    proto,host,port=transport(server)
    if not proto or not proto.startswith('tcp') or not port:return 'unknown'
    try:
        if ipaddress.ip_address(host)!=ipaddress.ip_address(server.ip):return 'unknown'
    except ValueError:
        return 'unknown'
    try:
        with socket.create_connection((server.ip,port),timeout=timeout):return 'reachable'
    except OSError:
        return 'unreachable'


def countries(servers):
    return sorted({(s.country,s.country_name) for s in servers})


def connect(*args,**kwargs):
    raise RuntimeError('VPN Gate connections are disabled: terms, operator privacy and OpenVPN transport review are incomplete.')
