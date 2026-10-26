"""Minecraft Java Edition: instances and their mods.

Instances: the official launcher (``.minecraft`` and every profile's ``gameDir``), Prism Launcher / MultiMC
(``instance.cfg`` + ``mmc-pack.json``), CurseForge (``minecraftinstance.json``), the Modrinth App (``profile.json``)
and ATLauncher (``instance.json``), plus folders the user adds.

Mods: every ``.jar`` in an instance's ``mods`` folder is opened as a data archive (``archive.py``) and its metadata
read: ``fabric.mod.json``, ``quilt.mod.json``, ``META-INF/mods.toml`` (Forge), ``META-INF/neoforge.mods.toml``
(NeoForge) and ``mcmod.info`` (old Forge). Mod code is never run. A jar without readable metadata is listed as
unknown with the reason.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from typing import Any

from akeno.i18n import msg
from akeno.system import fs
from akeno.system.mods.archive import ArchiveError, MAX_NESTED_COUNT, open_archive
from akeno.system.mods.model import Dependency, ModGroup, ModItem
from akeno.system.tasks import TaskContext, context

BUILTIN = {"minecraft", "java", "fabricloader", "fabric-loader", "fabric", "quilt_loader", "quilt_base", "forge",
           "neoforge", "fml", "javafml", "mcp", "mixinextras"}
LOADER_IDS = {"fabricloader": "fabric", "fabric-loader": "fabric", "quilt_loader": "quilt", "forge": "forge",
              "neoforge": "neoforge"}
MAX_JARS = 2000


@dataclass
class Instance:
    name: str
    root: str                     # the game directory (holds mods/, config/...)
    launcher: str
    mc_version: str | None = None
    loader: str | None = None     # fabric | quilt | forge | neoforge | vanilla | None (unknown)
    loader_version: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def mods_dir(self) -> str:
        return os.path.join(self.root, "mods")

    @property
    def key(self) -> str:
        return "minecraft:" + hashlib.sha256(os.path.normcase(os.path.abspath(self.root)).encode()).hexdigest()[:16]


# -- instances -------------------------------------------------------------------------------------------------------
def parse_version_id(vid: str) -> tuple[str | None, str | None, str | None]:
    """A launcher version id -> (minecraft, loader, loader version).

    "fabric-loader-0.15.11-1.20.1", "quilt-loader-0.26.0-1.20.4", "1.20.1-forge-47.2.0", "neoforge-20.4.237",
    "1.20.1"."""
    vid = (vid or "").strip()
    m = re.fullmatch(r"fabric-loader-([\w.+-]+?)-(\d+\.\d+(?:\.\d+)?[\w.-]*)", vid)
    if m:
        return m.group(2), "fabric", m.group(1)
    m = re.fullmatch(r"quilt-loader-([\w.+-]+?)-(\d+\.\d+(?:\.\d+)?[\w.-]*)", vid)
    if m:
        return m.group(2), "quilt", m.group(1)
    m = re.fullmatch(r"(\d+\.\d+(?:\.\d+)?)-forge-?([\w.]+)", vid)
    if m:
        return m.group(1), "forge", m.group(2)
    m = re.fullmatch(r"neoforge-(\d+)\.(\d+)\.([\w.-]+)", vid)
    if m:
        # NeoForge versions encode Minecraft's: 20.4.x is Minecraft 1.20.4, 21.0.x is 1.21
        major, minor = m.group(1), m.group(2)
        return (f"1.{major}" + (f".{minor}" if minor != "0" else "")), "neoforge", f"{major}.{minor}.{m.group(3)}"
    if re.fullmatch(r"\d+\.\d+(\.\d+)?", vid):
        return vid, "vanilla", None
    return None, None, None


def official_instances(appdata: str) -> list[Instance]:
    base = os.path.join(appdata, ".minecraft")
    out: dict[str, Instance] = {}
    profiles_path = os.path.join(base, "launcher_profiles.json")
    try:
        with open(profiles_path, encoding="utf-8") as f:
            profiles = json.load(f).get("profiles", {})
    except (OSError, ValueError, AttributeError):
        profiles = {}
    for p in profiles.values() if isinstance(profiles, dict) else []:
        if not isinstance(p, dict):
            continue
        gd = p.get("gameDir") or base
        mc, loader, lv = parse_version_id(str(p.get("lastVersionId") or ""))
        key = os.path.normcase(os.path.abspath(gd))
        if key in out:
            continue
        name = p.get("name") or (p.get("lastVersionId") or "Minecraft")
        out[key] = Instance(str(name), gd, "Minecraft Launcher", mc, loader, lv)
    if os.path.isdir(os.path.join(base, "mods")) and os.path.normcase(os.path.abspath(base)) not in out:
        out[os.path.normcase(os.path.abspath(base))] = Instance(".minecraft", base, "Minecraft Launcher")
    return [i for i in out.values() if os.path.isdir(i.mods_dir)]


MMC_LOADERS = {"net.fabricmc.fabric-loader": "fabric", "org.quiltmc.quilt-loader": "quilt",
               "net.minecraftforge": "forge", "net.neoforged": "neoforge"}


def mmc_instances(base: str, launcher: str) -> list[Instance]:
    """Prism Launcher / MultiMC / PolyMC: instances/<name>/{instance.cfg, mmc-pack.json, .minecraft|minecraft}."""
    out = []
    folder = os.path.join(base, "instances")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for n in names:
        inst = os.path.join(folder, n)
        if not os.path.isdir(inst) or fs.is_reparse(inst):
            continue
        game = next((os.path.join(inst, d) for d in (".minecraft", "minecraft") if os.path.isdir(os.path.join(inst, d))),
                    None)
        if game is None:
            continue
        title = n
        try:
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            with open(os.path.join(inst, "instance.cfg"), encoding="utf-8", errors="replace") as f:
                cp.read_string("[i]\n" + f.read())
            title = cp.get("i", "name", fallback=n)
        except (OSError, configparser.Error):
            pass
        mc = loader = lv = None
        try:
            with open(os.path.join(inst, "mmc-pack.json"), encoding="utf-8") as f:
                comps = json.load(f).get("components", [])
            for c in comps:
                uid, ver = c.get("uid"), c.get("version") or c.get("cachedVersion")
                if uid == "net.minecraft":
                    mc = ver
                elif uid in MMC_LOADERS:
                    loader, lv = MMC_LOADERS[uid], ver
        except (OSError, ValueError, AttributeError):
            pass
        out.append(Instance(title, game, launcher, mc, loader or ("vanilla" if mc else None), lv))
    return out


def curseforge_instances(home: str) -> list[Instance]:
    out = []
    folder = os.path.join(home, "curseforge", "minecraft", "Instances")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for n in names:
        inst = os.path.join(folder, n)
        meta = os.path.join(inst, "minecraftinstance.json")
        if not os.path.isfile(meta):
            continue
        mc = loader = lv = None
        title = n
        try:
            with open(meta, encoding="utf-8") as f:
                d = json.load(f)
            title = d.get("name") or n
            mc = d.get("gameVersion")
            base = (d.get("baseModLoader") or {}).get("name") or ""      # "forge-47.2.0", "fabric-0.15.11-1.20.1"
            m = re.match(r"(forge|neoforge|fabric|quilt)-([\w.]+)", base, re.I)
            if m:
                loader, lv = m.group(1).lower(), m.group(2)
        except (OSError, ValueError, AttributeError):
            pass
        out.append(Instance(title, inst, "CurseForge", mc, loader, lv))
    return out


def modrinth_instances(appdata: str) -> list[Instance]:
    out = []
    for app in ("ModrinthApp", "com.modrinth.theseus"):
        folder = os.path.join(appdata, app, "profiles")
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for n in names:
            inst = os.path.join(folder, n)
            if not os.path.isdir(os.path.join(inst, "mods")):
                continue
            mc = loader = lv = None
            title = n
            try:
                with open(os.path.join(inst, "profile.json"), encoding="utf-8") as f:
                    meta = json.load(f).get("metadata", {})
                title = meta.get("name") or n
                mc, loader, lv = meta.get("game_version"), meta.get("loader"), meta.get("loader_version", {}).get("id") \
                    if isinstance(meta.get("loader_version"), dict) else meta.get("loader_version")
            except (OSError, ValueError, AttributeError):
                pass
            out.append(Instance(title, inst, "Modrinth App", mc, loader, lv))
    return out


def atlauncher_instances(appdata: str) -> list[Instance]:
    out = []
    folder = os.path.join(appdata, "ATLauncher", "instances")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for n in names:
        inst = os.path.join(folder, n)
        if not os.path.isdir(os.path.join(inst, "mods")):
            continue
        mc = loader = lv = None
        try:
            with open(os.path.join(inst, "instance.json"), encoding="utf-8") as f:
                d = json.load(f)
            mc = d.get("id")
            lt = (d.get("launcher") or {}).get("loaderVersion") or {}
            loader, lv = (lt.get("type") or "").lower() or None, lt.get("version")
        except (OSError, ValueError, AttributeError):
            pass
        out.append(Instance(n, inst, "ATLauncher", mc, loader, lv))
    return out


def discover(env: dict | None = None, extra: list[str] | None = None) -> list[Instance]:
    env = os.environ if env is None else env
    appdata, home = env.get("APPDATA"), env.get("USERPROFILE") or os.path.expanduser("~")
    found: list[Instance] = []
    if appdata:
        found += official_instances(appdata)
        found += mmc_instances(os.path.join(appdata, "PrismLauncher"), "Prism Launcher")
        found += mmc_instances(os.path.join(appdata, "MultiMC"), "MultiMC")
        found += mmc_instances(os.path.join(appdata, "PolyMC"), "PolyMC")
        found += modrinth_instances(appdata)
        found += atlauncher_instances(appdata)
    if home:
        found += curseforge_instances(home)
    for folder in extra or []:
        root = folder if os.path.isdir(os.path.join(folder, "mods")) else None
        if root:
            found.append(Instance(os.path.basename(folder.rstrip("\\/")) or folder, root, "Added by you"))
    unique: dict[str, Instance] = {}
    for i in found:
        unique.setdefault(os.path.normcase(os.path.abspath(i.root)), i)
    return list(unique.values())


# -- mod metadata ----------------------------------------------------------------------------------------------------
def _lenient_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        # fabric.mod.json files in the wild carry comments, trailing commas and raw newlines in strings
        cleaned = re.sub(r"(?m)^\s*//.*$", "", text)
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        cleaned = cleaned.replace("\r", " ").replace("\n", " ").replace("\t", " ")
        return json.loads(cleaned)


def _deps_fabric(d: dict, kind: str) -> list[Dependency]:
    out = []
    for mid, ver in (d or {}).items() if isinstance(d, dict) else []:
        out.append(Dependency(str(mid), ver if isinstance(ver, (str, list)) else "*", kind, "semver"))
    return out


def read_jar(path: str) -> ModItem:
    """One mod archive's metadata. Errors are kept on the item; nothing is raised."""
    name = os.path.basename(path)
    enabled = not name.lower().endswith(".disabled")
    item = ModItem(re.sub(r"\.jar(\.disabled)?$", "", name, flags=re.I), path, "minecraft_mod", enabled=enabled)
    try:
        st = os.stat(path)
        item.size, item.mtime = st.st_size, st.st_mtime
    except OSError:
        pass
    try:
        with open_archive(path) as a:
            _fill(item, a)
    except ArchiveError as exc:
        item.error = str(exc)
        item.kind = "unknown"
    except (ValueError, KeyError, TypeError, tomllib.TOMLDecodeError, UnicodeError) as exc:
        item.error = f"unreadable metadata: {exc}"
    return item


