"""Game folder analysis: main executable, architecture, graphics API, upscalers, existing mods, anti-cheat.

Everything here is evidence based. When the evidence is ambiguous the result says so (``UNKNOWN`` API
with the candidates listed, ``NONE_FOUND`` anti-cheat instead of "no anti-cheat").
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from akeno.core.db import now_iso
from akeno.core.models import AntiCheatRisk, DetectedFile, Game, GraphicsApi
from akeno.games import pe
from akeno.games.launchers import RawGame

# Files that are never the game's main executable.
NON_GAME_EXE = re.compile(
    r"^(unins\d*|uninstall.*|setup.*|vc_?redist.*|dxsetup|dxwebsetup|dotnet.*|crashreport.*|crashpad.*|"
    r"crashhandler.*|unitycrashhandler(32|64)?|bugsplat.*|easyanticheat.*|start_protected_game|beservice.*|"
    r"battleye.*|.*launcher.*helper.*|ue4prereqsetup.*|ueprereqsetup.*|epicwebhelper|cefsharp.*|"
    r"quicksfv|touchup|cleanup|activation|register.*|eaanticheat.*|redprelauncher|redlauncher|"
    r"vulkan-?1|vkinfo|nvidia.*|python.*|7z.*|zip|blender|notepad.*|dlss5-feed-host(32|64)|presentmon.*)\.exe$",
    re.I,
)

GRAPHICS_IMPORTS = {
    "d3d12.dll": GraphicsApi.D3D12,
    "d3d11.dll": GraphicsApi.D3D11,
    "vulkan-1.dll": GraphicsApi.VULKAN,
    "d3d9.dll": GraphicsApi.D3D9,
    "opengl32.dll": GraphicsApi.OPENGL,
}

# DLL names the Windows loader resolves from the game folder first, so mods drop proxies under them.
PROXY_NAMES = {"dxgi.dll", "d3d11.dll", "d3d12.dll", "d3d9.dll", "winmm.dll", "version.dll", "dinput8.dll",
               "dbghelp.dll", "wininet.dll", "winhttp.dll", "opengl32.dll", "d3d10.dll", "xinput1_3.dll",
               "xinput1_4.dll", "dsound.dll"}

ANTI_CHEAT = [
    (re.compile(r"^easyanticheat(_eos)?(_setup)?\.(exe|sys|dll)$|^easyanticheat$", re.I), "Easy Anti-Cheat"),
    (re.compile(r"^start_protected_game\.exe$", re.I), "Easy Anti-Cheat"),
    (re.compile(r"^(beservice(_x64)?\.exe|beclient(_x64)?\.dll|battleye)$", re.I), "BattlEye"),
    (re.compile(r"^(pbsvc\.exe|pnkbstra\.exe|pb)$", re.I), "PunkBuster"),
    (re.compile(r"^(gameguard|gameguard\.des|npgg.*)$", re.I), "nProtect GameGuard"),
    (re.compile(r"^(x3\.xem|xigncode.*)$", re.I), "XIGNCODE3"),
    (re.compile(r"^(eaanticheat.*|eaac.*)$", re.I), "EA Javelin Anti-Cheat"),
    (re.compile(r"^(mhyprot.*\.sys)$", re.I), "mhyprot"),
    (re.compile(r"^(equ8.*)$", re.I), "EQU8"),
    (re.compile(r"^(vgc\.exe|vgk\.sys)$", re.I), "Riot Vanguard"),
    (re.compile(r"^(acesdk.*|ace-.*\.sys)$", re.I), "ACE Anti-Cheat"),
]

KNOWN_FILES = {
    "nvngx_dlss.dll": "dlss",
    "nvngx_dlssg.dll": "dlss_fg",
    "nvngx_dlssd.dll": "dlss_rr",
    "nvngx_dlssnr.dll": "nvidia_nr_runtime",
    "nvngx.dll_dlssnr.dll": "optiscaler_nr_forwarder",
    "sl.interposer.dll": "streamline",
    "libxess.dll": "xess",
    "libxess_dx11.dll": "xess",
    "amd_fidelityfx_dx12.dll": "fsr",
    "amd_fidelityfx_vk.dll": "fsr",
    "ffx_fsr2_api_x64.dll": "fsr",
    "ffx_fsr2_api_dx12_x64.dll": "fsr",
    "ffx_fsr3upscaler_x64.dll": "fsr",
    "optiscaler.ini": "optiscaler_config",
    "optiscaler.asi": "optiscaler",
    "reshade.ini": "reshade_config",
    "dlss5-feed.addon64": "dlss5_feeder",
    "dlss5-feed.addon32": "dlss5_feeder",
    "dlss5-feed-helper.addon64": "dlss5_feeder",
    "deep-fried-chicken.addon64": "neural_consumer",
    "dlssg_to_fsr3_amd_is_better.dll": "dlssg_to_fsr3",
    "akeno-install.json": "akeno_marker",
}

NEURAL_CONSUMER_RX = re.compile(r"^(renodx-dlss5.*\.addon64|deep-fried-chicken.*\.addon64)$", re.I)


def classify_proxy(path: str) -> DetectedFile:
    name = os.path.basename(path)
    info = pe.parse(path)
    s = {k: v.lower() for k, v in info.strings.items()}
    desc = " ".join(s.get(k, "") for k in ("ProductName", "FileDescription", "OriginalFilename", "CompanyName"))
    if "optiscaler" in desc:
        kind, detail = "optiscaler", "OptiScaler"
    elif "reshade" in desc:
        kind, detail = "reshade", "ReShade"
    elif "special k" in desc or "specialk" in desc:
        kind, detail = "specialk", "Special K"
    elif "dxvk" in desc:
        kind, detail = "dxvk", "DXVK"
    else:
        hits = pe.contains_bytes(path, [b"Ultimate-ASI-Loader", b"OptiScaler", b"ReShade", b"dxvk", b"SpecialK"])
        if b"Ultimate-ASI-Loader" in hits:
            kind, detail = "asi_loader", "Ultimate ASI Loader"
        elif b"OptiScaler" in hits:
            kind, detail = "optiscaler", "OptiScaler"
        elif b"ReShade" in hits:
            kind, detail = "reshade", "ReShade"
        elif b"SpecialK" in hits:
            kind, detail = "specialk", "Special K"
        elif b"dxvk" in hits:
            kind, detail = "dxvk", "DXVK"
        else:
            kind, detail = "unknown_proxy", info.strings.get("ProductName") or info.strings.get("FileDescription")
    return DetectedFile(name=name, path=path, kind=kind, detail=detail, version=info.file_version)


def scan_dir_mods(directory: str) -> list[DetectedFile]:
    found: list[DetectedFile] = []
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return found
    for fn in entries:
        p = os.path.join(directory, fn)
        low = fn.lower()
        if not os.path.isfile(p):
            continue
        if low in PROXY_NAMES:
            found.append(classify_proxy(p))
        elif low in KNOWN_FILES:
            ver = pe.parse(p).file_version if low.endswith(".dll") else None
            found.append(DetectedFile(name=fn, path=p, kind=KNOWN_FILES[low], version=ver))
        elif NEURAL_CONSUMER_RX.match(fn):
            found.append(DetectedFile(name=fn, path=p, kind="neural_consumer", detail=fn))
        elif low.endswith(".asi"):
            found.append(DetectedFile(name=fn, path=p, kind="asi_plugin"))
        elif low.endswith((".addon64", ".addon32", ".addon")):
            found.append(DetectedFile(name=fn, path=p, kind="reshade_addon"))
        elif low.endswith(".disabled") and low[:-9] in PROXY_NAMES | set(KNOWN_FILES):
            found.append(DetectedFile(name=fn, path=p, kind="disabled_mod"))
    return found


def _iter_exes(root: str, max_depth: int = 5, limit: int = 400):
    base = root.rstrip("\\/").count(os.sep)
    n = 0
    for dirpath, dirs, files in os.walk(root):
        depth = dirpath.count(os.sep) - base
        # Skip folders that never contain the game binary.
        dirs[:] = [d for d in dirs if d.lower() not in {
            "_commonredist", "commonredist", "redist", "redistributables", "directx", "support", "__installer",
            "easyanticheat", "battleye", "engine", "crashreportclient", "prereqs", "_redist", "dotnet",
            "vcredist", "physx", "tools", "mods", "dlss5-feeder", "reshade-shaders"}]
        if depth >= max_depth:
            dirs[:] = []
        for f in files:
            if f.lower().endswith(".exe"):
                yield os.path.join(dirpath, f)
                n += 1
                if n >= limit:
                    return


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def score_exe(path: str, game_name: str, install_dir: str) -> float:
    fn = os.path.basename(path)
    if NON_GAME_EXE.match(fn):
        return -100.0
    score = 0.0
    try:
        size = os.path.getsize(path)
    except OSError:
        return -100.0
    score += min(size / (8 * 1024 * 1024), 12.0)       # big binaries are likelier the game
    stem = _norm(os.path.splitext(fn)[0].replace("-Win64-Shipping", "").replace("-WinGDK-Shipping", ""))
    gname = _norm(game_name)
    if stem and gname and (stem in gname or gname in stem):
        score += 8
    low = path.lower()
    if low.endswith(("-win64-shipping.exe", "-wingdk-shipping.exe")):
        score += 15                                      # Unreal Engine game binary
    if os.sep + "binaries" + os.sep + "win64" + os.sep in low:
        score += 4
    if "launcher" in fn.lower():
        score -= 10
    rel_depth = os.path.relpath(path, install_dir).count(os.sep)
    score -= rel_depth * 0.5
    return score


def choose_exe(raw: RawGame) -> tuple[str | None, list[str]]:
    candidates = []
    if raw.exe_hint:
        hint = raw.exe_hint if os.path.isabs(raw.exe_hint) else os.path.join(raw.install_dir, raw.exe_hint)
        if os.path.isfile(hint):
            candidates.append(hint)
    scored = sorted(((score_exe(p, raw.name, raw.install_dir), p) for p in _iter_exes(raw.install_dir)),
                    reverse=True)
    for s, p in scored:
        if s > -50 and p not in candidates:
            candidates.append(p)
    # A launcher hint that is a tiny Unreal bootstrapper loses to the real Shipping binary.
    if candidates and raw.exe_hint:
        shipping = [p for p in candidates if p.lower().endswith("-shipping.exe")]
        if shipping and not candidates[0].lower().endswith("-shipping.exe"):
            candidates.remove(shipping[0])
            candidates.insert(0, shipping[0])
    return (candidates[0] if candidates else None), candidates[:12]


def detect_api(exe: str) -> tuple[GraphicsApi, list[str], str]:
    info = pe.parse(exe, with_version=False)
    if not info.valid:
        return GraphicsApi.UNKNOWN, [], "unreadable executable"
    apis = []
    for imp in info.imports:
        api = GRAPHICS_IMPORTS.get(imp)
        if api and api.value not in apis:
            apis.append(api.value)
    d = os.path.dirname(exe)
    hints = []
    if os.path.isfile(os.path.join(d, "D3D12", "D3D12Core.dll")):
        hints.append(GraphicsApi.D3D12.value)
    for h in hints:
        if h not in apis:
            apis.append(h)
    if len(apis) == 1:
        return GraphicsApi(apis[0]), apis, "executable imports"
    if not apis:
        return GraphicsApi.UNKNOWN, apis, "no graphics API import found (the game may load it dynamically)"
    return GraphicsApi.UNKNOWN, apis, "several graphics APIs are referenced; select the one the game uses"


def detect_anti_cheat(install_dir: str, exe_dir: str | None) -> tuple[AntiCheatRisk, list[str]]:
    hits: list[str] = []
    for root in {install_dir, exe_dir or install_dir}:
        base = root.rstrip("\\/").count(os.sep)
        for dirpath, dirs, files in os.walk(root):
            if dirpath.count(os.sep) - base >= 3:
                dirs[:] = []
            for name in list(dirs) + files:
                for rx, label in ANTI_CHEAT:
                    if rx.match(name):
                        rel = os.path.relpath(os.path.join(dirpath, name), install_dir)
                        entry = f"{label} ({rel})"
                        if entry not in hits:
                            hits.append(entry)
    if hits:
        return AntiCheatRisk.DETECTED, hits
    return AntiCheatRisk.NONE_FOUND, []


def analyze(raw: RawGame, exe_override: str | None = None, api_override: str | None = None,
            running: list | None = None) -> Game:
    from akeno.core import processes

    exe, candidates = choose_exe(raw)
    notes: list[str] = []
    if exe_override:
        if os.path.isfile(exe_override):
            exe = exe_override
            if exe_override not in candidates:
                candidates.insert(0, exe_override)
        else:
            notes.append("The executable you selected earlier no longer exists; AKENO picked one again.")
    game = Game(id=raw.id, name=raw.name, install_dir=raw.install_dir, launcher=raw.launcher,
                launcher_id=raw.launcher_id, exe_path=exe, exe_candidates=candidates, notes=notes)
    if not os.path.isdir(raw.install_dir):
        game.notes.append("The installation folder no longer exists.")
        game.analyzed_at = now_iso()
        return game
    if not exe:
        game.notes.append("No game executable was found in the installation folder.")
        game.anti_cheat, game.anti_cheat_details = detect_anti_cheat(raw.install_dir, None)
        game.analyzed_at = now_iso()
        return game

    info = pe.parse(exe, with_version=False)
    game.architecture = info.machine if info.valid else None
    api, apis, source = detect_api(exe)
    game.api, game.api_candidates, game.api_source = api, apis, source
    if api_override:
        game.api, game.api_source = GraphicsApi(api_override), "selected by you"

    exe_dir = os.path.dirname(exe)
    mods = scan_dir_mods(exe_dir)
    if os.path.normcase(exe_dir) != os.path.normcase(raw.install_dir):
        # DLSS DLLs often sit next to the exe; some games keep them in the root or a plugins folder.
        for m in scan_dir_mods(raw.install_dir):
            if m.kind in ("dlss", "dlss_fg", "dlss_rr", "streamline", "xess", "fsr"):
                mods.append(m)
    game.existing_mods = mods
    if not any(m.kind == "dlss" for m in mods):
        dlss = _find_file(raw.install_dir, "nvngx_dlss.dll", 6)
        if dlss:
            mods.append(DetectedFile(name="nvngx_dlss.dll", path=dlss, kind="dlss",
                                     version=pe.parse(dlss).file_version))
    dlss_files = [m for m in mods if m.kind == "dlss"]
    game.dlss_version = dlss_files[0].version if dlss_files else None
    ups = []
    for kind, label in (("dlss", "dlss"), ("fsr", "fsr"), ("xess", "xess")):
        if any(m.kind == kind for m in mods):
            ups.append(label)
    game.upscaler_inputs = ups
    game.anti_cheat, game.anti_cheat_details = detect_anti_cheat(raw.install_dir, exe_dir)
    try:
        procs = processes.processes_in_dir(raw.install_dir, running)
        game.is_running = bool(procs)
    except Exception:
        game.is_running = None
    game.analyzed_at = now_iso()
    return game


def _find_file(root: str, name: str, max_depth: int) -> str | None:
    base = root.rstrip("\\/").count(os.sep)
    low = name.lower()
    for dirpath, dirs, files in os.walk(root):
        if dirpath.count(os.sep) - base >= max_depth:
            dirs[:] = []
        for f in files:
            if f.lower() == low:
                return os.path.join(dirpath, f)
    return None


def find_artwork(game: Game) -> str | None:
    """Locally available artwork only: Steam's own library cache, or an icon/image shipped in the game folder."""
    if game.launcher == "steam" and game.launcher_id:
        from akeno.games.launchers import steam_root
        root = steam_root()
        if root:
            cache = Path(root, "appcache", "librarycache")
            for cand in (cache / f"{game.launcher_id}_header.jpg", cache / f"{game.launcher_id}_library_hero.jpg",
                         cache / game.launcher_id / "header.jpg", cache / f"{game.launcher_id}_library_600x900.jpg"):
                if cand.is_file():
                    return str(cand)
            sub = cache / game.launcher_id
            if sub.is_dir():
                for p in sorted(sub.rglob("*.jpg")):
                    if "header" in p.name or "hero" in p.name:
                        return str(p)
    if game.launcher == "xbox":
        for p in Path(game.install_dir).glob("*Logo*.png"):
            return str(p)
    return None
