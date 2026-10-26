"""Bufferbloat test: how much latency grows while the connection is full.

Phases: latency while idle, then while downloading, then while uploading. The load comes from Cloudflare's public
speed-test endpoints (``speed.cloudflare.com/__down`` and ``/__up``); the user is told that, and how much data and
time the test may use, before it starts. Each load phase stops at its time *or* data limit, whichever comes first.

The result compares medians: the added latency under load (and its jitter) is what makes games lag while someone
streams or uploads. A large increase is something the router's queue management (QoS / SQM such as fq_codel or
CAKE) can address; AKENO says that, but claims no improvement it did not measure.
"""

from __future__ import annotations

import ssl
import statistics
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from akeno.i18n import msg
from akeno.system.model import Finding, Level, Report
from akeno.system.network.probe import Prober, compute
from akeno.system.tasks import Cancelled, TaskContext, TimedOut, context

DOWN_URL = "https://speed.cloudflare.com/__down?bytes={n}"
UP_URL = "https://speed.cloudflare.com/__up"
HOST = "speed.cloudflare.com"


@dataclass(frozen=True)
class Limits:
    seconds: float = 12.0          # per load phase
    download_bytes: int = 200 * 2 ** 20
    upload_bytes: int = 40 * 2 ** 20
    streams: int = 4
    idle_seconds: float = 6.0

    @property
    def total_bytes(self) -> int:
        return self.download_bytes + self.upload_bytes


GRADES = ((10, "A+"), (30, "A"), (60, "B"), (200, "C"), (400, "D"))


def grade(added_ms: float | None) -> str:
    if added_ms is None:
        return "?"
    for limit, g in GRADES:
        if added_ms < limit:
            return g
    return "F"


