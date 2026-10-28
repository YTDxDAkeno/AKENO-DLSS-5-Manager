"""Windows Filtering Platform drop records: the authoritative evidence that WFP itself discarded a packet.

Packet Monitor does not record classify drops of the WFP ALE layers (CI showed zero drop records for a UDP DNS probe
that the full-tunnel firewall silently discarded). The Base Filtering Engine keeps its own event log of classify drops
(the data behind ``netsh wfp show netevents``). This module reads it read-only for one exact probe tuple.

* Nothing is changed: if Windows does not collect network events, the check is unavailable and fails closed; AKENO
  never switches event collection on behind the user's back.
* Only the matching tuple leaves this module (as a boolean); other applications' events are read in memory and
  discarded. Runs only in the explicit elevated AKENO helper.
"""
from __future__ import annotations

import ctypes
import ipaddress
import sys
import time

from akeno.network.wfp import FILTER_CONDITION, RPC_C_AUTHN_WINNT, SESSION, DISPLAY_DATA, VALUE
from akeno.network.windows import NativeUnavailable

FWPM_NET_EVENT_TYPE_CLASSIFY_DROP = 3
FWPM_ENGINE_COLLECT_NET_EVENTS = 0
FLAG_IP_PROTOCOL, FLAG_LOCAL_ADDR, FLAG_REMOTE_ADDR, FLAG_LOCAL_PORT, FLAG_REMOTE_PORT = 0x1, 0x2, 0x4, 0x8, 0x10
FLAG_IP_VERSION = 0x100
FWP_IP_VERSION_V4, FWP_IP_VERSION_V6 = 0, 1


class EventsUnavailable(NativeUnavailable):
    pass


class FILETIME(ctypes.Structure):
    _fields_ = [('low', ctypes.c_uint32), ('high', ctypes.c_uint32)]

    @property
    def value(self) -> int:
        return self.high << 32 | self.low

    @classmethod
    def of(cls, value: int) -> 'FILETIME':
        return cls(value & 0xFFFFFFFF, value >> 32)


class _Address(ctypes.Union):
    _fields_ = [('v4', ctypes.c_uint32), ('v6', ctypes.c_ubyte * 16)]


class BYTE_BLOB(ctypes.Structure):
    _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.c_void_p)]


class HEADER(ctypes.Structure):
    """FWPM_NET_EVENT_HEADER0."""
    _fields_ = [('timeStamp', FILETIME), ('flags', ctypes.c_uint32), ('ipVersion', ctypes.c_uint32),
                ('ipProtocol', ctypes.c_uint8), ('localAddr', _Address), ('remoteAddr', _Address),
                ('localPort', ctypes.c_uint16), ('remotePort', ctypes.c_uint16), ('scopeId', ctypes.c_uint32),
                ('appId', BYTE_BLOB), ('userId', ctypes.c_void_p)]


class CLASSIFY_DROP(ctypes.Structure):
    """FWPM_NET_EVENT_CLASSIFY_DROP0."""
    _fields_ = [('filterId', ctypes.c_uint64), ('layerId', ctypes.c_uint16)]


class EVENT(ctypes.Structure):
    """FWPM_NET_EVENT0: the union of per-type pointers is read as the classify-drop pointer only for that type."""
    _fields_ = [('header', HEADER), ('type', ctypes.c_uint32), ('classifyDrop', ctypes.POINTER(CLASSIFY_DROP))]


class TEMPLATE(ctypes.Structure):
    """FWPM_NET_EVENT_ENUM_TEMPLATE0."""
    _fields_ = [('startTime', FILETIME), ('endTime', FILETIME), ('numFilterConditions', ctypes.c_uint32),
                ('filterCondition', ctypes.POINTER(FILTER_CONDITION))]


def now() -> int:
    """Current system time as a FILETIME integer (100 ns since 1601), the clock of WFP event time stamps."""
    if sys.platform != 'win32':
        return int((time.time() + 11644473600) * 10_000_000)
    stamp = FILETIME()
    ctypes.windll.kernel32.GetSystemTimeAsFileTime(ctypes.byref(stamp))
    return stamp.value


def _address(header: HEADER, which: str) -> str | None:
    value = getattr(header, which)
    if header.ipVersion == FWP_IP_VERSION_V4:
        return str(ipaddress.IPv4Address(value.v4))
    if header.ipVersion == FWP_IP_VERSION_V6:
        return str(ipaddress.IPv6Address(bytes(value.v6)))
    return None


def matches(header: HEADER, flow: tuple) -> bool:
    """``flow`` is (source address, source port, target address, target port, protocol) of an outbound probe."""
    source, sport, target, dport, protocol = flow
    needed = FLAG_IP_PROTOCOL | FLAG_REMOTE_ADDR | FLAG_LOCAL_PORT | FLAG_REMOTE_PORT | FLAG_IP_VERSION
    if header.flags & needed != needed or header.ipProtocol != protocol or header.localPort != sport or \
            header.remotePort != dport or _address(header, 'remoteAddr') != str(ipaddress.ip_address(target)):
        return False
    unspecified = ipaddress.ip_address(source).is_unspecified
    return unspecified or not header.flags & FLAG_LOCAL_ADDR or _address(header, 'localAddr') == source


