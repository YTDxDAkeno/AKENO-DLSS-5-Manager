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


def packets(raw):
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
        if kind!=6:continue
        if len(block)<20:raise CaptureError('Invalid packet record.')
        iface,_,_,captured,original=struct.unpack_from(endian+'IIIII',block)
        if iface>=len(interfaces) or captured>len(block)-20 or captured>original:raise CaptureError('Invalid packet length.')
        packet=block[20:20+captured];link=interfaces[iface]
        if link==1:
            if len(packet)<14:continue
            protocol=packet[12:14];packet=packet[14:]
            if protocol not in (b'\x08\x00',b'\x86\xdd'):continue
        elif link not in (101,228,229):continue
        if not packet:continue
        version=packet[0]>>4
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
        observed=list(packets(all_file.read_bytes()));dropped={flow for flow,_ in packets(drops_file.read_bytes())}
        if not self.positive:raise CaptureError('DNS capture lacks a positive control.')
        for query,source,target in self.positive:
            flow=(target[0],target[1],source[0],source[1],17)
            from akeno.network.dns_probe import valid_reply
            if not any(item==flow and valid_reply(query,payload) for item,payload in observed):
                raise CaptureError('The actual positive DNS reply was not captured.')
        if any(flow not in dropped for flow in self.pending):
            raise CaptureError('No kernel drop record proves an asynchronous DNS denial.')
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
