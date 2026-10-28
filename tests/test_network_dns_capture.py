"""Packet evidence parser, drop evidence and capture reporting; synthetic fixtures are not native Windows evidence."""
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
def test_async_success_requires_an_actual_drop_record_and_reports_the_capture(tmp_path,missing):
    """The drop record is the evidence; an unrecorded positive control is reported, never a pass or a fail by itself
    (Packet Monitor does not reliably record WireGuardNT packets)."""
    query=question();reply=query[:2]+b'\x81\x83'+query[4:]
    positive=('1.1.1.1',53,'10.204.1.2',43001,17)
    denied=('192.168.1.2',43002,'8.8.8.8',53,17)
    monitor=DNSCapture.__new__(DNSCapture)
    monitor.directory=tmp_path;monitor.etl=tmp_path/'capture.etl';monitor._stop=lambda:None
    monitor._call=lambda *args:None
    monitor.positive=[(query,('10.204.1.2',43001),('1.1.1.1',53))];monitor.pending=[denied]
    (tmp_path/'all.pcapng').write_bytes(capture(positive,reply if missing!='positive' else query))
    (tmp_path/'drops.pcapng').write_bytes(capture(denied if missing!='drop' else positive,query))
    if missing=='drop':
        with pytest.raises(CaptureError):monitor.finish()
    else:assert monitor.finish()==1 and monitor.recorded is (missing=='none')


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


def test_reply_to_a_socket_with_unspecified_local_address_is_bound_by_ports_and_transaction(tmp_path):
    query=question();reply=query[:2]+b'\x81\x80'+query[4:]
    monitor=DNSCapture.__new__(DNSCapture)
    monitor.directory=tmp_path;monitor.etl=tmp_path/'capture.etl';monitor._stop=lambda:None
    monitor._call=lambda *args:None;monitor.pending=[]
    monitor.positive=[(query,('0.0.0.0',43001),('10.203.78.1',53))]
    (tmp_path/'drops.pcapng').write_bytes(capture(('10.0.0.9',1,'10.0.0.8',2,17),b'x'*12))
    for flow,payload,ok in [(('10.203.78.1',53,'10.203.78.2',43001,17),reply,True),
                            (('10.203.78.1',53,'10.203.78.2',43002,17),reply,False),
                            (('10.203.78.9',53,'10.203.78.2',43001,17),reply,False),
                            (('10.203.78.1',53,'10.203.78.2',43001,17),query,False)]:
        (tmp_path/'all.pcapng').write_bytes(capture(flow,payload,framing=b''))
        assert monitor.finish()==0 and monitor.recorded is ok
        assert (monitor.shape=={})==ok


class DropLog:
    def __init__(self, proven):
        self.proven, self.calls = set(proven), []

    def proves(self, flows, start):
        self.calls.append((list(flows), start))
        return {flow for flow in flows if flow in self.proven}


def monitor_with(tmp_path, observed_flow, drop_log):
    query=question();reply=query[:2]+b'\x81\x83'+query[4:]
    positive=('1.1.1.1',53,'10.204.1.2',43001,17)
    denied=('192.168.1.2',43002,'8.8.8.8',53,17)
    monitor=DNSCapture.__new__(DNSCapture)
    monitor.directory=tmp_path;monitor.etl=tmp_path/'capture.etl';monitor._stop=lambda:None
    monitor._call=lambda *args:None;monitor.started_at=1234;monitor.drop_log=drop_log
    monitor.positive=[(query,('10.204.1.2',43001),('1.1.1.1',53))];monitor.pending=[denied]
    raw=capture(positive,reply)
    if observed_flow:raw+=capture(denied,query)[len(block(0x0a0d0d0a,struct.pack('<IHHq',0x1a2b3c4d,1,0,-1))):]
    (tmp_path/'all.pcapng').write_bytes(raw)
    (tmp_path/'drops.pcapng').write_bytes(capture(('10.0.0.9',1,'10.0.0.8',2,17),b'x'*12))
    return monitor,denied


def test_wfp_classify_drop_event_proves_a_silently_denied_probe(tmp_path):
    log=DropLog([('192.168.1.2',43002,'8.8.8.8',53,17)])
    monitor,denied=monitor_with(tmp_path,False,log)
    assert monitor.finish()==1 and log.calls==[([denied],1234)]


def test_without_any_drop_record_the_denial_stays_unproven(tmp_path):
    monitor,_=monitor_with(tmp_path,False,DropLog([]))
    with pytest.raises(CaptureError,match="wfp_drop_events': 0"):monitor.finish()


def test_probe_seen_on_a_link_is_a_leak_even_if_a_drop_event_exists(tmp_path):
    log=DropLog([('192.168.1.2',43002,'8.8.8.8',53,17)])
    monitor,_=monitor_with(tmp_path,True,log)
    with pytest.raises(CaptureError,match='observed on a network link'):monitor.finish()
    assert log.calls==[]


def test_unavailable_drop_events_fail_closed_as_capture_errors(tmp_path):
    from akeno.network.wfp_events import EventsUnavailable
    class Off:
        def proves(self,flows,start):raise EventsUnavailable('Windows does not record firewall drop events.')
    monitor,_=monitor_with(tmp_path,False,Off())
    with pytest.raises(CaptureError,match='does not record'):monitor.finish()


def positive_monitor(tmp_path,record):
    query=question()
    monitor=DNSCapture.__new__(DNSCapture)
    monitor.directory=tmp_path;monitor.etl=tmp_path/'capture.etl';monitor._stop=lambda:None
    monitor._call=lambda *args:None;monitor.pending=[]
    monitor.positive=[(query,('10.203.78.2',43001),('10.203.78.1',53))]
    (tmp_path/'drops.pcapng').write_bytes(capture(('10.0.0.9',1,'10.0.0.8',2,17),b'x'*12))
    (tmp_path/'all.pcapng').write_bytes(record(query))
    return monitor


def test_query_recorded_on_the_tunnel_adapter_counts_as_recorded(tmp_path):
    flow=('10.203.78.2',43001,'10.203.78.1',53,17)
    monitor=positive_monitor(tmp_path,lambda q:capture(flow,q,framing=b''))
    assert monitor.finish()==0 and monitor.recorded is True


def test_query_seen_on_an_ethernet_link_does_not_count_as_recorded(tmp_path):
    flow=('10.203.78.2',43001,'10.203.78.1',53,17)
    monitor=positive_monitor(tmp_path,lambda q:capture(flow,q))
    assert monitor.finish()==0 and monitor.recorded is False
    assert monitor.shape['to_resolver']==1 and monitor.shape['bare_ip']==0


def test_a_different_query_on_the_tunnel_does_not_count_as_recorded(tmp_path):
    flow=('10.203.78.2',43001,'10.203.78.1',53,17)
    monitor=positive_monitor(tmp_path,lambda q:capture(flow,question(),framing=b''))
    assert monitor.finish()==0 and monitor.recorded is False and monitor.shape['probe_ids']==0


def test_unrecorded_capture_never_replaces_drop_evidence(tmp_path):
    """Nothing seen in the capture and no drop record: the denial stays unproven (fails closed)."""
    monitor,_=monitor_with(tmp_path,False,DropLog([]))
    (tmp_path/'all.pcapng').write_bytes(capture(('10.0.0.9',1,'10.0.0.8',2,17),b'x'*12))
    with pytest.raises(CaptureError,match='No kernel drop record'):monitor.finish()