class _Api:
    def __init__(self):
        if sys.platform != 'win32':
            raise EventsUnavailable('WFP drop records require Windows.')
        dll = ctypes.WinDLL('fwpuclnt.dll')
        handle = ctypes.c_void_p
        proto = {
            'FwpmEngineOpen0': [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(SESSION),
                                ctypes.POINTER(handle)],
            'FwpmEngineClose0': [handle],
            'FwpmEngineGetOption0': [handle, ctypes.c_uint32, ctypes.POINTER(ctypes.POINTER(VALUE))],
            'FwpmNetEventCreateEnumHandle0': [handle, ctypes.POINTER(TEMPLATE), ctypes.POINTER(handle)],
            'FwpmNetEventEnum0': [handle, handle, ctypes.c_uint32,
                                  ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(EVENT))), ctypes.POINTER(ctypes.c_uint32)],
            'FwpmNetEventDestroyEnumHandle0': [handle, handle],
            'FwpmFreeMemory0': [ctypes.POINTER(ctypes.c_void_p)],
        }
        for name, argtypes in proto.items():
            function = getattr(dll, name)
            function.argtypes = argtypes
            function.restype = None if name == 'FwpmFreeMemory0' else ctypes.c_uint32
            setattr(self, name, function)


class DropLog:
    """Read-only access to classify-drop events in a time window."""

    MAX_EVENTS = 20000

    def __init__(self, api=None):
        self._api = api

    @property
    def api(self):
        if self._api is None:
            self._api = _Api()
        return self._api

    def _free(self, pointer):
        self.api.FwpmFreeMemory0(ctypes.byref(ctypes.cast(pointer, ctypes.c_void_p)))

    def drops(self, start: int, end: int):
        """Yield (header, filter id) of every classify drop recorded between the FILETIMEs ``start`` and ``end``."""
        session = SESSION()
        session.displayData = DISPLAY_DATA('AKENO DNS verification (read-only)', None)
        engine = ctypes.c_void_p()
        code = self.api.FwpmEngineOpen0(None, RPC_C_AUTHN_WINNT, None, ctypes.byref(session), ctypes.byref(engine))
        if code:
            raise EventsUnavailable(f'Windows Filtering Platform is unavailable (error 0x{code:08x}).')
        try:
            option = ctypes.POINTER(VALUE)()
            code = self.api.FwpmEngineGetOption0(engine, FWPM_ENGINE_COLLECT_NET_EVENTS, ctypes.byref(option))
            if code:
                raise EventsUnavailable(f'Cannot read the WFP event setting (error 0x{code:08x}).')
            try:
                collecting = bool(option.contents.value.uint32)
            finally:
                self._free(option)
            if not collecting:
                raise EventsUnavailable('Windows does not record firewall drop events on this PC, so a silent DNS '
                                        'denial cannot be proven. AKENO does not change this setting.')
            template = TEMPLATE(FILETIME.of(start), FILETIME.of(end), 0, None)
            enum = ctypes.c_void_p()
            code = self.api.FwpmNetEventCreateEnumHandle0(engine, ctypes.byref(template), ctypes.byref(enum))
            if code:
                raise EventsUnavailable(f'Cannot read WFP drop events (error 0x{code:08x}).')
            try:
                seen = 0
                while seen < self.MAX_EVENTS:
                    entries = ctypes.POINTER(ctypes.POINTER(EVENT))()
                    count = ctypes.c_uint32()
                    code = self.api.FwpmNetEventEnum0(engine, enum, 512, ctypes.byref(entries), ctypes.byref(count))
                    if code:
                        raise EventsUnavailable(f'Cannot read WFP drop events (error 0x{code:08x}).')
                    try:
                        for index in range(count.value):
                            event = entries[index].contents
                            if event.type == FWPM_NET_EVENT_TYPE_CLASSIFY_DROP:
                                drop = event.classifyDrop
                                yield event.header, drop.contents.filterId if drop else None
                    finally:
                        if count.value:
                            self._free(entries)
                    seen += count.value
                    if count.value < 512:
                        return
            finally:
                self.api.FwpmNetEventDestroyEnumHandle0(engine, enum)
        finally:
            self.api.FwpmEngineClose0(engine)

    def proves(self, flows, start: int, wait: float = 3.0, sleep=time.sleep) -> set:
        """The subset of outbound ``flows`` for which WFP recorded a classify drop after ``start``.

        Events reach the log asynchronously; the log is read again for up to ``wait`` seconds.
        """
        flows = list(flows)
        found: set = set()
        deadline = time.monotonic() + wait
        while True:
            for header, _ in self.drops(start, now()):
                for flow in flows:
                    if flow not in found and matches(header, flow):
                        found.add(flow)
            if len(found) == len(flows) or time.monotonic() >= deadline:
                return found
            sleep(0.5)
