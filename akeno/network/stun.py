"""Bounded RFC 5389 binding discovery for explicitly configured AKENO coordinators.

No public third-party STUN server is hardcoded or contacted automatically. A discovered mapping is not a
successful WireGuard/P2P connection; socket/port ownership, NAT changes and endpoint-proof remain separate.
"""
import ipaddress,os,socket,struct,zlib
MAGIC=0x2112A442


def request():
    tx=os.urandom(12)
    return struct.pack('!HHI',1,0,MAGIC)+tx,tx


def parse_response(data,transaction):
    if len(transaction)!=12 or not 20<=len(data)<=1024:raise ValueError('Invalid STUN response.')
    kind,size,cookie=struct.unpack('!HHI',data[:8])
    if kind!=0x0101 or cookie!=MAGIC or data[8:20]!=transaction or size!=len(data)-20 or size%4:
        raise ValueError('Unmatched STUN response.')
    offset=20;result=None
    while offset<len(data):
        if offset+4>len(data):raise ValueError('Truncated STUN attribute.')
        attr,length=struct.unpack('!HH',data[offset:offset+4]);start=offset
        offset+=4;end=offset+length
        if end>len(data):raise ValueError('Truncated STUN attribute.')
        value=data[offset:end];offset=end+(-length%4)
        if offset>len(data):raise ValueError('Truncated STUN padding.')
        if attr==0x0020:
            if result is not None or length not in (8,20) or value[0]!=0:raise ValueError('Invalid mapped address.')
            family=value[1];port=struct.unpack('!H',value[2:4])[0]^(MAGIC>>16)
            if (family,length) not in ((1,8),(2,20)) or not port:raise ValueError('Invalid mapped address.')
            mask=struct.pack('!I',MAGIC)+transaction
            address=bytes(a^b for a,b in zip(value[4:],mask))
            ip=ipaddress.ip_address(address)
            if ip.is_multicast or ip.is_unspecified:raise ValueError('Invalid mapped address.')
            result=(str(ip),port)
        elif attr==0x8028:
            if length!=4 or offset!=len(data) or struct.unpack('!I',value)[0]!=(zlib.crc32(data[:start])^0x5354554e):
                raise ValueError('Invalid STUN fingerprint.')
    if result is None:raise ValueError('Mapped address missing.')
    return result


def discover(host,port,timeout=2):
    if not isinstance(port,int) or not 1<=port<=65535 or not 0<timeout<=5:
        raise ValueError('Invalid coordinator settings.')
    packet,tx=request()
    # A connected UDP socket restricts replies to the selected coordinator; transaction IDs bind the response.
    family=socket.AF_INET6 if ':' in host else socket.AF_INET
    with socket.socket(family,socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout);sock.connect((host,port));sock.send(packet)
        return parse_response(sock.recv(1025),tx)
