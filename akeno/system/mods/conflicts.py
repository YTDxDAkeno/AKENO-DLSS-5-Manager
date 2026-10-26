"""Mod Conflict Detector: only conflicts the metadata or the file layout proves.

Minecraft (per instance):
* the same mod id in two enabled archives;
* a mod for another loader than the instance's (Quilt loads Fabric mods; Sinytra Connector loads Fabric mods on
  NeoForge/Forge, so then it is only a note);
* a required dependency that no enabled mod (or bundled jar-in-jar, or ``provides``) supplies;
* a Minecraft or loader version requirement the instance does not meet (an unparseable requirement is not a
  conflict);
* mods that declare each other incompatible (``breaks`` / ``incompatible``) or discouraged (``conflicts``);
* archives that cannot be read.

Games (graphics hooks):
* two different products in the same proxy DLL role are impossible (one file name), but two loaders of the same
  kind are known to clash: more than one ReShade, more than one ASI loader, ReShade loaded both as a proxy and
  through OptiScaler's own ReShade support is not judged (supported setup).
"""

from __future__ import annotations

from collections import defaultdict

from akeno.i18n import msg
from akeno.system.model import Finding, Level
from akeno.system.mods import versions
from akeno.system.mods.minecraft import BUILTIN, LOADER_IDS
from akeno.system.mods.model import ModGroup, ModItem

FABRIC_FAMILY = {"fabric", "quilt"}
CONNECTORS = {"connector", "connectormod"}


def minecraft_conflicts(g: ModGroup) -> list[Finding]:
    out: list[Finding] = []
    mods = [m for m in g.mods if m.enabled]
    by_id: dict[str, list[ModItem]] = defaultdict(list)
    for m in mods:
        if m.mod_id:
            by_id[m.mod_id.lower()].append(m)
    present = set(by_id)
    for m in mods:
        present |= {x.lower() for x in m.provides + m.nested if x}
    for mid, items in sorted(by_id.items()):
        if len(items) > 1:
            f = Finding(f"mods.duplicate.{mid}", "mods", Level.PROBLEM, msg("Mod id {id} is installed twice", id=mid),
                        msg("{n} archives declare the same mod id. Minecraft refuses to start or loads only one.",
                            n=len(items)),
                        msg("Keep one of them (usually the newer version) and remove or disable the others."))
            for it in items:
                f.measured(msg("{file}: version {v}", file=it.path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1],
                               v=it.version or "?"), it.detection)
            out.append(f)
    loader = (g.loader or "").lower() or None
    connector = bool(present & CONNECTORS)
    if loader and loader != "vanilla":
        for m in mods:
            if not m.loaders or loader in m.loaders:
                continue
            if loader == "quilt" and "fabric" in m.loaders:
                continue
            if connector and loader in ("neoforge", "forge") and "fabric" in m.loaders:
                continue
            if loader == "neoforge" and "forge" in m.loaders and _old_neoforge(g.game_version):
                continue                      # NeoForge for 1.20.1 still loads Forge mods
            f = Finding(f"mods.loader.{m.mod_id or m.name}", "mods", Level.PROBLEM,
                        msg("{mod} is made for {wants}", mod=m.name, wants="/".join(m.loaders)),
                        msg("This instance runs {loader}. The mod declares only {wants}.", loader=loader,
                            wants=", ".join(m.loaders)),
                        msg("Use the {loader} version of this mod, or remove it.", loader=loader))
            f.measured(msg("Metadata: {file}", file=m.detection), "archive")
            out.append(f)
    for m in mods:
        for d in m.depends:
            did = d.id.lower()
            if d.kind == "required":
                if did in BUILTIN or did in LOADER_IDS:
                    _check_platform(out, g, m, d)
                    continue
                if did not in present:
                    f = Finding(f"mods.missing.{m.mod_id or m.name}.{did}", "mods", Level.PROBLEM,
                                msg("{mod} needs {dep}, which is missing", mod=m.name, dep=d.id),
                                msg("{mod} declares {dep} ({ver}) as required; no installed mod provides it.",
                                    mod=m.name, dep=d.id, ver=_fmt(d.versions)),
                                msg("Install {dep} in a version that matches, or remove {mod}.", dep=d.id, mod=m.name))
                    f.measured(msg("Required by the metadata in {file}", file=m.detection), "archive")
                    out.append(f)
                    continue
                provider = by_id.get(did, [None])[0]
                if provider is not None and provider.version:
                    ok = versions.matches(d.versions, provider.version, d.notation)
                    if ok is False:
                        f = Finding(f"mods.depversion.{m.mod_id or m.name}.{did}", "mods", Level.PROBLEM,
                                    msg("{mod} needs another version of {dep}", mod=m.name, dep=d.id),
                                    msg("Required: {want}. Installed: {have}.", want=_fmt(d.versions),
                                        have=provider.version),
                                    msg("Install a matching version of {dep}.", dep=d.id))
                        f.measured(msg("{dep} {have} does not match {want}", dep=d.id, have=provider.version,
                                       want=_fmt(d.versions)), m.detection)
                        out.append(f)
            elif d.kind in ("incompatible", "conflicts") and did in present:
                other = by_id.get(did, [None])[0]
                if other is not None and other.version and d.versions not in ("*", "", None):
                    if versions.matches(d.versions, other.version, d.notation) is False:
                        continue
                level = Level.PROBLEM if d.kind == "incompatible" else Level.WARN
                f = Finding(f"mods.incompatible.{m.mod_id or m.name}.{did}", "mods", level,
                            msg("{mod} declares {dep} incompatible", mod=m.name, dep=d.id) if d.kind == "incompatible"
                            else msg("{mod} warns against {dep}", mod=m.name, dep=d.id),
                            msg("Both are installed. The mod's author lists them as not working together."),
                            msg("Remove one of the two."))
                f.measured(msg("{kind} in {file}", kind=d.kind, file=m.detection), "archive")
                out.append(f)
    for m in g.mods:
        if m.error and m.kind == "unknown" and m.path.lower().endswith((".jar", ".jar.disabled")):
            f = Finding(f"mods.unreadable.{m.name}", "mods", Level.WARN, msg("{file} could not be read", file=m.name),
                        msg("Reason: {why}", why=m.error),
                        msg("A damaged or incomplete download is common; download the mod again from its source."))
            out.append(f)
    return out