def _fill(item: ModItem, a) -> None:
    found = []
    if a.has("fabric.mod.json"):
        d = _lenient_json(a.text("fabric.mod.json"))
        found.append("fabric")
        item.mod_id = str(d.get("id") or item.mod_id or "") or None
        item.name = d.get("name") or item.name
        item.version = _clean_version(d.get("version"))
        deps = d.get("depends") or {}
        item.depends += _deps_fabric(deps, "required") + _deps_fabric(d.get("breaks"), "incompatible") + \
            _deps_fabric(d.get("conflicts"), "conflicts") + _deps_fabric(d.get("recommends"), "optional")
        mc = deps.get("minecraft") if isinstance(deps, dict) else None
        item.minecraft = " || ".join(mc) if isinstance(mc, list) else mc
        provides = d.get("provides") or []
        item.provides += [str(x) for x in provides if isinstance(x, str)]
        for j in (d.get("jars") or [])[:MAX_NESTED_COUNT]:
            f = j.get("file") if isinstance(j, dict) else None
            if f and a.has(f):
                try:
                    with a.nested(f) as inner:
                        if inner.has("fabric.mod.json"):
                            nd = _lenient_json(inner.text("fabric.mod.json"))
                            if nd.get("id"):
                                item.nested.append(str(nd["id"]))
                            item.nested += [str(x) for x in nd.get("provides") or [] if isinstance(x, str)]
                except (ArchiveError, ValueError, AttributeError):
                    continue
        item.detection = "fabric.mod.json"
    if a.has("quilt.mod.json"):
        d = _lenient_json(a.text("quilt.mod.json")).get("quilt_loader", {})
        found.append("quilt")
        item.mod_id = item.mod_id or d.get("id")
        item.name = (d.get("metadata") or {}).get("name") or item.name
        item.version = item.version or _clean_version(d.get("version"))
        for dep in d.get("depends") or []:
            if isinstance(dep, str):
                item.depends.append(Dependency(dep, "*", "required"))
            elif isinstance(dep, dict) and dep.get("id"):
                kind = "optional" if dep.get("optional") else "required"
                item.depends.append(Dependency(str(dep["id"]), dep.get("versions", "*"), kind))
                if dep["id"] == "minecraft":
                    v = dep.get("versions", "*")
                    item.minecraft = item.minecraft or (" || ".join(v) if isinstance(v, list) else v)
        item.provides += [p if isinstance(p, str) else p.get("id", "") for p in d.get("provides") or []]
        item.detection = item.detection or "quilt.mod.json"
    for toml_name, loader in (("META-INF/neoforge.mods.toml", "neoforge"), ("META-INF/mods.toml", "forge")):
        if not a.has(toml_name):
            continue
        d = tomllib.loads(a.text(toml_name))
        if loader == "forge" and "neoforge" in found:
            continue
        mods = d.get("mods") or []
        first = mods[0] if mods else {}
        found.append("neoforge" if loader == "neoforge" or _mentions_neoforge(d) else "forge")
        item.mod_id = item.mod_id or first.get("modId")
        item.name = first.get("displayName") or item.name
        ver = first.get("version")
        if ver and "${" in str(ver) and a.has("META-INF/MANIFEST.MF"):
            ver = _manifest_version(a.text("META-INF/MANIFEST.MF")) or None
        item.version = item.version or _clean_version(ver)
        deps = (d.get("dependencies") or {}).get(item.mod_id or "", []) if isinstance(d.get("dependencies"), dict) \
            else []
        for dep in deps if isinstance(deps, list) else []:
            if not isinstance(dep, dict) or not dep.get("modId"):
                continue
            typ = str(dep.get("type", "")).lower()
            if typ in ("incompatible", "discouraged"):
                kind = "incompatible" if typ == "incompatible" else "conflicts"
            elif typ:
                kind = "required" if typ == "required" else "optional"
            else:
                kind = "required" if dep.get("mandatory", True) else "optional"
            item.depends.append(Dependency(str(dep["modId"]), str(dep.get("versionRange", "")), kind, "maven"))
            if dep["modId"] == "minecraft":
                item.minecraft = item.minecraft or dep.get("versionRange")
        item.detection = item.detection or toml_name
    if a.has("mcmod.info") and not found:
        raw = _lenient_json(a.text("mcmod.info"))
        lst = raw.get("modList", []) if isinstance(raw, dict) else raw
        first = lst[0] if lst else {}
        found.append("forge")
        item.mod_id = first.get("modid")
        item.name = first.get("name") or item.name
        item.version = _clean_version(first.get("version"))
        item.minecraft = first.get("mcversion")
        for dep in first.get("requiredMods") or first.get("dependencies") or []:
            if isinstance(dep, str):
                item.depends.append(Dependency(dep.split("@")[0], "*", "required", "maven"))
        item.detection = "mcmod.info"
    item.loaders = list(dict.fromkeys(found))
    item.loader = item.loaders[0] if item.loaders else None
    if not found:
        item.kind = "unknown"
        item.error = str(msg("No Fabric, Quilt, Forge or NeoForge metadata in this archive."))


