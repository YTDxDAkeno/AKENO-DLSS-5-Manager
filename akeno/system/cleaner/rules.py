"""What Smart Cleaner may look at, and why.

Every category names its exact root folders (an allow-list: never "all of AppData"), the minimum age of a file,
why its files count as removable, the risk, and what the user may notice afterwards. Rules that hold for every
category:

* Only regular files below a category's roots; links, junctions and reparse points are neither followed nor
  removed (``akeno.system.fs``).
* Never Windows itself (``%SystemRoot%`` except its ``Temp``), ``Program Files``, ``Prefetch``, a browser's cookies,
  logins, sessions, local storage or profiles: browser categories name the cache folders themselves.
* Shader caches are never selected by default: after deleting them games compile their shaders again and can
  stutter until they have.
* Large files are offered for review only; Smart Cleaner never removes them in bulk.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

RISK_LOW, RISK_MEDIUM, RISK_REVIEW = "low", "medium", "review"
DAY = 86400.0


@dataclass(frozen=True)
class Category:
    id: str
    title: str
    group: str                       # temp | akeno | logs | windows | browser | launcher | shader | large
    roots: tuple[str, ...]
    reason: str
    risk: str
    side_effect: str = ""
    min_age_days: float = 0.0
    name_pattern: str | None = None  # regex on the file name (None: every file)
    preselect: bool = False          # selected in the results by default (low risk only)
    shader: bool = False             # needs the extra shader confirmation
    review_only: bool = False        # listed, never cleaned in bulk
    processes: tuple[str, ...] = ()  # the program that must be closed first (e.g. a browser)
    min_size: int = 0
    max_files: int = 200_000
    keep_dirs: tuple[str, ...] = ()  # folder names below the roots that are never entered
    max_depth: int = 64              # 0: only files directly in a root

    def matches(self, name: str) -> bool:
        return self.name_pattern is None or re.search(self.name_pattern, name, re.I) is not None


GROUP_TITLES = {"temp": "Temporary files", "akeno": "AKENO's own files", "logs": "Logs and reports",
                "browser": "Browser caches", "launcher": "Game launcher caches", "shader": "Graphics shader caches",
                "large": "Large files (review only)"}

# Chromium-based browsers: per-profile cache folders. Cookies, logins, sessions, history, local storage, IndexedDB
# and extensions live in other folders of the same profile and are never listed.
CHROMIUM = {"Google Chrome": ("chrome.exe", r"Google\Chrome\User Data"),
            "Microsoft Edge": ("msedge.exe", r"Microsoft\Edge\User Data"),
            "Brave": ("brave.exe", r"BraveSoftware\Brave-Browser\User Data"),
            "Vivaldi": ("vivaldi.exe", r"Vivaldi\User Data")}
CHROMIUM_CACHE_DIRS = (r"Cache\Cache_Data", "Code Cache", "GPUCache")


def j(base: str, rel: str) -> str:
    """Join a Windows-style relative path ("Google\\Chrome\\User Data") component by component."""
    return os.path.join(base, *rel.split("\\"))


def _env(env: Mapping[str, str], key: str) -> str | None:
    v = env.get(key)
    return v if v else None


def chromium_profiles(user_data: str) -> list[str]:
    """Profile folders ("Default", "Profile 1"...) of a Chromium "User Data" folder."""
    try:
        names = os.listdir(user_data)
    except OSError:
        return []
    return [os.path.join(user_data, n) for n in sorted(names)
            if n == "Default" or re.fullmatch(r"Profile \d+", n) or n == "Guest Profile"]


def firefox_caches(local: str) -> list[str]:
    root = j(local, r"Mozilla\Firefox\Profiles")
    try:
        return [os.path.join(root, n, "cache2") for n in sorted(os.listdir(root))
                if os.path.isdir(os.path.join(root, n, "cache2"))]
    except OSError:
        return []


def categories(env: Mapping[str, str] | None = None, steam_dirs: list[str] | None = None,
               akeno_home: str | None = None, large_roots: list[str] | None = None) -> list[Category]:
    """The categories for this PC. ``env`` is the environment (tests pass their own folders)."""
    env = os.environ if env is None else env
    local, roaming = _env(env, "LOCALAPPDATA"), _env(env, "APPDATA")
    temp = _env(env, "TEMP") or _env(env, "TMP")
    sysroot = _env(env, "SystemRoot")
    profile = _env(env, "USERPROFILE")
    locallow = os.path.join(os.path.dirname(local), "LocalLow") if local else None
    if akeno_home is None:
        from akeno.paths import app_home
        akeno_home = str(app_home())
    out: list[Category] = []

    def add(**kw):
        roots = tuple(r for r in kw.pop("roots") if r)
        if roots:
            out.append(Category(roots=roots, **kw))

    add(id="user_temp", title="Your temporary files", group="temp", roots=[temp],
        reason="Programs write temporary files here and often leave them behind. Files untouched for two days are "
               "no longer in use.", risk=RISK_LOW, min_age_days=2, preselect=True)
    if sysroot:
        add(id="windows_temp", title="Windows temporary files", group="temp", roots=[os.path.join(sysroot, "Temp")],
            reason="Windows and installers leave temporary files here. Most of them can only be removed with "
                   "administrator rights; AKENO removes only the ones your account may remove.",
            risk=RISK_LOW, min_age_days=7, preselect=False)
    add(id="akeno_temp", title="AKENO's leftover temporary files", group="akeno",
        roots=[os.path.join(akeno_home, "cache", "staging")],
        reason="Files AKENO unpacked to prepare an installation. Every installation unpacks again from the verified "
               "package.", risk=RISK_LOW, min_age_days=1, preselect=True)
    add(id="akeno_partial", title="Unfinished AKENO downloads", group="akeno",
        roots=[os.path.join(akeno_home, "cache")], name_pattern=r"\.(part|partial|tmp)$",
        reason="Pieces of downloads that were cancelled or interrupted more than a day ago.", risk=RISK_LOW,
        min_age_days=1, preselect=True, keep_dirs=("components",))
    add(id="akeno_downloads", title="AKENO download cache", group="akeno",
        roots=[os.path.join(akeno_home, "cache", "downloads")],
        reason="Verified copies of the mod packages AKENO downloaded. They let you reinstall, switch versions and "
               "restore without internet.", risk=RISK_MEDIUM,
        side_effect="Reinstalling or switching a mod version downloads the package again.", min_age_days=0)
    add(id="akeno_package_cache", title="AKENO package cache", group="akeno",
        roots=[os.path.join(akeno_home, "cache")], name_pattern=r"^[0-9a-f]{16}-.+", max_depth=0,
        reason="Verified copies of packages AKENO downloaded for its older installers and tools.", risk=RISK_MEDIUM,
        side_effect="Reinstalling a mod downloads its package again.")
    add(id="akeno_logs", title="Old AKENO logs", group="logs", roots=[os.path.join(akeno_home, "logs")],
        name_pattern=r"\.log\.\d+$|\.txt$", reason="Rotated AKENO log files older than two weeks.",
        risk=RISK_LOW, min_age_days=14, preselect=True)
    if local:
        add(id="wer_reports", title="Windows error reports", group="logs",
            roots=[j(local, r"Microsoft\Windows\WER\ReportArchive"),
                   j(local, r"Microsoft\Windows\WER\ReportQueue")],
            reason="Copies of crash reports Windows kept after sending or queuing them. Crash Detective reads the "
                   "event log, not these files.", risk=RISK_LOW, min_age_days=14, preselect=True)
        add(id="crash_dumps", title="Program crash dumps", group="logs", roots=[os.path.join(local, "CrashDumps")],
            reason="Memory images written when a program crashed. Only a developer analysing that crash needs "
                   "them.", risk=RISK_MEDIUM, side_effect="A support team can no longer ask you for these dumps.",
            min_age_days=14)
        for name, (exe, rel) in CHROMIUM.items():
            caches = [j(p, d) for p in chromium_profiles(j(local, rel))
                      for d in CHROMIUM_CACHE_DIRS]
            add(id="browser_" + re.sub(r"\W+", "_", name.lower()), title=f"{name} cache", group="browser",
                roots=caches, reason="Copies of web pages, scripts and images the browser can download again. "
                                     "Cookies, sign-ins, passwords, history and open tabs are not part of it.",
                risk=RISK_LOW, side_effect="Websites load a little slower the first time.", processes=(exe,))
        add(id="browser_firefox", title="Firefox cache", group="browser", roots=firefox_caches(local),
            reason="Copies of web pages, scripts and images Firefox can download again. Cookies, sign-ins, passwords "
                   "and history are in the profile folder, which is not touched.", risk=RISK_LOW,
            side_effect="Websites load a little slower the first time.", processes=("firefox.exe",))
        add(id="launcher_steam_web", title="Steam store and overlay cache", group="launcher",
            roots=[j(local, r"Steam\htmlcache")],
            reason="The Steam client's web browser cache (store, community, overlay pages).", risk=RISK_LOW,
            side_effect="Store pages load a little slower the first time.", processes=("steam.exe", "steamwebhelper.exe"))
        add(id="launcher_epic_web", title="Epic Games Launcher web cache", group="launcher",
            roots=[j(local, r"EpicGamesLauncher\Saved\webcache"),
                   j(local, r"EpicGamesLauncher\Saved\webcache_4147"),
                   j(local, r"EpicGamesLauncher\Saved\webcache_4430")],
            reason="The launcher's web page cache.", risk=RISK_LOW,
            side_effect="The launcher's pages load a little slower the first time.",
            processes=("epicgameslauncher.exe",))
        shader_side = ("Games compile their shaders again: the first minutes in each game can stutter. Only "
                       "useful after a graphics driver problem or to free space.")
        add(id="shader_nvidia", title="NVIDIA shader cache", group="shader",
            roots=[j(local, r"NVIDIA\DXCache"), j(local, r"NVIDIA\GLCache")]
            + ([j(locallow, r"NVIDIA\PerDriverVersion\DXCache"),
                j(locallow, r"NVIDIA\PerDriverVersion\GLCache")] if locallow else []),
            reason="Compiled shaders the NVIDIA driver keeps so games do not compile them again.", risk=RISK_REVIEW,
            side_effect=shader_side, shader=True)
        add(id="shader_amd", title="AMD shader cache", group="shader",
            roots=[j(local, r"AMD\DxCache"), j(local, r"AMD\DxcCache"),
                   j(local, r"AMD\VkCache"), j(local, r"AMD\GLCache")],
            reason="Compiled shaders the AMD driver keeps so games do not compile them again.", risk=RISK_REVIEW,
            side_effect=shader_side, shader=True)
        add(id="shader_intel", title="Intel shader cache", group="shader",
            roots=[j(locallow, r"Intel\ShaderCache")] if locallow else [],
            reason="Compiled shaders the Intel driver keeps.", risk=RISK_REVIEW, side_effect=shader_side, shader=True)
        add(id="shader_directx", title="DirectX shader cache", group="shader",
            roots=[os.path.join(local, "D3DSCache")],
            reason="Compiled shaders Windows' DirectX keeps for programs and games.", risk=RISK_REVIEW,
            side_effect=shader_side, shader=True)
    steam_shader = [os.path.join(s, "steamapps", "shadercache") for s in (steam_dirs or [])]
    add(id="shader_steam", title="Steam shader pre-caches", group="shader", roots=steam_shader,
        reason="Shaders Steam downloaded or compiled for installed games (Vulkan and OpenGL games use them).",
        risk=RISK_REVIEW, side_effect="Steam processes the shaders again before the next start of each game.",
        shader=True, processes=("steam.exe",))
    if large_roots is None and profile:
        large_roots = [os.path.join(profile, n) for n in ("Downloads", "Desktop", "Documents", "Videos")]
    add(id="large_files", title="Large files in your folders", group="large", roots=large_roots or [],
        reason="Files of 1 GB or more in Downloads, Desktop, Documents and Videos. Only you can tell whether you "
               "still need them: open their folder and decide.", risk=RISK_REVIEW, min_size=1 << 30,
        review_only=True, max_files=100_000)
    return out


# Folders Smart Cleaner never removes anything from, whatever a category says (defence in depth).
def forbidden_roots(env: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env
    out = []
    sysroot = _env(env, "SystemRoot")
    if sysroot:
        out += [sysroot]
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        if _env(env, key):
            out.append(env[key])
    return out


ALWAYS_ALLOWED_UNDER_FORBIDDEN = ("Temp",)        # %SystemRoot%\Temp is the only allowed folder inside Windows
PROTECTED_NAMES = re.compile(r"^(desktop\.ini|thumbs\.db|ntuser\..*|pagefile\.sys|hiberfil\.sys|swapfile\.sys)$", re.I)
NEVER_IN_PATH = ("prefetch", "cookies", "login data", "local storage", "sessions", "indexeddb", "web data",
                 "extensions")


def path_allowed(path: str, roots: tuple[str, ...], env: Mapping[str, str] | None = None) -> bool:
    """The final gate before anything is removed: inside one of the category's roots, not inside a forbidden
    folder (except Windows' Temp), not a protected name, no forbidden folder name on the way."""
    from akeno.system.fs import within
    if PROTECTED_NAMES.match(os.path.basename(path)):
        return False
    if not any(within(r, path) and os.path.normcase(os.path.abspath(r)) != os.path.normcase(os.path.abspath(path))
               for r in roots):
        return False
    parts = {p.lower() for p in Path(path).parts}
    if parts & set(NEVER_IN_PATH):
        return False
    env = os.environ if env is None else env
    sysroot = _env(env, "SystemRoot")
    for f in forbidden_roots(env):
        if within(f, path):
            if sysroot and f == sysroot and within(os.path.join(sysroot, "Temp"), path):
                continue
            return False
    return True
