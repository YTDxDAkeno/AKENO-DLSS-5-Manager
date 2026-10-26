"""Mods of ordinary games and of FiveM.

Adapters (each only reads):
* **graphics hooks** next to the game executable: ReShade, OptiScaler, ASI loaders, Special K, DXVK and unknown
  DLLs with a proxy name, ASI plugins and ReShade add-ons (the classification AKENO's game analysis already uses);
  ASI plugins in ``scripts`` and ``plugins`` folders;
* **BepInEx** (``BepInEx/plugins``, ``BepInEx/patchers``), **MelonLoader** (``Mods``, ``Plugins``);
* **Unreal Engine**: ``Content/Paks/~mods`` and ``LogicMods`` paks, **UE4SS** and its Lua mods;
* a ``mods`` folder in the game root (listed as unknown when its format is not recognised).

A file that AKENO installed and journals (``Installer.managed_files``) is marked *AKENO*; everything else is
*external* and stays read-only for AKENO.

FiveM: ``plugins`` (graphics hooks, ASI), ``mods`` (RPF archives), FiveM's own client files (``citizen``: not
mods) and the server cache (``data/server-cache*``): files streamed by servers you joined. They are counted, never
reported as installed mods, and the list is incomplete by nature.
"""

from __future__ import annotations

import os
from typing import Callable, Iterable

from akeno.i18n import msg
from akeno.system import fs
from akeno.system.mods.conflicts import game_conflicts
from akeno.system.mods.model import SOURCE_AKENO, SOURCE_EXTERNAL, SOURCE_SERVER, ModGroup, ModItem
from akeno.system.tasks import LimitReached, TaskContext, context

HOOK_KINDS = {"reshade", "optiscaler", "asi_loader", "specialk", "dxvk", "unknown_proxy", "asi_plugin",
              "reshade_addon", "optiscaler_config", "reshade_config", "dlss5_feeder", "neural_consumer",
              "optiscaler_nr_forwarder", "dlssg_to_fsr3", "disabled_mod", "akeno_marker"}
KIND_LOADER = {"reshade": "ReShade", "optiscaler": "OptiScaler", "asi_loader": "ASI Loader", "specialk": "Special K",
               "dxvk": "DXVK", "asi_plugin": "ASI Loader", "reshade_addon": "ReShade", "dlss5_feeder": "ReShade",
               "neural_consumer": "ReShade"}
MAX_PER_FOLDER = 3000


def _item(path: str, kind: str, detection: str, managed: set[str], base: str, **kw) -> ModItem:
    try:
        st = os.stat(path)
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = 0, 0.0
    rel = os.path.relpath(path, base).replace("\\", "/").lower() if base else ""
    source = SOURCE_AKENO if rel in managed or os.path.basename(path).lower() in managed else SOURCE_EXTERNAL
    name = kw.pop("name", None) or os.path.splitext(os.path.basename(path))[0]
    enabled = not path.lower().endswith((".disabled", ".akeno-disabled"))
    return ModItem(name, path, kind, source, detection, size=size, mtime=mtime, enabled=enabled, **kw)


def _version_info(path: str) -> dict[str, str]:
    try:
        from akeno.games import pe
        info = pe.parse(path)
        return dict(info.strings) | ({"FileVersion": info.file_version} if info.file_version else {})
    except Exception:                                  # noqa: BLE001 (unreadable files stay unknown)
        return {}


def _files(folder: str, exts: tuple[str, ...], ctx, depth: int = 3) -> list[str]:
    if not os.path.isdir(folder) or fs.is_reparse(folder):
        return []
    out = []
    try:
        for e in fs.walk(folder, ctx, max_files=MAX_PER_FOLDER, max_depth=depth):
            if e.path.lower().endswith(exts):
                out.append(e.path)
    except LimitReached:
        pass
    return sorted(out)


def _child(folder: str, name: str) -> str:
    try:
        for n in os.listdir(folder):
            if n.lower() == name.lower():
                return os.path.join(folder, n)
    except OSError:
        pass
    return os.path.join(folder, name)


