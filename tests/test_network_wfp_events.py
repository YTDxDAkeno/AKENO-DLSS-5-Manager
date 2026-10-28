"""WFP net-event ABI layout and tuple matching; no native Windows evidence."""
import ctypes
import ipaddress

import pytest

from akeno.network import wfp_events as w

pytestmark = pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason='x64 ABI layout')


def test_structures_match_the_documented_x64_layout():
    offsets = {name: getattr(w.HEADER, name).offset for name, _ in w.HEADER._fields_}
    assert offsets == {'timeStamp': 0, 'flags': 8, 'ipVersion': 12, 'ipProtocol': 16, 'localAddr': 20,
                       'remoteAddr': 36, 'localPort': 52, 'remotePort': 54, 'scopeId': 56, 'appId': 64,
                       'userId': 80}
    assert ctypes.sizeof(w.HEADER) == 88
    assert w.EVENT.type.offset == 88 and w.EVENT.classifyDrop.offset == 96 and ctypes.sizeof(w.EVENT) == 104
    assert ctypes.sizeof(w.TEMPLATE) == 32 and w.TEMPLATE.filterCondition.offset == 24
    assert ctypes.sizeof(w.CLASSIFY_DROP) == 16


def header(remote='8.8.8.8', rport=53, lport=43002, local='192.168.1.2', protocol=17, flags=None, version=0):
    h = w.HEADER()
    h.flags = flags if flags is not None else (w.FLAG_IP_PROTOCOL | w.FLAG_LOCAL_ADDR | w.FLAG_REMOTE_ADDR |
                                               w.FLAG_LOCAL_PORT | w.FLAG_REMOTE_PORT | w.FLAG_IP_VERSION)
    h.ipVersion, h.ipProtocol, h.localPort, h.remotePort = version, protocol, lport, rport
    if version == 0:
        h.remoteAddr.v4 = int(ipaddress.IPv4Address(remote))
        h.localAddr.v4 = int(ipaddress.IPv4Address(local))
    else:
        h.remoteAddr.v6[:] = list(ipaddress.IPv6Address(remote).packed)
        h.localAddr.v6[:] = list(ipaddress.IPv6Address(local).packed)
    return h


FLOW = ('192.168.1.2', 43002, '8.8.8.8', 53, 17)


def test_only_the_exact_probe_tuple_matches():
    assert w.matches(header(), FLOW)
    for changed in (header(remote='1.1.1.1'), header(rport=853), header(lport=1), header(protocol=6),
                    header(local='192.168.1.3'), header(flags=w.FLAG_IP_PROTOCOL | w.FLAG_REMOTE_ADDR)):
        assert not w.matches(changed, FLOW)
    assert w.matches(header(local='10.0.0.1'), ('0.0.0.0', 43002, '8.8.8.8', 53, 17))


def test_ipv6_tuples_match_by_packed_address():
    flow = ('2001:db8::2', 40000, '2606:4700:4700::1111', 53, 17)
    assert w.matches(header('2606:4700:4700::1111', 53, 40000, '2001:db8::2', version=1), flow)
    assert not w.matches(header('2606:4700:4700::1112', 53, 40000, '2001:db8::2', version=1), flow)


def test_filetime_clock_and_round_trip():
    stamp = w.FILETIME.of(0x01DA_1234_5678_9ABC)
    assert stamp.value == 0x01DA_1234_5678_9ABC
    assert w.now() > 130_000_000_000_000_000          # after 2012 in FILETIME units


def test_drop_log_retries_until_every_flow_is_proven():
    log = w.DropLog()
    seen = iter([[], [(header(), 7)]])
    log.drops = lambda start, end: next(seen)
    sleeps = []
    assert log.proves([FLOW], 1, wait=5, sleep=sleeps.append) == {FLOW} and sleeps == [0.5]