class HttpLoad:
    """Download or upload through parallel HTTPS streams until the time or byte limit."""

    def __init__(self, opener=None):
        self.ctx_ssl = ssl.create_default_context()
        self.opener = opener

    def run(self, direction: str, limits: Limits, stop: threading.Event) -> dict[str, float]:
        budget = limits.download_bytes if direction == "down" else limits.upload_bytes
        per = budget // limits.streams
        moved = [0] * limits.streams
        errors: list[str] = []
        t0 = time.monotonic()

        def stream(i: int) -> None:
            try:
                if direction == "down":
                    req = urllib.request.Request(DOWN_URL.format(n=per), headers={"User-Agent": "AKENO-NetworkLab"})
                    with self._open(req) as resp:
                        while not stop.is_set() and moved[i] < per:
                            chunk = resp.read(64 * 1024)
                            if not chunk:
                                break
                            moved[i] += len(chunk)
                else:
                    body = _Body(per, stop, lambda n: moved.__setitem__(i, moved[i] + n))
                    req = urllib.request.Request(UP_URL, data=body, method="POST",
                                                 headers={"User-Agent": "AKENO-NetworkLab",
                                                          "Content-Type": "application/octet-stream",
                                                          "Content-Length": str(per)})
                    with self._open(req) as resp:
                        resp.read(1024)
            except Exception as exc:                  # noqa: BLE001 (reported, the phase still has its pings)
                if not stop.is_set():
                    errors.append(str(exc))

        threads = [threading.Thread(target=stream, args=(i,), daemon=True) for i in range(limits.streams)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(max(0.0, limits.seconds + 5 - (time.monotonic() - t0)))
        dt = max(0.001, time.monotonic() - t0)
        return {"bytes": float(sum(moved)), "seconds": dt, "mbps": sum(moved) * 8 / dt / 1e6,
                "errors": float(len(errors)), "error": errors[0] if errors else ""}

    def _open(self, req):
        if self.opener is not None:
            return self.opener(req)
        return urllib.request.urlopen(req, timeout=10, context=self.ctx_ssl)


class _Body:
    """An upload body of ``size`` zero bytes, read in chunks, stopping when told to."""

    def __init__(self, size: int, stop: threading.Event, counted: Callable[[int], None]):
        self.left = size
        self.stop = stop
        self.counted = counted
        self.chunk = b"\0" * (64 * 1024)

    def read(self, n: int = -1) -> bytes:
        if self.left <= 0 or self.stop.is_set():
            return b""
        k = min(self.left, len(self.chunk) if n is None or n < 0 else min(n, len(self.chunk)))
        self.left -= k
        self.counted(k)
        return self.chunk[:k]

    def __len__(self) -> int:
        return self.left


@dataclass
class Phase:
    name: str
    rtts: list[float | None] = field(default_factory=list)
    load: dict[str, float] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {"name": self.name, "stats": compute(self.rtts).to_dict(), "load": self.load}


def run(prober: Prober, target: str, consent: bool, limits: Limits = Limits(), ctx: TaskContext | None = None,
        loader: HttpLoad | None = None) -> Report:
    """``consent`` must be True: the caller asked the user, who saw the data and time limits."""
    if not consent:
        raise PermissionError(msg("The bufferbloat test needs your consent: it downloads and uploads test data."))
    ctx = context(ctx)
    loader = loader or HttpLoad()
    phases = [Phase("idle"), Phase("download"), Phase("upload")]
    for i, ph in enumerate(phases):
        ctx.check()
        ctx.report(msg("Bufferbloat test: {phase}", phase=msg(PHASE_TEXT[ph.name])), i, len(phases))
        stop = threading.Event()
        result: dict[str, Any] = {}
        th = None
        if ph.name != "idle":
            def work(direction=("down" if ph.name == "download" else "up"), out=result, stop=stop):
                out.update(loader.run(direction, limits, stop))
            th = threading.Thread(target=work, daemon=True)
            th.start()
            time.sleep(0.8)                         # let the streams ramp up before measuring
        end = time.monotonic() + (limits.idle_seconds if ph.name == "idle" else limits.seconds)
        try:
            while time.monotonic() < end:
                ctx.check()
                t = time.monotonic()
                try:
                    ph.rtts.append(prober(*_split(target)))
                except OSError:
                    ph.rtts.append(None)
                if th is not None and not th.is_alive():
                    break                            # the data limit was reached first
                rest = 0.25 - (time.monotonic() - t)
                if rest > 0:
                    time.sleep(rest)
        finally:
            stop.set()
            if th is not None:
                th.join(15)
        ph.load = {k: v for k, v in result.items() if k != "error"} | ({"error": result["error"]} if result.get("error")
                                                                         else {})
    return interpret(phases, target, limits)


PHASE_TEXT = {"idle": "idle", "download": "while downloading", "upload": "while uploading"}


def _split(target: str) -> tuple[str, str]:
    return ("tcp", target) if ":" in target else ("icmp", target)


def interpret(phases: list[Phase], target: str, limits: Limits) -> Report:
    rep = Report("network")
    rep.data["phases"] = [p.summary() for p in phases]
    rep.data["target"] = target
    idle = compute(phases[0].rtts)
    rep.data["idle"] = idle.to_dict()
    if not idle.received:
        rep.errors.append(msg("The latency target did not answer while idle; the test cannot compare."))
        return rep
    worst = None
    for ph in phases[1:]:
        st = compute(ph.rtts)
        added = round(st.median - idle.median, 1) if st.median is not None else None
        ph_data = {"added_ms": added, "grade": grade(added), "stats": st.to_dict(), "load": ph.load}
        rep.data[ph.name] = ph_data
        if added is not None and (worst is None or added > worst[1]):
            worst = (ph.name, added, st)
    if worst is None:
        rep.errors.append(msg("No latency was measured under load."))
        return rep
    name, added, st = worst
    g = grade(added)
    rep.data["grade"] = g
    level = Level.OK if added < 30 else (Level.INFO if added < 60 else (Level.WARN if added < 200 else Level.PROBLEM))
    f = rep.add(Finding("net.bufferbloat", "network", level,
                        msg("Latency under load: +{ms} ms (grade {g})", ms=added, g=g),
                        msg("Idle: {idle} ms. Worst {phase}: {load} ms median, {j} ms jitter.", idle=idle.median,
                            phase=msg(PHASE_TEXT[name]), load=st.median, j=st.jitter),
                        msg("No action needed: a full connection barely affects your latency.") if added < 30 else
                        msg("When your connection is busy (downloads, uploads, streams), games lag. Smart queue "
                            "management on the router (QoS or SQM with fq_codel or CAKE) usually helps; pausing big "
                            "downloads while you play does too. Measure again after a change.")))
    for ph in phases[1:]:
        d = rep.data.get(ph.name) or {}
        load = d.get("load") or {}
        f.measured(msg("{phase}: +{ms} ms at {mbps} Mbit/s", phase=msg(PHASE_TEXT[ph.name]), ms=d.get("added_ms"),
                       mbps=round(load.get("mbps") or 0, 1)), HOST)
    if added >= 60:
        f.cause(msg("The router or modem queues packets when the line is full (bufferbloat)."))
        f.link("network", msg("Open Network Gaming Mode"), "gaming")
    return rep
