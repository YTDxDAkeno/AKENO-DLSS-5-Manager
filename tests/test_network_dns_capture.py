"""Packet evidence parser/positive controls; synthetic fixtures are not native Windows evidence."""
import ipaddress
import struct

import pytest

from akeno.network.dns_capture import packets,DNSCapture,CaptureError
from akeno.network.dns_probe import question


def block(kind,body):
    body+=b'\0'*(-len(body)%4);size=12+len(body)
    return struct.pack('<II',kind,size)+body+struct.pack('<I',size)


def ipv4(flow,payload):
    source,sport,target,dport,protocol=flow
    ip=bytearray(20);ip[0]=0x45;ip[8]=64;ip[9]=protocol
    struct.pack_into('!H',ip,2,20+8+len(payload))
    ip[12:16]=ipaddress.ip_address(source).packed;ip[16:20]=ipaddress.ip_address(target).packed
    total=sum(struct.unpack('!10H',bytes(ip)))
    while total>>16:total=(total&0xffff)+(total>>16)
    struct.pack_into('!H',ip,10,~total&0xffff)
    return bytes(ip)+struct.pack('!HHHH',sport,dport,len(payload)+8,0)+payload


def capture(flow,payload,framing=b'\0'*12+b'\x08\x00'):
    packet=framing+ipv4(flow,payload)
    return (block(0x0a0d0d0a,struct.pack('<IHHq',0x1a2b3c4d,1,0,-1))+
            block(1,struct.pack('<HHI',1,0,65535))+
            block(6,struct.pack('<IIIII',0,0,0,len(packet),len(packet))+packet))


def test_private_capture_parser_extracts_exact_probe_tuple_and_payload():
    flow=('192.168.1.2',43000,'8.8.8.8',53,17);query=question()
    raw=capture(flow,query)
    assert list(packets(raw))==[(flow,query)]
    for data in (b'',raw[:-1],raw[:32]+b'bad'):
        with pytest.raises(CaptureError):list(packets(data))


def test_capture_stop_preserves_foreign_collector_and_requires_own_private_file(tmp_path):
    monitor=DNSCapture.__new__(DNSCapture)
    monitor.started=True;monitor.etl=tmp_path/'random-private.etl'
    calls=[]
    def call(*args):
        calls.append(args)
        return 'Log file: another-collector.etl'
    monitor._call=call
    with pytest.raises(CaptureError,match='ownership'):
        monitor._stop()
    assert calls==[('status',)] and monitor.started
    monitor._call=lambda *args:calls.append(args) or str(monitor.etl)
    monitor._stop()
    assert calls[-1]==('stop',) and not monitor.started


@pytest.mark.parametrize('missing',['none','drop','positive'])
def test_async_success_requires_actual_drop_and_captured_positive_reply(tmp_path,missing):
    query=question();reply=query[:2]+b'\x81\x83'+query[4:]
    positive=('1.1.1.1',53,'10.204.1.2',43001,17)
    denied=('192.168.1.2',43002,'8.8.8.8',53,17)
    monitor=DNSCapture.__new__(DNSCapture)
    monitor.directory=tmp_path;monitor.etl=tmp_path/'capture.etl';monitor._stop=lambda:None
    monitor._call=lambda *args:None
    monitor.positive=[(query,('10.204.1.2',43001),('1.1.1.1',53))];monitor.pending=[denied]
    (tmp_path/'all.pcapng').write_bytes(capture(positive,reply if missing!='positive' else query))
    (tmp_path/'drops.pcapng').write_bytes(capture(denied if missing!='drop' else positive,query))
    if missing=='none':assert monitor.finish()==1
    else:
        with pytest.raises(CaptureError):monitor.finish()


def test_bare_ip_records_of_layer3_adapters_are_parsed_only_with_a_valid_ip_header():
    """Packet Monitor stores WireGuardNT packets without an Ethernet header under an Ethernet link type."""
    flow=('10.203.78.1',53,'10.203.78.2',43001,17);query=question()
    stats={}
    assert list(packets(capture(flow,query,framing=b''),stats))==[(flow,query)] and stats['bare_ip']==1
    damaged=bytearray(ipv4(flow,query));damaged[11]^=0xff                       # wrong IPv4 header checksum
    raw=capture(flow,query,framing=b'')
    raw=raw.replace(ipv4(flow,query),bytes(damaged))
    stats={}
    assert list(packets(raw,stats))==[] and 'bare_ip' not in stats
    stats={}
    assert list(packets(capture(flow,query,framing=b'\0'*12+b'\x81\x00'),stats))==[]
    assert stats['ethertypes']==['8100']
