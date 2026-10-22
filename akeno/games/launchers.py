"""Installed-game discovery for Steam, Epic, GOG, Ubisoft Connect, EA app, Xbox/Microsoft Store and manual folders.

Each scanner returns lightweight ``RawGame`` records from the launcher's own manifests. Executable
selection and graphics analysis happen later in :mod:`akeno.games.analyzer`. Scanners take explicit
roots so tests can point them at fixture directories; on Windows the roots come from the registry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import string
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from akeno.games import vdf
from akeno.paths import is_windows

log = logging.getLogger(__name__)


@dataclass
class RawGame:
    id: str
    name: str
    install_dir: str
    launcher: str
    launcher_id: str | None = None
    exe_hint: str | None = None          # launcher-declared executable (absolute or relative)
    extra: dict = field(default_factory=dict)


# Steam ships these as "apps" in steamapps but they are tools, runtimes or redistributables, not games.
STEAM_NON_GAMES = {"228980", "1070560", "1391110", "1628350", "1493710", "1826330", "250820", "431960",
                   "1905180", "2180100", "1161040"}
STEAM_NON_GAME_NAME = re.compile(r"^(Steamworks Common Redistributables|Proton|Steam Linux Runtime|SteamVR|"
                                 r"Wallpaper Engine)", re.I)


def _reg_value(root, path: str, name: str):
    if not is_windows():
        return None
    import winreg
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(root, path, 0, winreg.KEY_READ | view) as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            continue
    return None


def _reg_subkeys(root, path: str) -> list[tuple[str, dict]]:
    if not is_windows():
        return []
    import winreg
    out: dict[str, dict] = {}
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(root, path, 0, winreg.KEY_READ | view) as k:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    vals = {}
                    try:
                        with winreg.OpenKey(k, name) as sk:
                            j = 0
                            while True:
                                try:
                                    vn, vv, _ = winreg.EnumValue(sk, j)
                                except OSError:
                                    break
                                vals[vn] = vv
                                j += 1
                    except OSError:
                        pass
                    out.setdefault(name, vals)
        except OSError:
            continue
    return list(out.items())


# --- Steam ------------------------------------------------------------------------------------------

def steam_root() -> str | None:
    if not is_windows():
        return None
    import winreg
    p = _reg_value(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath") or \
        _reg_value(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")
    return os.path.normpath(p) if p else None


def steam_libraries(root: str) -> list[str]:
    libs = [os.path.join(root, "steamapps")]
    lf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    try:
        data = vdf.loads(Path(lf).read_text(encoding="utf-8", errors="replace"))
    except (OSError, vdf.VdfError) as exc:
        log.info("steam libraryfolders.vdf unreadable: %s", exc)
        return [p for p in libs if os.path.isdir(p)]
    folders = vdf.get_ci(data, "libraryfolders") or vdf.get_ci(data, "LibraryFolders") or {}
    for key, val in folders.items():
        path = None
        if isinstance(val, dict):
            path = vdf.get_ci(val, "path")
        elif isinstance(val, str) and key.isdigit():
            path = val   # old format: "1" "D:\\SteamLibrary"
        if path:
            libs.append(os.path.join(os.path.normpath(path), "steamapps"))
    seen, out = set(), []
    for p in libs:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen and os.path.isdir(p):
            seen.add(k)
            out.append(p)
    return out


def scan_steam(root: str | None = None) -> list[RawGame]:
    root = root or steam_root()
    if not root or not os.path.isdir(root):
        return []
    games = []
    for lib in steam_libraries(root):
        try:
            entries = os.listdir(lib)
        except OSError:
            continue
        for fn in entries:
            if not (fn.startswith("appmanifest_") and fn.endswith(".acf")):
                continue
            try:
                data = vdf.loads(Path(lib, fn).read_text(encoding="utf-8", errors="replace"))
            except (OSError, vdf.VdfError) as exc:
                log.info("skipping %s: %s", fn, exc)
                continue
            st = vdf.get_ci(data, "AppState") or {}
            appid = str(vdf.get_ci(st, "appid") or "")
            name = vdf.get_ci(st, "name") or f"Steam app {appid}"
            installdir = vdf.get_ci(st, "installdir")
            if not appid or not installdir or appid in STEAM_NON_GAMES or STEAM_NON_GAME_NAME.match(name):
                continue
            path = os.path.join(lib, "common", installdir)
            if not os.path.isdir(path):
                continue
            games.append(RawGame(id=f"steam:{appid}", name=name, install_dir=path, launcher="steam",
                                 launcher_id=appid))
    return games


# --- Epic ---------------------------------------------------------------------------------------------

def epic_manifest_dir() -> str | None:
    if not is_windows():
        return None
    base = os.environ.get("ProgramData", r"C:\ProgramData")
    p = os.path.join(base, "Epic", "EpicGamesLauncher", "Data", "Manifests")
    return p if os.path.isdir(p) else None


def scan_epic(manifest_dir: str | None = None) -> list[RawGame]:
    d = manifest_dir or epic_manifest_dir()
    if not d or not os.path.isdir(d):
        return []
    games = []
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith(".item"):
            continue
        try:
            m = json.loads(Path(d, fn).read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError) as exc:
            log.info("skipping epic manifest %s: %s", fn, exc)
            continue
        cats = [c.lower() for c in m.get("AppCategories", [])]
        if cats and "games" not in cats:
            continue
        if m.get("bIsIncompleteInstall"):
            continue
        loc = m.get("InstallLocation")
        if not loc or not os.path.isdir(loc):
            continue
        app = m.get("AppName") or m.get("CatalogItemId") or fn
        exe = m.get("LaunchExecutable")
        games.append(RawGame(id=f"epic:{app}", name=m.get("DisplayName") or app, install_dir=loc, launcher="epic",
                             launcher_id=app, exe_hint=os.path.join(loc, exe) if exe else None))
    return games


# --- GOG ----------------------------------------------------------------------------------------------

def scan_gog(entries: list[tuple[str, dict]] | None = None) -> list[RawGame]:
    if entries is None:
        if not is_windows():
            return []
        import winreg
        entries = _reg_subkeys(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\GOG.com\Games")
    games = []
    for gid, vals in entries:
        path = vals.get("path") or vals.get("PATH")
        if not path or not os.path.isdir(path):
            continue
        if str(vals.get("dependsOn", "")).strip():
            continue  # DLC
        exe = vals.get("exe") or vals.get("EXE")
        name = vals.get("gameName") or vals.get("GAMENAME") or gid
        games.append(RawGame(id=f"gog:{gid}", name=name, install_dir=path, launcher="gog", launcher_id=gid,
                             exe_hint=exe))
    return games


# --- Ubisoft Connect ----------------------------------------------------------------------------------

def scan_ubisoft(entries: list[tuple[str, dict]] | None = None) -> list[RawGame]:
    if entries is None:
        if not is_windows():
            return []
        import winreg
        entries = _reg_subkeys(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Ubisoft\Launcher\Installs")
    games = []
    for gid, vals in entries:
        path = vals.get("InstallDir")
        if not path or not os.path.isdir(path):
            continue
        path = os.path.normpath(path)
        games.append(RawGame(id=f"ubisoft:{gid}", name=os.path.basename(path.rstrip("\\/")) or gid,
                             install_dir=path, launcher="ubisoft", launcher_id=gid))
    return games


# --- EA app -------------------------------------------------------------------------------------------

def scan_ea(dirs: list[str] | None = None) -> list[RawGame]:
    """EA app games carry ``__Installer/installerdata.xml``; the install roots come from the registry."""
    if dirs is None:
        dirs = []
        if is_windows():
            import winreg
            for _, vals in _reg_subkeys(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EA Games"):
                p = vals.get("Install Dir") or vals.get("InstallDir")
                if p:
                    dirs.append(p)
            for _, vals in _reg_subkeys(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Electronic Arts"):
                p = vals.get("Install Dir") or vals.get("InstallDir")
                if p:
                    dirs.append(p)
    games, seen = [], set()
    for p in dirs:
        p = os.path.normpath(p)
        key = os.path.normcase(p)
        if key in seen or not os.path.isdir(p):
            continue
        seen.add(key)
        name, cid = os.path.basename(p), None
        xml_path = os.path.join(p, "__Installer", "installerdata.xml")
        if os.path.isfile(xml_path):
            try:
                root = ET.parse(xml_path).getroot()
                title = root.find(".//gameTitle")
                if title is None:
                    title = root.find(".//title")
                if title is not None and (title.text or "").strip():
                    name = title.text.strip()
                cid_el = root.find(".//contentID")
                cid = cid_el.text.strip() if cid_el is not None and cid_el.text else None
            except ET.ParseError:
                pass
        gid = cid or hashlib.sha1(key.encode()).hexdigest()[:12]
        games.append(RawGame(id=f"ea:{gid}", name=name, install_dir=p, launcher="ea", launcher_id=cid))
    return games


# --- Xbox / Microsoft Store ---------------------------------------------------------------------------

def xbox_roots() -> list[str]:
    roots = []
    if not is_windows():
        return roots
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        marker = os.path.join(drive, ".GamingRoot")
        try:
            if not os.path.isfile(marker):
                continue
            data = Path(marker).read_bytes()
        except OSError:
            continue
        # .GamingRoot: "RGBX" magic, uint32 count, then UTF-16 NUL-terminated relative folder names.
        rel = data[8:].decode("utf-16-le", "ignore").split("\0")
        for r in rel:
            if r.strip():
                roots.append(os.path.join(drive, r.strip()))
    return roots


def scan_xbox(roots: list[str] | None = None) -> list[RawGame]:
    roots = xbox_roots() if roots is None else roots
    games = []
    for root in roots:
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for folder in entries:
            base = os.path.join(root, folder)
            content = os.path.join(base, "Content")
            cfg = os.path.join(content, "MicrosoftGame.config")
            if not os.path.isfile(cfg):
                continue
            name, exe = folder, None
            try:
                tree = ET.parse(cfg).getroot()
                shell = tree.find(".//ShellVisuals")
                if shell is not None and shell.get("DefaultDisplayName"):
                    name = shell.get("DefaultDisplayName")
                e = tree.find(".//Executable")
                if e is not None and e.get("Name"):
                    exe = os.path.join(content, e.get("Name"))
            except ET.ParseError:
                pass
            if name.startswith("ms-resource:"):
                name = folder
            games.append(RawGame(id=f"xbox:{folder}", name=name, install_dir=content, launcher="xbox",
                                 launcher_id=folder, exe_hint=exe))
    return games


# --- Manual -------------------------------------------------------------------------------------------

def manual_id(path: str) -> str:
    return "manual:" + hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode()).hexdigest()[:12]


class ManualGameError(ValueError):
    pass


def manual_game(path: str, name: str | None = None) -> RawGame:
    """A user-picked game folder or executable. Raises ``ManualGameError`` with a readable reason."""
    if not path:
        raise ManualGameError("No path given.")
    p = os.path.abspath(path)
    exe = None
    if os.path.isfile(p):
        if not p.lower().endswith(".exe"):
            raise ManualGameError("The selected file is not a Windows executable (.exe).")
        exe, p = p, os.path.dirname(p)
    if not os.path.isdir(p):
        raise ManualGameError(f"The folder does not exist: {p}")
    has_exe = exe is not None
    if not has_exe:
        for _dirpath, _dirs, files in _walk_limited(p, 4):
            if any(f.lower().endswith(".exe") for f in files):
                has_exe = True
                break
    if not has_exe:
        raise ManualGameError("No executable (.exe) was found in this folder or its subfolders.")
    return RawGame(id=manual_id(exe or p), name=name or os.path.basename(p.rstrip("\\/")) or p,
                   install_dir=p, launcher="manual", exe_hint=exe)


def _walk_limited(root: str, depth: int):
    base = root.rstrip("\\/").count(os.sep)
    for dirpath, dirs, files in os.walk(root):
        yield dirpath, dirs, files
        if dirpath.count(os.sep) - base >= depth:
            dirs[:] = []


def scan_battlenet(entries=None):
    """Bounded uninstall-registry inventory. No recursive drive scan or product.db guesses."""
    if entries is None:
        if not is_windows():return []
        import winreg
        entries=[]
        for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
            entries.extend(_reg_subkeys(hive,r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'))
    games=[];seen=set()
    for key,values in entries:
        publisher=str(values.get('Publisher','')).casefold();name=str(values.get('DisplayName',''))
        path=values.get('InstallLocation')
        if 'blizzard' not in publisher or not path or not os.path.isdir(path):continue
        if name.casefold() in ('battle.net','blizzard battle.net','battle.net setup'):continue
        root=os.path.normcase(os.path.abspath(path))
        if root in seen:continue
        seen.add(root)
        identity=hashlib.sha256(root.encode()).hexdigest()[:16]
        games.append(RawGame(id='battlenet:'+identity,name=name or str(key),install_dir=str(path),launcher='battlenet',launcher_id=str(key)))
    return games


def scan_all(manual_dirs: list[str] | None = None) -> tuple[list[RawGame], list[str]]:
    """Run every scanner; returns games and per-launcher error messages."""
    out: list[RawGame] = []
    errors: list[str] = []
    for label, fn in (("Steam", scan_steam), ("Epic Games", scan_epic), ("GOG", scan_gog),
                      ("Ubisoft Connect", scan_ubisoft), ("EA app", scan_ea), ("Xbox", scan_xbox), ("Battle.net", scan_battlenet)):
        try:
            out.extend(fn())
        except Exception as exc:  # one broken launcher must not hide the others
            log.exception("%s scan failed", label)
            errors.append(f"{label}: {exc}")
    for d in manual_dirs or []:
        try:
            out.append(manual_game(d))
        except ManualGameError as exc:
            errors.append(f"{d}: {exc}")
    seen, unique = set(), []
    for g in out:
        if g.id not in seen:
            seen.add(g.id)
            unique.append(g)
    return unique, errors