def scan_game_folder(exe_dir: str, root: str, managed: set[str], ctx: TaskContext | None = None) -> list[ModItem]:
    """Mods of one game: ``exe_dir`` holds the executable, ``root`` is the install folder."""
    ctx = context(ctx)
    from akeno.games.analyzer import scan_dir_mods
    items: list[ModItem] = []
    seen: set[str] = set()

    def add(it: ModItem):
        key = os.path.normcase(it.path)
        if key not in seen:
            seen.add(key)
            items.append(it)

    if exe_dir and os.path.isdir(exe_dir):
        for d in scan_dir_mods(exe_dir):
            if d.kind not in HOOK_KINDS:
                continue                           # DLSS/FSR/XeSS runtimes the game ships are not mods
            add(_item(d.path, d.kind, msg("file name and version info"), managed, exe_dir,
                      name=d.detail if d.kind not in ("asi_plugin", "reshade_addon", "disabled_mod", "akeno_marker")
                      and d.detail else None, version=d.version, loader=KIND_LOADER.get(d.kind)))
        for sub in ("scripts", "plugins"):
            for p in _files(_child(exe_dir, sub), (".asi",), ctx, 2):
                add(_item(p, "asi_plugin", msg("ASI plugin in {folder}", folder=sub), managed, exe_dir,
                          loader="ASI Loader"))
    ctx.check()
    for base in {root, exe_dir} - {None, ""}:
        bep = _child(base, "BepInEx")
        if os.path.isdir(bep):
            core = next((p for p in _files(_child(bep, "core"), (".dll",), ctx, 1)
                         if os.path.basename(p).lower() in ("bepinex.dll", "bepinex.core.dll")), None)
            lv = _version_info(core).get("FileVersion") if core else None
            for folder, kind in (("plugins", "bepinex_plugin"), ("patchers", "bepinex_patcher")):
                for p in _files(_child(bep, folder), (".dll",), ctx, 4):
                    vi = _version_info(p)
                    add(_item(p, kind, msg("BepInEx {folder} folder", folder=folder), managed, base,
                              name=vi.get("ProductName") or vi.get("FileDescription"), version=vi.get("FileVersion"),
                              loader="BepInEx", extra={"loader_version": lv}))
        mel = _child(base, "MelonLoader")
        if os.path.isdir(mel):
            for folder, kind in (("Mods", "melonloader_mod"), ("Plugins", "melonloader_plugin")):
                for p in _files(_child(base, folder), (".dll",), ctx, 1):
                    vi = _version_info(p)
                    add(_item(p, kind, msg("MelonLoader {folder} folder", folder=folder), managed, base,
                              name=vi.get("ProductName"), version=vi.get("FileVersion"), loader="MelonLoader"))
        ctx.check()
        for paks in _unreal_paks(base):
            for sub in ("~mods", "LogicMods"):
                for p in _files(_child(paks, sub), (".pak", ".utoc", ".ucas"), ctx, 3):
                    if p.lower().endswith((".utoc", ".ucas")):
                        continue                    # companions of a .pak, listed once
                    add(_item(p, "ue_pak", msg("Unreal Engine {folder} folder", folder=sub), managed, base,
                              loader="Unreal Engine (pak)"))
        for ue4ss in _ue4ss_dirs(base):
            for mod in sorted(os.listdir(_child(ue4ss, "Mods"))) if os.path.isdir(_child(ue4ss, "Mods")) else []:
                path = os.path.join(_child(ue4ss, "Mods"), mod)
                if os.path.isdir(path) and not fs.is_reparse(path):
                    enabled = os.path.isfile(os.path.join(path, "enabled.txt"))
                    it = _item(path, "ue4ss_mod", msg("UE4SS Mods folder"), managed, base, loader="UE4SS")
                    it.enabled = enabled                 # UE4SS loads a Lua mod only with enabled.txt
                    add(it)
        generic = _child(base, "mods")
        if os.path.isdir(generic) and not any(m.path.lower().startswith(generic.lower()) for m in items):
            for p in _files(generic, ("",), ctx, 2)[:500]:
                add(_item(p, "unknown", msg("mods folder (format not recognised)"), managed, base))
    return items


