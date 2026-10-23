"""Bounded read-only FiveM discovery. GTA V/Online are never installation targets."""
from __future__ import annotations

import configparser
import hashlib
import os
import re
from pathlib import Path
from typing import Iterable

from akeno.core.models import Game, GraphicsApi, DetectedFile
from akeno.games.analyzer import detect_anti_cheat, KNOWN_FILES, PROXY_NAMES
from akeno.games.launchers import RawGame
from akeno.games import pe
from akeno.security.downloads import sha256_file
from .models import Edition, FiveMInstallation, InventoryFile
from .paths import is_link

MAX_FILES = 2000
SKIPPED_DIRS = {"reshade-shaders", "shaders", "textures", "cache", "logs", "crashes"}
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_INVENTORY_BYTES = 512 * 1024 * 1024


def _child(directory: Path, name: str) -> Path:
    """Case-insensitive Windows names, also usable in Linux fixtures."""
    try:
        return next((p for p in directory.iterdir() if p.name.casefold() == name.casefold()), directory / name)
    except OSError:
        return directory / name


def _regular(path: Path) -> bool:
    return path.is_file() and not is_link(path)


def _kind(path: Path, plugin: bool) -> str | None:
    name = path.name.lower()
    if name in PROXY_NAMES:
        return "graphics_hook"
    if name in KNOWN_FILES:
        return KNOWN_FILES[name]
    if name.endswith(".asi"):
        return "asi_plugin"
    if name.endswith((".addon", ".addon32", ".addon64")):
        return "neural_consumer" if any(x in name for x in ("dlss5", "deep-fried")) else "reshade_addon"
    if plugin and name.endswith((".dll", ".ini", ".cfg", ".fx")):
        return "plugin_file"
    return None


def _inventory(app: Path, gta: Path | None) -> tuple[tuple[InventoryFile, ...], list[str]]:
    found: dict[Path, InventoryFile] = {}
    errors: list[str] = []
    roots = [(app, False), (_child(app, "plugins"), True), (_child(app, "mods"), True)]
    if gta:
        roots.append((gta, False))  # inspect only; never write to the base game
    count = 0
    total_bytes = 0
    for root, recursive in roots:
        if is_link(root) or (root.exists() and not root.is_dir()):
            errors.append(f"Unsafe inventory directory: {root}")
            continue
        if not root.is_dir():
            continue
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=lambda e: errors.append(str(e))):
            d = Path(directory)
            depth = len(d.relative_to(root).parts)
            linked = [x for x in dirs if is_link(d / x)]
            errors.extend(f"Linked inventory directory: {d / x}" for x in linked)
            # Shader packs (often thousands of .fx files) and caches are content, not graphics hooks.
            dirs[:] = sorted(x for x in dirs if x not in linked and x.casefold() not in SKIPPED_DIRS) \
                if recursive and depth < 3 else []
            for name in sorted(files):
                p = d / name
                kind = _kind(p, recursive)
                if not kind:
                    continue
                count += 1
                if count > MAX_FILES:
                    errors.append("Inventory limit reached; manual inspection required.")
                    return tuple(found.values()), errors
                try:
                    if not _regular(p) or p.stat().st_size > MAX_FILE_BYTES:
                        raise ValueError("linked, non-regular or oversized file")
                    total_bytes += p.stat().st_size
                    if total_bytes > MAX_INVENTORY_BYTES:
                        errors.append("Inventory byte limit reached; manual inspection required.")
                        return tuple(found.values()), errors
                    found[p] = InventoryFile(p, kind, sha256_file(p))
                except (OSError, ValueError) as exc:
                    errors.append(f"Cannot inspect {p}: {exc}")
    return tuple(sorted(found.values(), key=lambda f: str(f.path))), errors


