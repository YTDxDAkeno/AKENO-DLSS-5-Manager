"""Latency probes: ICMP echo (Windows ``IcmpSendEcho``, no administrator rights) and the TCP handshake time.

ICMP and TCP measure different things. Many servers and routers drop ICMP or answer it with low priority, so an
ICMP target that does not answer is *not* an outage; the TCP handshake to a web server (port 443) shows whether the
path itself works. Every probe returns the round-trip time in milliseconds or None for "no answer".
"""

from __future__ import annotations

import ipaddress
import socket
import statistics
import time
from dataclasses import dataclass, field
from typing import Callable

from akeno.paths import is_windows


class IcmpUnavailable(OSError):
    pass


class WinIcmp:
    """IPv4 echo through iphlpapi's IcmpSendEcho (documented, works for standard users)."""

    def __init__(self):
        if not is_windows():
            raise IcmpUnavailable("ICMP echo through Windows needs Windows.")
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        self.lib = ctypes.WinDLL("iphlpapi", use_last_error=True)
        self.lib.IcmpCreateFile.restype = wintypes.HANDLE
        self.lib.IcmpCloseHandle.argtypes = [wintypes.HANDLE]
        self.lib.IcmpSendEcho.argtypes = [wintypes.HANDLE, ctypes.c_uint32, ctypes.c_void_p, wintypes.WORD,
                                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
        self.lib.IcmpSendEcho.restype = wintypes.DWORD

    def ping(self, host: str, timeout_ms: int = 1000) -> float | None:
        ctypes, wintypes = self.ctypes, self.wintypes
        try:
            addr = ipaddress.IPv4Address(host)
        except ValueError:
            raise IcmpUnavailable("ICMP probes are sent to IPv4 addresses only.")
        dest = int.from_bytes(addr.packed, "little")        # IPAddr in network byte order, read as a ULONG
        payload = b"AKENO-NetworkLab"

        class ICMP_ECHO_REPLY(ctypes.Structure):
            _fields_ = [("Address", ctypes.c_uint32), ("Status", ctypes.c_uint32), ("RoundTripTime", ctypes.c_uint32),
                        ("DataSize", ctypes.c_uint16), ("Reserved", ctypes.c_uint16), ("Data", ctypes.c_void_p),
                        ("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte), ("Flags", ctypes.c_ubyte),
                        ("OptionsSize", ctypes.c_ubyte), ("OptionsData", ctypes.c_void_p)]

        size = ctypes.sizeof(ICMP_ECHO_REPLY) + len(payload) + 8 + 64
        buf = ctypes.create_string_buffer(size)
        data = ctypes.create_string_buffer(payload)
        h = self.lib.IcmpCreateFile()
        if not h or h == wintypes.HANDLE(-1).value:
            raise IcmpUnavailable("Windows could not open an ICMP handle.")
        try:
            t0 = time.perf_counter()
            n = self.lib.IcmpSendEcho(h, dest, data, len(payload), None, buf, size, timeout_ms)
            elapsed = (time.perf_counter() - t0) * 1000
        finally:
            self.lib.IcmpCloseHandle(h)
        if n == 0:
            return None
        reply = ICMP_ECHO_REPLY.from_buffer(buf)
        if reply.Status != 0:                     # IP_SUCCESS; anything else is unreachable, TTL expired...
            return None
        # RoundTripTime has 1 ms resolution; the measured call time is a better value below a millisecond
        rtt = float(reply.RoundTripTime)
        return round(min(elapsed, rtt + 1.0) if rtt < 2 else rtt, 2)


def tcp_ping(host: str, port: int = 443, timeout: float = 2.0) -> float | None:
    """Time of a TCP handshake (connect, then close without sending anything)."""
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return round((time.perf_counter() - t0) * 1000, 2)
    except OSError:
        return None


@dataclass
class Series:
    target: str
    method: str                       # icmp | tcp
    label: str = ""
    rtts: list[float | None] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    error: str = ""

    @property
    def sent(self) -> int:
        return len(self.rtts)

    @property
    def received(self) -> list[float]:
        return [r for r in self.rtts if r is not None]

    def stats(self) -> "Stats":
        return compute(self.rtts)


@dataclass
class Stats:
    sent: int
    received: int
    loss_percent: float | None
    min: float | None
    avg: float | None
    median: float | None
    max: float | None
    jitter: float | None
    p95: float | None
    spikes: int

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def compute(rtts: list[float | None]) -> Stats:
    """Loss, min/avg/median/max, p95, jitter and spikes.

    Jitter is the mean absolute difference between consecutive answered probes (the instantaneous variation of
    RFC 3550, averaged instead of smoothed). A spike is an answer more than twice the median and at least 20 ms
    above it."""
    sent = len(rtts)
    got = [r for r in rtts if r is not None]
    if not sent:
        return Stats(0, 0, None, None, None, None, None, None, None, 0)
    loss = round((sent - len(got)) / sent * 100, 1)
    if not got:
        return Stats(sent, 0, loss, None, None, None, None, None, None, 0)
    ordered = sorted(got)
    med = statistics.median(ordered)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    diffs = [abs(b - a) for a, b in zip(got, got[1:])]
    jitter = round(sum(diffs) / len(diffs), 2) if diffs else 0.0
    spikes = sum(1 for r in got if r > 2 * med and r - med >= 20)
    return Stats(sent, len(got), loss, round(ordered[0], 2), round(sum(got) / len(got), 2), round(med, 2),
                 round(ordered[-1], 2), jitter, round(p95, 2), spikes)


Prober = Callable[[str, str], float | None]          # (method, target) -> rtt or None


def default_prober() -> Prober:
    icmp = None
    try:
        icmp = WinIcmp()
    except IcmpUnavailable:
        icmp = None

    def probe(method: str, target: str) -> float | None:
        if method == "icmp":
            if icmp is None:
                raise IcmpUnavailable("ICMP needs Windows (or administrator rights elsewhere).")
            return icmp.ping(target)
        host, _, port = target.rpartition(":") if target.count(":") == 1 else (target, "", "")
        return tcp_ping(host or target, int(port) if port else 443)
    return probe
