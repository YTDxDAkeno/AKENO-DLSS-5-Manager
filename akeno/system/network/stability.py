"""Ping & Stability Test: latency, jitter and loss to the local gateway and to the internet, measured in parallel,
and what the pattern means.

Targets:
* the **gateway** (your router), ICMP: problems here are in your own network (Wi-Fi, cable, router);
* **internet** targets, ICMP and the TCP handshake to port 443 of the same hosts;
* optionally a host the user names (a game server).

Reading the results:
* gateway bad → local network; gateway good but internet bad → beyond your network (provider, route);
* ICMP unanswered while TCP works → the target or a router drops ICMP: not an outage;
* nothing answers and names do not resolve → no connection; addresses answer but names do not → DNS.
"""

from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from akeno.i18n import msg
from akeno.system.model import Finding, Level, Report
from akeno.system.network.probe import IcmpUnavailable, Prober, Series, compute
from akeno.system.tasks import Cancelled, TaskContext, TimedOut, context

INTERNET = (("1.1.1.1", "Cloudflare"), ("8.8.8.8", "Google"), ("9.9.9.9", "Quad9"))
JITTER_WARN, JITTER_BAD = 15.0, 30.0
LOSS_WARN, LOSS_BAD = 1.0, 5.0
GATEWAY_SLOW = 20.0          # ms: a wired router answers in about 1 ms, Wi-Fi in a few
NAME_TEST = "www.microsoft.com"


@dataclass
class Plan:
    gateway: str | None
    internet: tuple[tuple[str, str], ...] = INTERNET
    extra: str | None = None              # host or host:port the user named
    count: int = 30
    interval: float = 0.5


def run(plan: Plan, prober: Prober, ctx: TaskContext | None = None,
        resolve: Callable[[str], bool] | None = None) -> Report:
    ctx = context(ctx)
    t0 = time.monotonic()
    series: list[Series] = []
    if plan.gateway:
        series.append(Series(plan.gateway, "icmp", "gateway"))
    for host, name in plan.internet:
        series.append(Series(host, "icmp", name))
        series.append(Series(host + ":443", "tcp", name))
    if plan.extra:
        method = "tcp" if ":" in plan.extra else "icmp"
        series.append(Series(plan.extra, method, "custom"))
        if method == "icmp":
            series.append(Series(plan.extra + ":443", "tcp", "custom"))
    lock = threading.Lock()
    done = [0]
    total = plan.count * len(series)

    def worker(s: Series) -> None:
        for i in range(plan.count):
            if ctx.cancelled or ctx.timed_out:
                return
            start = time.monotonic()
            try:
                rtt = prober(s.method, s.target)
            except IcmpUnavailable as exc:
                s.error = str(exc)
                return
            except OSError as exc:
                rtt = None
                s.error = str(exc)
            s.rtts.append(rtt)
            with lock:
                done[0] += 1
                if done[0] % 10 == 0:
                    ctx.report(msg("Measuring latency"), done[0], total)
            left = plan.interval - (time.monotonic() - start)
            if left > 0 and i < plan.count - 1:
                time.sleep(left)

    with ThreadPoolExecutor(max_workers=min(12, len(series))) as pool:
        list(pool.map(worker, series))
    ctx.check()
    dns_ok = (resolve or _resolves)(NAME_TEST)
    rep = interpret(series, dns_ok, plan)
    rep.duration_s = round(time.monotonic() - t0, 1)
    return rep


def _resolves(name: str) -> bool:
    try:
        socket.getaddrinfo(name, 443, proto=socket.IPPROTO_TCP)
        return True
    except OSError:
        return False


