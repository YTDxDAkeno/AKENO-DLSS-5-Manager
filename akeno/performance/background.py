"""Background programs: what runs next to the game and how much it uses (read-only analysis).

Only programs the user selects are ever lowered in priority or paused, and never the protected ones: Windows
itself, security software, anti-cheat services, audio, input and display drivers, AKENO, and the game's own
processes. Analysis uses psutil (CPU share over a short interval and working-set memory).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import psutil

# never lowered or paused, whatever the user selects
PROTECTED = {
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
    "lsaiso.exe", "svchost.exe", "dwm.exe", "explorer.exe", "fontdrvhost.exe", "sihost.exe", "ctfmon.exe",
    "audiodg.exe", "conhost.exe", "dllhost.exe", "runtimebroker.exe", "searchhost.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "taskhostw.exe", "msmpeng.exe", "nissrv.exe", "securityhealthservice.exe",
    "smartscreen.exe", "memcompression", "spoolsv.exe", "wudfhost.exe", "nvcontainer.exe", "nvdisplay.container.exe",
    "amdrsserv.exe", "igfxem.exe", "rtkaudioservice64.exe",
    # anti-cheat: touching these can get an account banned
    "easyanticheat.exe", "easyanticheat_eos.exe", "beservice.exe", "beservice_x64.exe", "vgc.exe", "vgtray.exe",
    "faceitservice.exe", "faceit.exe", "esea client.exe", "eac_launcher.exe", "gameguard.erl", "xigncode.exe",
    # AKENO
    "akeno.exe", "python.exe", "pythonw.exe",
}
# what usually runs in the background and costs frames; offered for selection, nothing is preselected
KNOWN = {
    "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "firefox.exe": "Firefox", "opera.exe": "Opera",
    "brave.exe": "Brave", "onedrive.exe": "OneDrive", "dropbox.exe": "Dropbox", "googledrivefs.exe": "Google Drive",
    "teams.exe": "Microsoft Teams", "ms-teams.exe": "Microsoft Teams", "slack.exe": "Slack", "spotify.exe": "Spotify",
    "epicgameslauncher.exe": "Epic Games Launcher", "eadesktop.exe": "EA app", "upc.exe": "Ubisoft Connect",
    "battle.net.exe": "Battle.net", "galaxyclient.exe": "GOG Galaxy", "steamwebhelper.exe": "Steam (web helper)",
    "discord.exe": "Discord", "obs64.exe": "OBS Studio", "icue.exe": "iCUE", "lghub.exe": "Logitech G HUB",
    "razer synapse 3.exe": "Razer Synapse", "armourycrate.service.exe": "Armoury Crate", "searchapp.exe": "Search",
    "phoneexperiencehost.exe": "Phone Link", "widgets.exe": "Widgets",
}
STREAMING = {"obs64.exe", "obs32.exe", "discord.exe", "streamlabs obs.exe", "xsplit.core.exe", "nvidia broadcast.exe"}


@dataclass
class BackgroundProcess:
    name: str
    title: str
    pids: list[int]
    cpu_percent: float
    memory_mb: float
    known: bool

    @property
    def pid(self) -> int:
        return self.pids[0]


def protected(name: str) -> bool:
    return name.lower() in PROTECTED


def _own_tree() -> set[int]:
    try:
        me = psutil.Process(os.getpid())
        return {me.pid, *(p.pid for p in me.children(recursive=True)), *(p.pid for p in me.parents())}
    except psutil.Error:
        return {os.getpid()}


def analyse(sample_s: float = 0.5, exclude_pid: int | None = None, limit: int = 25) -> list[BackgroundProcess]:
    """Programs of the current user by CPU and memory use, grouped by executable name."""
    skip = _own_tree()
    if exclude_pid:
        skip |= _tree(exclude_pid)
    procs = []
    for p in psutil.process_iter(["pid", "name", "username"]):
        name = (p.info.get("name") or "").lower()
        if not name or p.pid in skip or protected(name) or p.pid in (0, 4):
            continue
        try:
            p.cpu_percent(None)
            procs.append(p)
        except psutil.Error:
            continue
    time.sleep(sample_s)
    groups: dict[str, BackgroundProcess] = {}
    cores = psutil.cpu_count() or 1
    for p in procs:
        try:
            cpu = p.cpu_percent(None) / cores
            mem = p.memory_info().rss / 2 ** 20
        except psutil.Error:
            continue
        name = (p.info.get("name") or "").lower()
        g = groups.setdefault(name, BackgroundProcess(name, KNOWN.get(name, p.info.get("name") or name), [], 0.0, 0.0,
                                                      name in KNOWN))
        g.pids.append(p.pid)
        g.cpu_percent += cpu
        g.memory_mb += mem
    rows = sorted(groups.values(), key=lambda g: (g.known, g.cpu_percent * 50 + g.memory_mb), reverse=True)
    return [r for r in rows if r.known or r.cpu_percent >= 0.5 or r.memory_mb >= 300][:limit]


def _tree(pid: int) -> set[int]:
    try:
        p = psutil.Process(pid)
        return {pid, *(c.pid for c in p.children(recursive=True))}
    except psutil.Error:
        return {pid}


def matching_processes(names: list[str], exclude_pid: int | None = None) -> list[BackgroundProcess]:
    """Running processes with these executable names, minus protected ones and the game's own tree."""
    wanted = {n.lower() for n in names if not protected(n)}
    skip = _own_tree() | (_tree(exclude_pid) if exclude_pid else set())
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        name = (p.info.get("name") or "").lower()
        if name in wanted and p.pid not in skip:
            out.append(BackgroundProcess(name, KNOWN.get(name, name), [p.pid], 0.0, 0.0, name in KNOWN))
    return out