def inspect_installation(path: str | Path) -> FiveMInstallation | None:
    """Accept an install root, FiveM.exe or FiveM.app; absent/ambiguous installs stay unknown."""
    supplied = Path(path).absolute()
    if is_link(supplied):
        return None
    root = supplied.parent if supplied.suffix.lower() == ".exe" else supplied
    if root.name.lower() == "fivem.app":
        root = root.parent
    app, launcher = _child(root, "FiveM.app"), _child(root, "FiveM.exe")
    if not app.is_dir() or not _regular(launcher) or is_link(app):
        return None
    root, app, launcher = root.resolve(), app.resolve(), launcher.resolve()
    errors: list[str] = []
    gta: Path | None = None
    edition = Edition.UNKNOWN
    build = channel = None
    config = _child(app, "CitizenFX.ini")
    config_hash = "missing"
    try:
        if not _regular(config) or config.stat().st_size > 65536:
            raise ValueError("CitizenFX.ini missing, linked or too large")
        config_hash = sha256_file(config)
        ini = configparser.ConfigParser(interpolation=None, strict=True)
        ini.read_string(config.read_text(encoding="utf-8-sig"))
        ivpath = ini.get("Game", "IVPath", fallback="").strip().strip('"')
        build = ini.get("Game", "SavedBuildNumber", fallback=None)
        channel = ini.get("Game", "UpdateChannel", fallback=None)
        if ivpath:
            candidate = Path(os.path.expandvars(ivpath)).expanduser()
            if not candidate.is_absolute():
                raise ValueError("IVPath must be absolute")
            gta = candidate.resolve()
            legacy, enhanced = _child(gta, "GTA5.exe"), _child(gta, "GTA5_Enhanced.exe")
            if _regular(legacy) and not enhanced.exists():
                edition = Edition.LEGACY
            elif _regular(enhanced) and not legacy.exists():
                edition = Edition.ENHANCED
        if edition == Edition.UNKNOWN:
            errors.append("GTA edition cannot be established from IVPath; both or neither executable found.")
    except (OSError, UnicodeError, ValueError, configparser.Error) as exc:
        errors.append(f"Cannot read CitizenFX configuration: {exc}")
    if gta and (root == gta or root.is_relative_to(gta) or gta.is_relative_to(root)):
        errors.append("FiveM and base-game folders overlap; installation is forbidden.")
    inventory, inventory_errors = _inventory(app, gta)
    errors.extend(inventory_errors)
    game_exe = _child(gta, "GTA5.exe") if gta and edition == Edition.LEGACY else None
    if edition == Edition.LEGACY:
        # GTA5.exe's import table is packed; FiveM itself creates the D3D11 device (Cfx rage-graphics-five
        # RenderHooks.cpp), and FiveM Legacy has no other renderer.
        api, candidates, source = GraphicsApi.D3D11, ["d3d11"], "FiveM Legacy renderer (Cfx source)"
    else:
        api, candidates, source = GraphicsApi.UNKNOWN, [], "unknown"
    info = pe.parse(str(game_exe), with_version=False) if game_exe else None
    ac, details = detect_anti_cheat(str(app), str(app))
    # GTA Online anti-cheat in the separate GTA directory is not a FiveM attestation.
    # Any actual detection in FiveM remains a hard block; none_found is not permission.
    identity = hashlib.sha256(str(root).casefold().encode()).hexdigest()[:20]
    title = {Edition.LEGACY: "FiveM Legacy", Edition.ENHANCED: "FiveM (GTA V Enhanced)"}.get(edition, "FiveM")
    game = Game(id=f"fivem:{identity}", name=title, install_dir=str(root),
                launcher="fivem", exe_path=str(launcher), api=api, api_candidates=candidates,
                api_source=source, architecture=info.machine if info and info.valid else None,
                anti_cheat=ac, anti_cheat_details=details,
                notes=["FiveM client only; GTA V and GTA Online files must never be modified.",
                       "SavedBuildNumber is a preference, not evidence of the running build."])
    game.existing_mods = [DetectedFile(f.path.name, str(f.path), f.kind) for f in inventory]
    from akeno.paths import resource_dir
    game.artwork_path = str(Path(resource_dir()) / "assets" / "icons" / "fivem.svg")
    material = [config_hash, sha256_file(launcher), str(gta), edition.value]
    if game_exe and _regular(game_exe):
        material.append(sha256_file(game_exe))
    material.extend(f"{f.path}:{f.sha256}" for f in inventory)
    fingerprint = hashlib.sha256("\n".join(material).encode()).hexdigest()
    return FiveMInstallation(root, app, launcher, gta, edition, game, inventory, tuple(errors),
                             build, channel, fingerprint)


def default_roots() -> list[Path]:
    roots: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "FiveM")
    if os.name == "nt":
        import winreg
        # Standard Windows uninstall metadata; no credential or unrelated registry scans.
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Uninstall\FiveM") as key:
                location, _ = winreg.QueryValueEx(key, "InstallLocation")
                if isinstance(location, str) and location:
                    roots.append(Path(location))
        except OSError:
            pass
    return roots


def discover(extra_roots: Iterable[str | Path] = (), processes: Iterable | None = None) -> list[FiveMInstallation]:
    """Default location + explicit portable locations + read-only running process paths."""
    from akeno.core import processes as process_api
    candidates = [*default_roots(), *(Path(p) for p in extra_roots)]
    for proc in process_api.list_processes() if processes is None else processes:
        if proc.path and re.fullmatch(r"FiveM(?:_b\d+)?(?:_GTAProcess)?\.exe", proc.name, re.I):
            p = Path(proc.path)
            candidates.extend(list(p.parents)[:6])  # includes FiveM.app/data/cache/subprocess layouts
    found = {}
    for candidate in candidates:
        try:
            installation = inspect_installation(candidate)
            if installation:
                found[str(installation.root).casefold()] = installation
        except OSError:
            continue
    return sorted(found.values(), key=lambda i: str(i.root).casefold())


def as_raw_game(installation: FiveMInstallation) -> RawGame:
    g = installation.game
    return RawGame(g.id, g.name, g.install_dir, "fivem", exe_hint=g.exe_path,
                   extra={"edition": installation.edition.value, "app_dir": str(installation.app_dir)})


def classify_process(name: str) -> str:
    """GTA5.exe cannot distinguish story mode from GTA Online; never guess the session."""
    if re.fullmatch(r"FiveM(?:_b\d+)?(?:_GTAProcess)?\.exe", name, re.I):
        return "fivem"
    if name.casefold() == "gta5_enhanced.exe":
        return "gta_v_enhanced_session_unknown"
    if name.casefold() in {"gta5.exe", "playgtav.exe", "gtavlauncher.exe"}:
        return "gta_v_session_unknown"
    return "unknown"