def interpret(series: list[Series], dns_ok: bool, plan: Plan) -> Report:
    rep = Report("network")
    rows = []
    for s in series:
        st = s.stats()
        rows.append({"target": s.target, "label": s.label, "method": s.method, "stats": st.to_dict(),
                     "rtts": s.rtts, "error": s.error})
    rep.data["series"] = rows
    gw = next((s for s in series if s.label == "gateway"), None)
    icmp_inet = [s for s in series if s.method == "icmp" and s.label not in ("gateway", "custom")]
    tcp_inet = [s for s in series if s.method == "tcp" and s.label != "custom"]
    gw_st = gw.stats() if gw and not gw.error else None
    icmp_ok = [s for s in icmp_inet if s.stats().received and not s.error]
    tcp_ok = [s for s in tcp_inet if s.stats().received]
    icmp_unavailable = all(s.error and "ICMP" in s.error for s in icmp_inet) if icmp_inet else True

    if not tcp_ok and not icmp_ok:
        f = rep.add(Finding("net.offline", "network", Level.PROBLEM, msg("No internet connection"),
                            msg("No internet target answered, neither to ICMP nor to a TCP connection."),
                            msg("Check the cable or Wi-Fi connection and the router. Connection Repair shows the "
                                "adapter's state.")))
        if gw_st and gw_st.received:
            f.measured(msg("The router answers ({ms} ms): the problem is between the router and the internet.",
                           ms=gw_st.median), "ICMP")
        elif gw is not None:
            f.measured(msg("The router does not answer either."), "ICMP")
        f.link("network", msg("Open Connection Repair"), "repair")
        return rep
    if not dns_ok:
        f = rep.add(Finding("net.dns", "network", Level.PROBLEM, msg("Names do not resolve"),
                            msg("Internet addresses answer, but {name} could not be resolved.", name=NAME_TEST),
                            msg("The DNS server does not answer. The DNS benchmark compares other servers; Connection "
                                "Repair can clear the DNS cache.")))
        f.measured(msg("TCP connections to IP addresses work."), "TCP 443")
        f.link("network", msg("Run the DNS benchmark"), "dns")
    if icmp_inet and not icmp_ok and tcp_ok and not icmp_unavailable:
        f = rep.add(Finding("net.icmp_blocked", "network", Level.INFO, msg("ICMP is not answered"),
                            msg("The internet targets did not answer ping, but TCP connections to them work."),
                            msg("A firewall or the provider drops ICMP. That is not an outage; the TCP values below "
                                "are the ones to read.")))
        f.measured(msg("TCP handshake answered by {n} of {m} targets.", n=len(tcp_ok), m=len(tcp_inet)), "TCP 443")
    if gw_st and gw_st.sent:
        problems = []
        if (gw_st.loss_percent or 0) >= LOSS_WARN:
            problems.append(msg("{p} % of the probes to the router were lost", p=gw_st.loss_percent))
        if (gw_st.jitter or 0) >= JITTER_WARN:
            problems.append(msg("the router's answers vary by {j} ms on average", j=gw_st.jitter))
        if gw_st.median is not None and gw_st.median >= GATEWAY_SLOW:
            problems.append(msg("the router answers in {ms} ms (expected: a few ms)", ms=gw_st.median))
        if problems:
            level = Level.PROBLEM if (gw_st.loss_percent or 0) >= LOSS_BAD or (gw_st.jitter or 0) >= JITTER_BAD \
                else Level.WARN
            f = rep.add(Finding("net.local", "network", level, msg("Your local network is unstable"),
                                msg("The problem starts before the internet: between this PC and the router."),
                                msg("On Wi-Fi: move closer to the router, use 5 GHz, or use a cable for gaming. On a "
                                    "cable: check the cable and the router port.")))
            for p in problems:
                f.measured(p, "ICMP")
        elif gw_st.received == 0 and not gw.error:
            rep.add(Finding("net.gateway_silent", "network", Level.INFO, msg("The router does not answer ping"),
                            msg("Some routers do not answer ICMP. The internet tests below still tell how the "
                                "connection behaves.")))
    best = _best(tcp_ok + icmp_ok)
    if best is not None:
        st = best.stats()
        local_fine = gw_st is None or ((gw_st.loss_percent or 0) < LOSS_WARN and (gw_st.jitter or 0) < JITTER_WARN)
        bad = (st.loss_percent or 0) >= LOSS_WARN or (st.jitter or 0) >= JITTER_WARN or st.spikes >= 3
        if bad:
            f = rep.add(Finding("net.internet", "network",
                                Level.PROBLEM if (st.loss_percent or 0) >= LOSS_BAD or (st.jitter or 0) >= JITTER_BAD
                                else Level.WARN,
                                msg("The connection to the internet fluctuates"),
                                msg("Best target {t}: {loss} % loss, {j} ms jitter, {s} spikes.", t=best.target,
                                    loss=st.loss_percent, j=st.jitter, s=st.spikes),
                                msg("If your local network is fine, the cause is beyond it (provider, route) or your "
                                    "own connection is full: the bufferbloat test and the bandwidth monitor show "
                                    "whether downloads on your network cause it.")))
            f.measured(msg("{method} to {t}: median {med} ms, max {mx} ms", method=best.method.upper(), t=best.target,
                           med=st.median, mx=st.max), best.method.upper())
            if local_fine and gw_st is not None:
                f.cause(msg("The router answers steadily, so the fluctuation comes from beyond your network or from "
                            "traffic filling your connection."))
            f.link("network", msg("Run the bufferbloat test"), "bufferbloat")
            f.link("network", msg("Open the bandwidth monitor"), "bandwidth")
        rep.data["best"] = {"target": best.target, "method": best.method, "stats": st.to_dict()}
    custom = [s for s in series if s.label == "custom"]
    for s in custom:
        st = s.stats()
        if st.sent and not st.received and s.method == "icmp":
            rep.add(Finding("net.custom_icmp", "network", Level.INFO, msg("{t} does not answer ping", t=s.target),
                            msg("Many game servers drop ICMP. The TCP value or the game's own ping is the reliable "
                                "one.")))
    if not rep.problems():
        f = rep.add(Finding("net.stable", "network", Level.OK, msg("The connection was stable during the test"),
                            msg("No loss, low jitter and no spikes beyond the thresholds.")))
        if best is not None:
            st = best.stats()
            f.measured(msg("{method} to {t}: median {med} ms, jitter {j} ms, loss {loss} %",
                           method=best.method.upper(), t=best.target, med=st.median, j=st.jitter,
                           loss=st.loss_percent), best.method.upper())
    return rep


def _best(series: list[Series]) -> Series | None:
    """The steadiest internet series (the one most representative of the connection itself)."""
    scored = []
    for s in series:
        st = s.stats()
        if not st.received:
            continue
        scored.append(((st.loss_percent or 0), st.median or 1e9, s))
    if not scored:
        return None
    return sorted(scored, key=lambda x: (x[0], x[1]))[0][2]


def quick(prober: Prober, gateway: str | None, ctx: TaskContext | None = None, count: int = 10) -> dict[str, Any]:
    """A short measurement (before/after a change, the bufferbloat idle phase): median, jitter, loss."""
    rep = run(Plan(gateway, INTERNET[:2], None, count, 0.3), prober, ctx, resolve=lambda n: True)
    return rep.data.get("best") or {}
