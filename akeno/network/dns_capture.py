"""Bounded, private Packet Monitor evidence for asynchronous Windows packet drops.

Never takes over an existing capture/filter set. Raw DNS captures are accessible only
to Administrators/SYSTEM, never exported, and removed on normal cleanup. The observer
requires a captured positive control and actual kernel drop records for pending probes.
"""
from __future__ import annotations

import ipaddress
import re
import shutil
import struct
import subprocess
import uuid


class CaptureError(ValueError):
    pass


def _bare_ip(packet):
    """True for a complete IPv4/IPv6 UDP/TCP packet without a link header.

    Packet Monitor records packets of layer-3 adapters (WireGuardNT has no Ethernet framing) in an interface block
    whose link type says Ethernet. Accept such a record only when its own IP header proves it: the length field
    equals the captured length and, for IPv4, the header checksum is valid.
    """
    if len(packet)>=20 and packet[0]>>4==4:
        ihl=(packet[0]&15)*4
        if ihl<20 or len(packet)<ihl or struct.unpack_from('!H',packet,2)[0]!=len(packet) or packet[9] not in (6,17):
            return False
        total=sum(struct.unpack_from('!%dH'%(ihl//2),packet))
        while total>>16:total=(total&0xffff)+(total>>16)
        return total==0xffff
    if len(packet)>=40 and packet[0]>>4==6:
        return struct.unpack_from('!H',packet,4)[0]+40==len(packet) and packet[6] in (6,17)
    return False


def packets(raw, stats=None):
    if len(raw)>16*1024*1024:raise CaptureError('DNS capture exceeded its bound.')
    if raw[:4]!=b'\x0a\x0d\x0d\x0a':raise CaptureError('Missing capture section header.')
    offset,endian,interfaces=0,'<',[]
    while offset<len(raw):
        if offset+12>len(raw):raise CaptureError('Truncated packet evidence.')
        if raw[offset:offset+4]==b'\x0a\x0d\x0d\x0a':
            magic=raw[offset+8:offset+12]
            if magic not in (b'\x4d\x3c\x2b\x1a',b'\x1a\x2b\x3c\x4d'):raise CaptureError('Invalid capture format.')
            endian='<' if magic==b'\x4d\x3c\x2b\x1a' else '>';interfaces=[]
        kind,size=struct.unpack_from(endian+'II',raw,offset)
        if size<12 or size%4 or offset+size>len(raw) or struct.unpack_from(endian+'I',raw,offset+size-4)[0]!=size:
            raise CaptureError('Invalid packet evidence block.')
        block=raw[offset+8:offset+size-4];offset+=size
        if kind==1:
            if len(block)<8:raise CaptureError('Invalid capture interface.')
            interfaces.append(struct.unpack_from(endian+'H',block)[0])
            if stats is not None:stats['links']=sorted(set(interfaces))
        if kind!=6:continue
        if stats is not None:stats['records']=stats.get('records',0)+1
        if len(block)<20:raise CaptureError('Invalid packet record.')
        iface,_,_,captured,original=struct.unpack_from(endian+'IIIII',block)
        if iface>=len(interfaces) or captured>len(block)-20 or captured>original:raise CaptureError('Invalid packet length.')
        packet=block[20:20+captured];link=interfaces[iface]
        if link==1:
            if len(packet)>=14 and packet[12:14] in (b'\x08\x00',b'\x86\xdd'):
                packet=packet[14:]
            elif _bare_ip(packet):
                if stats is not None:stats['bare_ip']=stats.get('bare_ip',0)+1
            else:
                # Metadata only (EtherType values), never addresses or payload.
                if stats is not None and len(packet)>=14:
                    stats['ethertypes']=sorted(set(stats.get('ethertypes',[])+[packet[12:14].hex()]))[:16]
                continue
        elif link not in (101,228,229):continue
        if not packet:continue
        version=packet[0]>>4
        if stats is not None:stats['versions']=sorted(set(stats.get('versions',[])+[version]))
        if version==4 and len(packet)>=20:
            ihl=(packet[0]&15)*4
            if ihl<20 or len(packet)<ihl:continue
            proto=packet[9];source=packet[12:16];target=packet[16:20];body=packet[ihl:]
        elif version==6 and len(packet)>=40:
            proto=packet[6];source=packet[8:24];target=packet[24:40];body=packet[40:]
        else:continue
        if proto not in (6,17) or len(body)<8:continue
        sport,dport=struct.unpack_from('!HH',body)
        payload=body[8:] if proto==17 else body[(body[12]>>4)*4:] if len(body)>=20 else b''
        yield (str(ipaddress.ip_address(source)),sport,str(ipaddress.ip_address(target)),dport,proto),payload


class DNSCapture:
    def __init__(self):
        from akeno.network.windows import WindowsWireGuard
        from akeno.network.bootstrap import _protected_directory
        from akeno.network.lan_windows import _system_tool
        self.exe=_system_tool('pktmon.exe')
        self.directory=WindowsWireGuard().root.parent/('.AkenoDNS-'+uuid.uuid4().hex)
        _protected_directory(self.directory)
        self.etl=self.directory/'measurement.etl'
        self.filters=[];self.started=False;self.pending=[];self.positive=[]

    def _call(self,*arguments):
        result=subprocess.run([self.exe,*map(str,arguments)],capture_output=True,text=True,
                              timeout=10,creationflags=0x08000000)
        if result.returncode:raise CaptureError('Native DNS packet measurement is unavailable.')
        if len(result.stdout)>512*1024:raise CaptureError('Native capture status exceeded its bound.')
        return result.stdout

    def start(self):
        # Localized/unrecognized states fail closed; do not stop or reset somebody else's collector.
        status=self._call('status').strip().casefold()
        if not ('not running' in status or status=='stopped'):
            raise CaptureError('Another or unrecognized Packet Monitor session is active.')
        filters=self._call('filter','list').strip().casefold()
        if not re.fullmatch(r'(?:packet filters:\s*)?(?:none|no filters(?: defined)?\.?|there are no filters\.?)',filters):
            raise CaptureError('Existing Packet Monitor filters must be preserved.')
        for port in (53,853):
            name='AKENO-DNS-'+uuid.uuid4().hex
            self._call('filter','add',name,'-p',str(port));self.filters.append(name)
        self._call('start','--capture','--comp','all','--pkt-size','0','--file-name',self.etl,
                   '--file-size','4','--log-mode','circular')
        self.started=True

    def expect_reply(self,query,source,target):
        self.positive.append((query,source,target))

    def defer(self,source,target,tcp):
        self.pending.append((source[0],source[1],target[0],target[1],6 if tcp else 17))
        return 'pending'

    def finish(self):
        self._stop()
        all_file=self.directory/'all.pcapng';drops_file=self.directory/'drops.pcapng'
        self._call('etl2pcap',self.etl,'--out',all_file)
        self._call('etl2pcap',self.etl,'--drop-only','--out',drops_file)
        stats={}
        drop_stats={}
        observed=list(packets(all_file.read_bytes(),stats))
        dropped_flows=[flow for flow,_ in packets(drops_file.read_bytes(),drop_stats)];dropped=set(dropped_flows)
        if not self.positive:raise CaptureError('DNS capture lacks a positive control.')
        from akeno.network.dns_probe import valid_reply
        for query,source,target in self.positive:
            # A connected UDP socket may report an unspecified local address on Windows; the reply is then bound
            # by the resolver address, both ports and the random transaction id/question it must echo.
            any_local=ipaddress.ip_address(source[0]).is_unspecified
            def matches(item):
                return (item[0],item[1],item[3],item[4])==(target[0],target[1],source[1],17) and \
                    (any_local or item[2]==source[0])
            if not any(matches(item) and valid_reply(query,payload) for item,payload in observed):
                # Structural metadata only: no addresses, names or payload bytes leave this function.
                shape={'from_resolver':sum(1 for item,_ in observed if item[:2]==(target[0],target[1])),
                       'to_probe_port':sum(1 for item,_ in observed if item[3]==source[1]),
                       'valid_reply_payload':sum(1 for _,payload in observed if valid_reply(query,payload)),
                       'query_payload':sum(1 for _,payload in observed if payload[:2]==query[:2]),
                       'unspecified_local':any_local}
                raise CaptureError(f'The actual positive DNS reply was not captured (capture metadata={stats}; '
                                   f'parsed={len(observed)}; shape={shape}).')
        missing=[flow for flow in self.pending if flow not in dropped]
        if missing:
            # Structural counts only. 'seen_in_capture' > 0 would mean a denied probe was observed on a link: a leak.
            shape={'pending':len(self.pending),'missing':len(missing),'drop_records':drop_stats.get('records',0),
                   'drop_parsed':len(dropped_flows),'drop_links':drop_stats.get('links',[]),
                   'same_destination':sum(1 for f in missing if any(d[2:]==f[2:] for d in dropped)),
                   'same_source_address':sum(1 for f in missing if any(d[0]==f[0] for d in dropped)),
                   'protocols':sorted({f[4] for f in missing}),
                   'seen_in_capture':sum(1 for f in missing if any(item==f for item,_ in observed))}
            raise CaptureError(f'No kernel drop record proves an asynchronous DNS denial (shape={shape}).')
        return len(self.pending)

    def _stop(self):
        if self.started:
            # Refuse to stop a replacement capture after an external collector intervened.
            status=self._call('status')
            if str(self.etl).casefold() not in status.casefold():
                raise CaptureError('DNS capture ownership cannot be established.')
            self._call('stop');self.started=False

    def close(self):
        try:self._stop()
        finally:
            for name in self.filters:self._call('filter','remove',name)
            if not self.started:shutil.rmtree(self.directory)