def _old_neoforge(mc: str | None) -> bool:
    return bool(mc) and versions.compare(mc, "1.20.1") == 0


def _check_platform(out: list[Finding], g: ModGroup, m: ModItem, d) -> None:
    did = d.id.lower()
    if did == "minecraft" and g.game_version:
        ok = versions.matches(d.versions, g.game_version, d.notation)
        if ok is False:
            f = Finding(f"mods.mcversion.{m.mod_id or m.name}", "mods", Level.PROBLEM,
                        msg("{mod} is made for another Minecraft version", mod=m.name),
                        msg("It needs Minecraft {want}; this instance is {have}.", want=_fmt(d.versions),
                            have=g.game_version),
                        msg("Download the mod's version for Minecraft {have}.", have=g.game_version))
            f.measured(msg("minecraft {want} in {file}", want=_fmt(d.versions), file=m.detection), "archive")
            out.append(f)
    elif did in LOADER_IDS and g.loader == LOADER_IDS[did] and g.loader_version:
        ok = versions.matches(d.versions, g.loader_version, d.notation)
        if ok is False:
            f = Finding(f"mods.loaderversion.{m.mod_id or m.name}", "mods", Level.PROBLEM,
                        msg("{mod} needs a newer or older {loader}", mod=m.name, loader=g.loader),
                        msg("It needs {loader} {want}; the instance has {have}.", loader=g.loader,
                            want=_fmt(d.versions), have=g.loader_version),
                        msg("Change the instance's {loader} version in your launcher, or use another version of the "
                            "mod.", loader=g.loader))
            out.append(f)


def _fmt(v) -> str:
    if isinstance(v, list):
        return " | ".join(str(x) for x in v)
    return str(v or "*")


# -- graphics hooks in game folders -----------------------------------------------------------------------------------
def game_conflicts(g: ModGroup) -> list[Finding]:
    out: list[Finding] = []
    folders: dict[str, list[ModItem]] = defaultdict(list)
    for m in g.mods:
        folders[_folder(m.path)].append(m)
    for folder, items in folders.items():
        reshade = [m for m in items if m.kind == "reshade" and m.enabled]
        if len(reshade) > 1:
            f = Finding("mods.reshade_twice." + folder, "mods", Level.WARN, msg("ReShade is installed more than once"),
                        msg("{n} ReShade DLLs in the same folder: {files}. Two ReShade instances hook the same "
                            "rendering calls.", n=len(reshade), files=", ".join(_base(m.path) for m in reshade)),
                        msg("Keep one ReShade DLL. If AKENO installed one of them, remove the other from the game "
                            "folder yourself."))
            f.measured(msg("Found in {folder}", folder=folder), "PE version info")
            out.append(f)
        loaders = [m for m in items if m.kind == "asi_loader" and m.enabled]
        if len(loaders) > 1:
            f = Finding("mods.asi_loader_twice." + folder, "mods", Level.WARN, msg("Several ASI loaders"),
                        msg("{files} each load ASI plugins; plugins may be loaded twice.",
                            files=", ".join(_base(m.path) for m in loaders)),
                        msg("Keep one ASI loader."))
            out.append(f)
        unknown = [m for m in items if m.kind == "unknown_proxy" and m.enabled]
        for m in unknown:
            f = Finding("mods.unknown_proxy." + m.path, "mods", Level.INFO,
                        msg("Unknown DLL with a proxy name: {file}", file=_base(m.path)),
                        msg("Windows loads {file} from the game folder instead of the system copy. AKENO cannot tell "
                            "which mod it belongs to.", file=_base(m.path)),
                        msg("If you did not install it, scan it with Security Center."))
            f.link("security", msg("Scan with Security Center"), "file:" + m.path)
            out.append(f)
    for m in g.mods:
        if m.error:
            out.append(Finding("mods.unreadable." + m.path, "mods", Level.WARN, msg("{file} could not be read",
                                                                                   file=_base(m.path)),
                               msg("Reason: {why}", why=m.error)))
    return out


def _folder(path: str) -> str:
    import os
    return os.path.dirname(path)


def _base(path: str) -> str:
    import os
    return os.path.basename(path)