def _mentions_neoforge(d: dict) -> bool:
    deps = d.get("dependencies") or {}
    for lst in deps.values() if isinstance(deps, dict) else []:
        for dep in lst if isinstance(lst, list) else []:
            if isinstance(dep, dict) and dep.get("modId") == "neoforge":
                return True
    return False


def _manifest_version(text: str) -> str | None:
    for line in text.splitlines():
        if line.lower().startswith("implementation-version:"):
            return line.split(":", 1)[1].strip() or None
    return None


def _clean_version(v) -> str | None:
    if v is None:
        return None
    v = str(v).strip()
    return None if not v or "${" in v else v


def scan_instance(inst: Instance, ctx: TaskContext | None = None) -> ModGroup:
    ctx = context(ctx)
    g = ModGroup(inst.key, msg("Minecraft: {name}", name=inst.name), "minecraft", inst.root, launcher=inst.launcher,
                 loader=inst.loader, loader_version=inst.loader_version, game_version=inst.mc_version)
    try:
        names = sorted(os.listdir(inst.mods_dir))
    except OSError as exc:
        g.status = "error"
        g.errors.append(str(exc))
        return g
    jars = [n for n in names if n.lower().endswith((".jar", ".jar.disabled"))][:MAX_JARS]
    for i, n in enumerate(jars):
        ctx.check()
        if i % 20 == 0:
            ctx.report(msg("Reading mods of {name}", name=inst.name), i, len(jars))
        path = os.path.join(inst.mods_dir, n)
        if fs.is_reparse(path) or not os.path.isfile(path):
            g.errors.append(str(msg("{name}: a link, not read", name=n)))
            continue
        g.mods.append(read_jar(path))
    if g.loader is None:
        loaders = {m.loader for m in g.mods if m.enabled and m.loader}
        if len(loaders) == 1:
            g.loader = loaders.pop()
            g.notes.append(str(msg("Mod loader inferred from the mods (the launcher's metadata was not found).")))
    from akeno.system.mods.conflicts import minecraft_conflicts
    g.findings = minecraft_conflicts(g)
    if any(m.error for m in g.mods):
        g.status = "partial"
    return g
