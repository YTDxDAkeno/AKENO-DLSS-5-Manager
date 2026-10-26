"""DNS benchmark and (only on explicit request) a reversible DNS server change.

The benchmark sends standard DNS queries (UDP port 53, one A record, recursion desired) for popular names (the
answer a resolver usually has cached: what you feel when you open a game launcher) and for random names under
``example.com`` (IANA-reserved; the resolver has to ask the authoritative servers: an uncached lookup). It reports
the median and average time, the timeouts and errors, and how reliable each server was.

AKENO never changes DNS settings by itself. When the user chooses a server for an adapter, the current setting is
recorded first (static addresses, or "assigned by DHCP"), the change runs with administrator rights through the UAC
prompt, and *Restore* puts back exactly what was there.
"""

from __future__ import annotations

import os
import random
import socket
import statistics
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from akeno.i18n import msg
from akeno.system.tasks import TaskContext, context

RESOLVERS = (("Cloudflare", "1.1.1.1"), ("Google", "8.8.8.8"), ("Quad9", "9.9.9.9"), ("OpenDNS", "208.67.222.222"),
             ("AdGuard", "94.140.14.14"))
POPULAR = ("www.google.com", "store.steampowered.com", "www.epicgames.com", "discord.com", "www.microsoft.com",
           "www.youtube.com", "www.cloudflare.com", "www.riotgames.com")
UNCACHED_PARENT = "example.com"


def build_query(name: str, qid: int, qtype: int = 1) -> bytes:
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)          # recursion desired, one question
    q = b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.rstrip(".").split(".")) + b"\0"
    return header + q + struct.pack(">HH", qtype, 1)


def parse_reply(data: bytes, qid: int) -> tuple[bool, int, int]:
    """(matches our query, response code, answer count). Raises ValueError for malformed packets."""
    if len(data) < 12:
        raise ValueError("short DNS reply")
    rid, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", data[:12])
    if rid != qid or not flags & 0x8000:
        return False, -1, 0
    return True, flags & 0xF, an


