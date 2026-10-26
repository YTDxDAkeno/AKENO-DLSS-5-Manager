"""Bandwidth Monitor: total upload and download per adapter, and which programs use the network.

Windows keeps byte counters per adapter, not per program; per-program network bytes need event tracing with
administrator rights. AKENO therefore shows what it can measure without them, and labels it:

* the adapters' real upload and download rate (and history while the page is open);
* per program: its open internet connections (TCP established, UDP endpoints) and the remote addresses, plus its
  total I/O rate. Windows counts network and file transfers together in that rate, so it is a hint, not a network
  measurement.
* programs known to download in the background (launchers, cloud sync, Windows Update) are marked.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import psutil

from akeno.i18n import msg
from akeno.system.tasks import TaskContext, context

DOWNLOADERS = {
    "steam.exe": "Steam", "steamwebhelper.exe": "Steam", "steamservice.exe": "Steam",
    "epicgameslauncher.exe": "Epic Games Launcher", "eadesktop.exe": "EA app", "eabackgroundservice.exe": "EA app",
    "upc.exe": "Ubisoft Connect", "battle.net.exe": "Battle.net", "agent.exe": "Battle.net (agent)",
    "galaxyclient.exe": "GOG Galaxy", "riotclientservices.exe": "Riot Client",
    "onedrive.exe": "OneDrive", "dropbox.exe": "Dropbox", "googledrivefs.exe": "Google Drive",
    "qbittorrent.exe": "qBittorrent", "utorrent.exe": "uTorrent", "bittorrent.exe": "BitTorrent",
    "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "firefox.exe": "Firefox",
    "discord.exe": "Discord", "obs64.exe": "OBS Studio", "spotify.exe": "Spotify",
}
WINDOWS_TRANSFER = {"wuauserv": "Windows Update", "bits": "Background Intelligent Transfer",
                    "dosvc": "Delivery Optimization"}


@dataclass
class ProcessNet:
    pid: int
    name: str
    title: str
    tcp: int = 0
    udp: int = 0
    remotes: set[str] = field(default_factory=set)
    io_bps: float | None = None
    known: bool = False
    services: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["remotes"] = sorted(self.remotes)[:20]
        return d


def connections() -> dict[int, ProcessNet]:
    out: dict[int, ProcessNet] = {}
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        return out
    names: dict[int, str] = {}
    for c in conns:
        if not c.pid:
            continue
        if c.type == 1 and c.status != psutil.CONN_ESTABLISHED:      # SOCK_STREAM: only established
            continue
        if c.raddr and _local(c.raddr.ip):
            continue
        if c.pid not in names:
            try:
                names[c.pid] = psutil.Process(c.pid).name()
            except psutil.Error:
                names[c.pid] = "?"
        name = names[c.pid]
        p = out.get(c.pid)
        if p is None:
            p = out[c.pid] = ProcessNet(c.pid, name, DOWNLOADERS.get(name.lower(), name),
                                        known=name.lower() in DOWNLOADERS)
        if c.type == 1:
            p.tcp += 1
        else:
            p.udp += 1
        if c.raddr:
            p.remotes.add(f"{c.raddr.ip}:{c.raddr.port}")
    return out


def _local(ip: str) -> bool:
    return ip.startswith(("127.", "::1", "0.0.0.0")) or ip == "::"


def sample(seconds: float = 1.5, ctx: TaskContext | None = None, services: dict[int, list[str]] | None = None
           ) -> dict[str, Any]:
    """One measurement: adapter rates and programs with internet connections."""
    ctx = context(ctx)
    procs = connections()
    io0 = {}
    for pid in procs:
        try:
            c = psutil.Process(pid).io_counters()
            io0[pid] = c.read_bytes + c.write_bytes
        except (psutil.Error, AttributeError, OSError):
            pass
    net0 = psutil.net_io_counters()
    t0 = time.monotonic()
    ctx.sleep(seconds)
    dt = max(0.001, time.monotonic() - t0)
    net1 = psutil.net_io_counters()
    for pid, p in procs.items():
        if pid in io0:
            try:
                c = psutil.Process(pid).io_counters()
                p.io_bps = max(0.0, (c.read_bytes + c.write_bytes - io0[pid]) / dt)
            except (psutil.Error, AttributeError, OSError):
                pass
        if services and pid in services:
            p.services = services[pid]
            if any(s.lower() in WINDOWS_TRANSFER for s in p.services):
                p.known = True
                p.title = ", ".join(WINDOWS_TRANSFER[s.lower()] for s in p.services if s.lower() in WINDOWS_TRANSFER)
    rows = sorted(procs.values(), key=lambda p: (-(p.io_bps or 0), -(p.tcp + p.udp)))
    return {"t": time.time(), "up_bps": max(0, net1.bytes_sent - net0.bytes_sent) / dt,
            "down_bps": max(0, net1.bytes_recv - net0.bytes_recv) / dt,
            "processes": [p.to_dict() for p in rows[:40]]}


def service_names() -> dict[int, list[str]]:
    """Windows: the service short names in each svchost process (to name Windows Update, BITS, Delivery
    Optimization)."""
    out: dict[int, list[str]] = {}
    if not hasattr(psutil, "win_service_iter"):
        return out
    try:
        for s in psutil.win_service_iter():
            try:
                d = s.as_dict()
            except (psutil.Error, OSError):
                continue
            if d.get("pid"):
                out.setdefault(d["pid"], []).append(d.get("name") or "")
    except (psutil.Error, OSError):
        pass
    return out