def _unreal_paks(base: str) -> list[str]:
    """<root>/<Project>/Content/Paks folders (one level of project folders)."""
    out = []
    try:
        for n in os.listdir(base):
            paks = os.path.join(base, n, "Content", "Paks")
            if os.path.isdir(paks):
                out.append(paks)
    except OSError:
        pass
    return out


def _ue4ss_dirs(base: str) -> list[str]:
    out = []
    try:
        for n in os.listdir(base):
            for rel in (("Binaries", "Win64", "ue4ss"), ("Binaries", "Win64"), ("Binaries", "WinGDK")):
                d = os.path.join(base, n, *rel)
                if os.path.isdir(os.path.join(d, "Mods")) and (os.path.isfile(os.path.join(d, "UE4SS.dll")) or
                                                               os.path.isfile(os.path.join(d, "ue4ss.dll"))):
                    out.append(d)
    except OSError:
        pass
    return out


def scan_game(game, managed: set[str], ctx: TaskContext | None = None) -> ModGroup:
    g = ModGroup("game:" + game.id, game.name, "game", game.install_dir, game_id=game.id,
                 launcher=getattr(game, "launcher", ""))
    try:
        g.mods = scan_game_folder(game.target_dir or game.install_dir, game.install_dir, managed, ctx)
    except OSError as exc:
        g.status = "error"
        g.errors.append(str(exc))
    loaders = sorted({m.loader for m in g.mods if m.loader})
    g.loader = ", ".join(loaders) or None
    g.findings = game_conflicts(g)
    return g


# -- FiveM ---------------------------------------------------------------------------------------------------------
def scan_fivem(installation, game_id: str, managed_plugins: set[str], ctx: TaskContext | None = None) -> ModGroup:
    """``managed_plugins``: AKENO's journalled files, relative to FiveM.app/plugins (its install target)."""
    ctx = context(ctx)
    app = str(installation.app_dir)
    g = ModGroup("game:" + game_id, msg("FiveM"), "fivem", str(installation.root), game_id=game_id, launcher="fivem",
                 game_version=getattr(installation.edition, "value", None))
    plugins = _child(app, "plugins")
    g.mods += scan_game_folder(plugins, "", managed_plugins, ctx) if os.path.isdir(plugins) else []
    for p in _files(_child(app, "mods"), (".rpf",), ctx, 2):
        g.mods.append(_item(p, "rpf", msg("FiveM mods folder (RPF archive)"), set(), app, loader="FiveM mods"))
    citizen = _child(app, "citizen")
    if os.path.isdir(citizen):
        g.notes.append(str(msg("FiveM's own client files (citizen) are part of FiveM, not mods; they are not listed.")))
    server = []
    try:
        data = _child(app, "data")
        server = [os.path.join(data, n) for n in os.listdir(data) if n.lower().startswith("server-cache")]
    except OSError:
        pass
    total = files = 0
    for folder in server:
        size, n = fs.dir_size(folder, ctx, max_files=200_000)
        total += size
        files += n
    if server:
        g.mods.append(ModItem(str(msg("Server cache ({n} files)", n=files)), server[0], "server_cache", SOURCE_SERVER,
                              str(msg("data/server-cache folders")), size=total,
                              extra={"folders": server, "files": files}))
        g.notes.append(str(msg("Servers stream their resources into the server cache while you play. These are not "
                               "mods installed on this PC, the cache keeps only what was streamed, and AKENO cannot "
                               "tell which server sent what.")))
    for e in installation.errors:
        g.errors.append(str(e))
    g.loader = ", ".join(sorted({m.loader for m in g.mods if m.loader})) or None
    g.findings = game_conflicts(g)
    return g