def query(server: str, name: str, timeout: float = 2.0, sock_factory=None) -> tuple[float | None, str]:
    """Time of one lookup in ms, or None with the reason ("timeout", "servfail", "refused", "error")."""
    qid = random.randint(0, 0xFFFF)
    packet = build_query(name, qid)
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    sock = (sock_factory or socket.socket)(family, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        t0 = time.perf_counter()
        sock.sendto(packet, (server, 53))
        end = t0 + timeout
        while True:
            left = end - time.perf_counter()
            if left <= 0:
                return None, "timeout"
            sock.settimeout(left)
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                return None, "timeout"
            try:
                ok, rcode, _ = parse_reply(data, qid)
            except ValueError:
                continue
            if not ok:
                continue                      # a late answer to an earlier query, or a spoofed packet
            ms = round((time.perf_counter() - t0) * 1000, 2)
            if rcode in (0, 3):               # NOERROR, NXDOMAIN: both are valid answers
                return ms, "ok"
            return None, {2: "servfail", 5: "refused"}.get(rcode, "error")
    except OSError:
        return None, "error"
    finally:
        sock.close()


@dataclass
class ResolverResult:
    name: str
    server: str
    cached: list[float | None] = field(default_factory=list)
    uncached: list[float | None] = field(default_factory=list)
    errors: dict[str, int] = field(default_factory=dict)
    system: bool = False

    def summary(self) -> dict[str, Any]:
        def med(xs):
            got = [x for x in xs if x is not None]
            return round(statistics.median(got), 1) if got else None
        all_ = self.cached + self.uncached
        answered = [x for x in all_ if x is not None]
        return {"name": self.name, "server": self.server, "system": self.system,
                "cached_ms": med(self.cached), "uncached_ms": med(self.uncached),
                "avg_ms": round(sum(answered) / len(answered), 1) if answered else None,
                "answered_percent": round(len(answered) / len(all_) * 100, 1) if all_ else None,
                "errors": dict(self.errors), "queries": len(all_)}


def benchmark(servers: list[tuple[str, str, bool]], ctx: TaskContext | None = None, rounds: int = 2,
              uncached: int = 5, lookup: Callable[[str, str], tuple[float | None, str]] | None = None
              ) -> list[dict[str, Any]]:
    """``servers``: (name, address, is_current_system_server). Sorted by reliability, then cached speed."""
    ctx = context(ctx)
    lookup = lookup or (lambda server, name: query(server, name))
    results = [ResolverResult(n, a, system=s) for n, a, s in servers]
    total = len(results) * (rounds * len(POPULAR) + uncached)
    done = 0
    for r in range(rounds):
        for name in POPULAR:
            for res in results:                 # interleaved, so a passing network hiccup hits every server
                ctx.check()
                ms, why = lookup(res.server, name)
                if r == 0 and ms is not None:
                    continue                     # the first round only warms the resolver's cache
                res.cached.append(ms)
                if ms is None:
                    res.errors[why] = res.errors.get(why, 0) + 1
                done += 1
                ctx.report(msg("Querying DNS servers"), done, total)
    for i in range(uncached):
        label = "akeno-" + "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(12))
        for res in results:
            ctx.check()
            ms, why = lookup(res.server, f"{label}.{UNCACHED_PARENT}")
            res.uncached.append(ms)
            if ms is None:
                res.errors[why] = res.errors.get(why, 0) + 1
            done += 1
            ctx.report(msg("Querying DNS servers"), done, total)
    out = [r.summary() for r in results]
    return sorted(out, key=lambda s: (-(s["answered_percent"] or 0), s["cached_ms"] if s["cached_ms"] is not None
                                      else 1e9))


def server_list(system_dns: list[str]) -> list[tuple[str, str, bool]]:
    out = [(str(msg("Current ({addr})", addr=a)), a, True) for a in dict.fromkeys(system_dns) if a and ":" not in a]
    known = {a for _, a, _ in out}
    out += [(n, a, False) for n, a in RESOLVERS if a not in known]
    return out


# -- explicit, reversible change ------------------------------------------------------------------------------------
def current_setting(interface_guid_or_index, reader=None) -> dict[str, Any]:
    """What restoring needs: the static servers, or that DHCP assigns them (``NameServer`` empty)."""
    reader = reader or _read_nameserver
    value = reader(interface_guid_or_index)
    servers = [s for s in (value or "").replace(";", ",").replace(" ", ",").split(",") if s]
    return {"dhcp": not servers, "servers": servers}


def _read_nameserver(index) -> str:
    from akeno.system.commands import powershell_json
    data = powershell_json(
        "$a = Get-NetAdapter -InterfaceIndex " + str(int(index)) + " -ErrorAction Stop; "
        "$k = 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\Interfaces\\' + $a.InterfaceGuid; "
        "[pscustomobject]@{ns=(Get-ItemProperty -LiteralPath $k -Name NameServer -ErrorAction SilentlyContinue).NameServer}",
        30)
    return (data or {}).get("ns") or ""


def apply_servers(index: int, servers: list[str] | None, store=None, before: dict | None = None,
                  runner=None) -> int | None:
    """Set the adapter's DNS servers (None: back to DHCP) through UAC. Journalled with ``before`` for restore."""
    import ipaddress
    for s in servers or []:
        ipaddress.ip_address(s)                 # nothing but addresses reaches the command line
    if servers:
        cmd = ("Set-DnsClientServerAddress -InterfaceIndex " + str(int(index)) + " -ServerAddresses "
               + ",".join("'" + s + "'" for s in servers))
    else:
        cmd = "Set-DnsClientServerAddress -InterfaceIndex " + str(int(index)) + " -ResetServerAddresses"
    aid = store.begin("network", "dns.set", str(index), ",".join(servers or ["DHCP"]),
                      {"index": int(index), "before": before}) if store else None
    try:
        code = (runner or _elevated_ps)(cmd)
    except Exception as exc:
        if aid:
            store.finish(aid, False, str(exc))
        raise
    if aid:
        store.finish(aid, code == 0, "UAC declined" if code is None else f"exit {code}")
    return code


def restore(action_id: int, store, runner=None) -> int | None:
    a = store.action(action_id)
    if not a or a["kind"] != "dns.set" or a["state"] != "done":
        raise ValueError("This change cannot be restored.")
    before = a["undo"].get("before") or {}
    servers = None if before.get("dhcp", True) else before.get("servers")
    code = apply_servers(a["undo"]["index"], servers, None, runner=runner)
    if code == 0:
        store.mark_undone(action_id)
    return code


def _elevated_ps(cmd: str) -> int | None:
    import base64
    from akeno.system.commands import powershell_exe, run_elevated
    encoded = base64.b64encode(("$ErrorActionPreference='Stop';" + cmd).encode("utf-16-le")).decode("ascii")
    return run_elevated(powershell_exe(), f"-NoLogo -NoProfile -NonInteractive -EncodedCommand {encoded}",
                        timeout=120, show=False)
