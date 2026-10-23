"""Build the file-level installation plan for a resolved stack.

The plan copies every staged file to its final name, writes the generated configuration, takes back what
a previous managed version installed but this one does not (no mixed versions), and lists every file of
another tool it would have to replace or disable. Those need the user's approval; their originals are
backed up and restored on uninstall.
"""

from __future__ import annotations

from akeno.i18n import msg

import json
from dataclasses import dataclass, field
from pathlib import Path

from akeno.backends.base import FileOp, InstallPlan
from akeno.games import pe
from akeno.security.downloads import sha256_file

from . import configs
from .catalog import Recipe, StackCatalog
from .sources import Staged, reshade_identity

BACKEND_ID = "stack"
MARKER = "akeno-dlss5.json"
PROXY_ORDER = ("winmm.dll", "version.dll", "dbghelp.dll", "wininet.dll", "winhttp.dll")
ASI_LOADER_NAMES = ("version.dll", "dinput8.dll", "winmm.dll", "d3d9.dll", "dsound.dll", "xinput1_3.dll")
OPTISCALER_NAMES = (*PROXY_ORDER, "dxgi.dll", "d3d12.dll", "optiscaler.asi", "optiscaler.dll")
OTHER_CONSUMERS = ("deep-fried-chicken", "renodx-dlss.addon64", "zzz_renodx-dlss.addon64", "standalone-dlssnr")
GRAPHICS_NAMES = ("dxgi.dll", "d3d11.dll", "d3d12.dll", "opengl32.dll", "d3d9.dll")
LOG_ARTIFACTS = ("ReShade.log", "ReShade2.log", "dlss5-feed.log", "OptiScaler.log")
SCANNED_SUBFOLDERS = ("reshade-shaders", "OptiScaler")


@dataclass
class PreviewRow:
    path: str
    action: str              # add | update | remove | restore | replace | disable | keep | config
    detail: str = ""
    approval: str | None = None     # replace | disable when another tool's file is affected


@dataclass
class Prepared:
    game_id: str
    recipe_id: str
    selection: dict
    plan: InstallPlan
    rows: list[PreviewRow] = field(default_factory=list)
    fingerprint: dict[str, str | None] = field(default_factory=dict)
    fivem_ack: bool = False          # CitizenFX.ini needs FiveM's ReShade acknowledgement
    notes: list[str] = field(default_factory=list)
    download_bytes: int = 0

    @property
    def approvals(self) -> list[PreviewRow]:
        return [r for r in self.rows if r.approval]


def _scan(root: Path, prefix: str) -> dict[str, Path]:
    """lower-case rel path (relative to the transaction root) -> file, for the folders a stack touches."""
    base = root / prefix if prefix else root
    out: dict[str, Path] = {}
    try:
        children = list(base.iterdir())
    except OSError:
        return out
    for p in children:
        if p.is_file():
            out[(prefix + p.name).casefold()] = p
        elif p.is_dir() and not p.is_symlink() and p.name.casefold() in {s.casefold() for s in SCANNED_SUBFOLDERS}:
            count = 0
            for q in p.rglob("*"):
                count += 1
                if count > 5000:
                    break
                if q.is_file():
                    out[(prefix + q.relative_to(base).as_posix()).casefold()] = q
    return out


def _is_asi_loader(path: Path) -> bool:
    try:
        return b"Ultimate-ASI-Loader" in pe.contains_bytes(str(path), [b"Ultimate-ASI-Loader"])
    except OSError:
        return False


def _is_optiscaler(path: Path) -> bool:
    info = pe.parse(str(path))
    if not info.valid:
        return False
    if "optiscaler" in " ".join(info.strings.values()).lower():
        return True
    try:
        return b"OptiScaler" in pe.contains_bytes(str(path), [b"OptiScaler"])
    except OSError:
        return False


def pick_proxy(folder: Path, managed: set[str], previous: str | None = None) -> str:
    """Upstream's rule (Skynizz install.py): OptiScaler.asi beside an ASI loader, else the first free
    common proxy name. The previous managed name is kept so an update replaces it in place."""
    if previous and (previous.casefold() in managed or not (folder / previous).exists()):
        return previous
    try:
        files = {p.name.casefold(): p for p in folder.iterdir() if p.is_file()}
    except OSError:
        files = {}
    if any(name.endswith(".asi") and name != "optiscaler.asi" for name in files) or any(
            n in files and n not in managed and _is_asi_loader(files[n]) for n in ASI_LOADER_NAMES):
        return "OptiScaler.asi"
    for name in PROXY_ORDER:
        if name not in files or name in managed:
            return name
    raise ValueError("No free proxy name in the game folder (winmm, version, dbghelp, wininet and winhttp are "
                     "all taken by other mods).")


def _version_tuple(text: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in str(text or "0").split(".")[:3] if x.isdigit())


class Planner:
    def __init__(self, catalog: StackCatalog):
        self.catalog = catalog

    def build(self, *, game, target, recipe: Recipe, staged: dict[str, Staged], preset: str, custom: dict | None,
              profile, managed: set[str], previous: dict | None, previous_files: dict[str, dict]) -> Prepared:
        """``managed``: lower-case rel paths AKENO owns in this target. ``previous_files``: rel -> record of the
        active managed version (``installed``, ``original_action``, ``backup_path``, ``original_sha``)."""
        root = Path(target.root)
        existing = _scan(root, target.prefix)
        plan = InstallPlan(BACKEND_ID, None, str(root), kind="install")
        rows: list[PreviewRow] = []
        ops: dict[str, FileOp] = {}
        replace: set[str] = set()
        disable: dict[str, str] = {}

        def add(rel: str, source: Path | None = None, content: bytes | None = None, kind: str = "file",
                sha: str | None = None, description: str = "") -> None:
            low = rel.casefold()
            current = existing.get(low)
            if current is not None and low not in managed:
                if kind == "file":
                    if sha and sha256_file(current) == sha:
                        rows.append(PreviewRow(rel, "keep", "Already present with identical content; left as it is."))
                        return
                    replace.add(rel)
                    rows.append(PreviewRow(rel, "replace", "Belongs to another tool: backed up, restored when you "
                                                           "remove the mod.", "replace"))
                else:
                    replace.add(rel)
                    rows.append(PreviewRow(rel, "config", "Existing settings are kept and merged; the original is "
                                                          "backed up."))
            elif current is not None:
                rows.append(PreviewRow(rel, "update" if kind == "file" else "config", description))
            else:
                rows.append(PreviewRow(rel, "add" if kind == "file" else "config", description))
            ops[low] = FileOp(rel, source=source, content=content, kind=kind, expected_sha256=sha,
                              description=description)

        def text_of(rel: str) -> str:
            p = existing.get(rel.casefold())
            return p.read_text(encoding="utf-8-sig", errors="replace") if p is not None else ""

        def disable_file(path: Path, why: str) -> None:
            rel = path.relative_to(root).as_posix()
            if rel.casefold() in managed or rel.casefold() in ops:
                return
            disable[rel.casefold()] = rel
            rows.append(PreviewRow(rel, "disable", msg("{reason} Renamed to *.akeno-disabled, renamed back on removal.",
                                                       reason=msg(why)), "disable"))

        api = target.api
        feeder = recipe.uses_feeder
        consumer_role = recipe.consumer_role
        consumer = staged[consumer_role]
        component = self.catalog.component(consumer.version.component)
        consumer_cfg = None

        # -- ReShade ---------------------------------------------------------------------------------------
        if recipe.uses_reshade:
            name = target.rel(target.reshade_name)
            cur = existing.get(name.casefold())
            reuse = False
            if cur is not None and name.casefold() not in managed:
                ok, version, _ = reshade_identity(cur)
                if ok and _version_tuple(version) >= ((6, 8) if feeder else (6, 0)):
                    reuse = True
                    rows.append(PreviewRow(name, "keep", msg("Your ReShade {version} with add-on support is used.",
                                                             version=version)))
            if not reuse:
                rs = staged["reshade"]
                add(name, rs.path("ReShade64.dll"), sha=rs.hashes["ReShade64.dll"],
                    description=msg("ReShade {version} with add-on support", version=rs.version.version))
            for other in GRAPHICS_NAMES:
                if other == target.reshade_name:
                    continue
                p = existing.get(target.rel(other).casefold())
                if p is not None and reshade_identity(p)[0]:
                    disable_file(p, "A second ReShade would load twice.")

        # -- Feeder and motion vectors -------------------------------------------------------------------------
        if feeder:
            fd = staged["feeder"]
            add(target.rel("dlss5-feed.addon64"), fd.path("dlss5-feed.addon64"), sha=fd.hashes["dlss5-feed.addon64"],
                description=msg("DLSS5-Feeder {version}", version=fd.version.version))
            add(target.rel("reshade-shaders/Shaders/DLSS5_Feed.fx"), fd.path("reshade-shaders/Shaders/DLSS5_Feed.fx"),
                sha=fd.hashes["reshade-shaders/Shaders/DLSS5_Feed.fx"], description="DLSS5-Feeder effect")
            mo = staged["motion"]
            for logical, path in mo.files.items():
                add(target.rel("reshade-shaders/" + logical), path, sha=mo.hashes[logical],
                    description=msg("Motion vectors: {version}", version=mo.version.display))
            helper = existing.get(target.rel("dlss5-feed-helper.addon64").casefold())
            if helper is not None:
                disable_file(helper, "The Feeder helper must not run beside the in-process add-on.")
            mv = mo.version
            add(target.rel(configs.PRESET_FILE),
                content=configs.reshade_preset(mv.technique or "DH_UBER_MOTION_020", mv.shader or "dh_uber_motion.fx",
                                               mv.provider_id or 0).encode("utf-8"),
                kind="config", description="Effect order: motion vectors above DLSS 5 Feed")
            add(target.rel("dlss5-feed.cfg"),
                content=configs.feeder_cfg(dict(profile.feeder) if profile else {}).encode("utf-8"),
                kind="config", description="Feeder: full DLSS path at 100% transport resolution")

        # -- Neural Rendering consumer ------------------------------------------------------------------------------
        if consumer_role == "consumer_renodx":
            add(target.rel("renodx-dlss5.addon64"), consumer.path("renodx-dlss5.addon64"),
                sha=consumer.hashes["renodx-dlss5.addon64"],
                description=msg("RenoDX DLSS 5 {version}", version=consumer.version.version))
            consumer_cfg = configs.renodx_values(preset, custom, consumer.version.settings,
                                                 consumer.version.config_version, feeder,
                                                 renodx_overrides(game, profile, feeder))
        else:
            folder = root / target.prefix if target.prefix else root
            proxy = pick_proxy(folder, {m.rsplit("/", 1)[-1] for m in managed}, (previous or {}).get("proxy"))
            plan.params["proxy"] = proxy
            add(target.rel(proxy), consumer.path("OptiScaler.dll"), sha=consumer.hashes["OptiScaler.dll"],
                description=msg("{name} {version}, loaded as {proxy}", name=component.name,
                                version=consumer.version.version, proxy=proxy))
            for logical, path in consumer.files.items():
                if logical not in ("OptiScaler.dll", "OptiScaler.ini"):
                    add(target.rel(logical), path, sha=consumer.hashes[logical], description=component.name)
            values = configs.optiscaler_values(preset, custom, component.features, api, consumer.version.settings,
                                               feeder, dict(profile.optiscaler) if profile else None)
            template = consumer.path("OptiScaler.ini").read_text(encoding="utf-8-sig")
            text = configs.optiscaler_ini(template, values, text_of(target.rel("OptiScaler.ini")) or None)
            add(target.rel("OptiScaler.ini"), content=text.encode("utf-8"), kind="config",
                description=msg("OptiScaler configuration, {preset} preset",
                                preset=msg(configs.PRESET_LABELS.get(preset, preset))))

        # Exactly one neural consumer may run.
        own = {op.rel_path.casefold() for op in ops.values()}
        for low, path in existing.items():
            name = low.rsplit("/", 1)[-1]
            if low in own or low in managed or "/" in low[len(target.prefix):]:
                continue
            if name.startswith("renodx-dlss5") and name.endswith(".addon64"):
                disable_file(path, "Another RenoDX DLSS 5 add-on." if consumer_role == "consumer_renodx"
                             else "Two neural consumers must never run together.")
            elif any(name.startswith(x) for x in OTHER_CONSUMERS) and name.endswith((".addon64", ".dll")):
                disable_file(path, "Another DLSS 5 neural add-on; only one may run.")
            elif name in OPTISCALER_NAMES and name != target.reshade_name and _is_optiscaler(path):
                disable_file(path, "Another OptiScaler installation would hook the same calls.")

        if recipe.uses_reshade:
            definitions: dict[str, str] = {}
            if feeder:
                definitions["DLSS5_MV_PROVIDER"] = str(staged["motion"].version.provider_id or 0)
                definitions.update(profile.reshade_definitions if profile else {})
            text = configs.reshade_ini(text_of(target.rel("ReShade.ini")), effects=feeder, definitions=definitions,
                                       consumer=consumer_cfg)
            add(target.rel("ReShade.ini"), content=text.encode("utf-8"), kind="config",
                description="ReShade add-on path" + (", effects" if feeder else "") + ", RenoDX DLSS 5 preset"
                if consumer_cfg else "ReShade add-on path and effects")

        # -- Runtimes ---------------------------------------------------------------------------------------------------
        if "sr_runtime" in recipe.roles:
            sr = staged["sr_runtime"]
            rel = target.rel("nvngx_dlss.dll")
            if target.kind == "game" and rel.casefold() in existing and rel.casefold() not in managed:
                rows.append(PreviewRow(rel, "keep", "The game's own DLSS runtime is used."))
            else:
                add(rel, sr.path("nvngx_dlss.dll"), sha=sr.hashes["nvngx_dlss.dll"],
                    description=msg("DLSS Super Resolution {version}", version=sr.version.version))
        nr = staged["nr_runtime"]
        add(target.rel("nvngx_dlssnr.dll"), nr.path("nvngx_dlssnr.dll"), sha=nr.hashes["nvngx_dlssnr.dll"],
            description=msg("DLSS 5 model {version}", version=nr.version.display))
        versions = {role: s.version.key for role, s in staged.items()}
        marker = {"managed_by": "AKENO DLSS 5 Manager", "recipe": recipe.id, "preset": preset,
                  "versions": {role: s.version.display for role, s in staged.items()}}
        add(target.rel(MARKER), content=json.dumps(marker, indent=2).encode("utf-8"), kind="marker",
            description="AKENO installation record")

        # -- What the previous managed version installed but this one does not ------------------------------------
        for rel, rec in sorted(previous_files.items()):
            if rel.casefold() in ops:
                continue
            path = root / rel
            installed = rec.get("installed")
            if installed in (None, "deleted") or not path.is_file() or path.is_symlink():
                continue
            actual = sha256_file(path)
            is_config = rec.get("file_kind") == "config"
            if actual != installed and not is_config:
                rows.append(PreviewRow(rel, "keep", "From the previous version but changed since; left in place."))
                continue
            backup = rec.get("backup_path")
            if rec.get("original_action") == "replace" and backup and Path(backup).is_file():
                ops[rel.casefold()] = FileOp(rel, source=Path(backup), expected_sha256=rec.get("original_sha"),
                                             kind=rec.get("file_kind") or "file",
                                             description="Original file put back")
                rows.append(PreviewRow(rel, "restore", "Not used by this version; your original is put back."))
            else:
                ops[rel.casefold()] = FileOp(rel, kind="delete", expected_sha256=actual,
                                             description="Not part of the selected version")
                rows.append(PreviewRow(rel, "remove", "Installed by the previous version, not used by this one."))

        plan.ops = list(ops.values())
        plan.replace_foreign = replace
        plan.rename_away = sorted(disable.values())
        plan.params.update({"recipe": recipe.id, "preset": preset, "custom": dict(custom or {}),
                            "versions": versions, "target_kind": target.kind,
                            "preexisting_logs": [n for n in LOG_ARTIFACTS if target.rel(n).casefold() in existing]})
        plan.summary = [msg("{method}: {preset} preset into {folder}", method=msg(recipe.name),
                            preset=msg(configs.PRESET_LABELS.get(preset, preset)), folder=str(root))]
        fingerprint = {}
        for row in rows:
            p = root / row.path
            fingerprint[row.path] = sha256_file(p) if p.is_file() else None
        selection = {"recipe": recipe.id, "versions": versions, "preset": preset, "custom": dict(custom or {})}
        return Prepared(game.id, recipe.id, selection, plan, rows, fingerprint)

    def configure(self, *, game, target, recipe: Recipe, active: dict, staged_consumer: Staged | None,
                  preset: str, custom: dict | None, profile) -> InstallPlan:
        """A configuration-only transaction: the consumer's settings for another preset."""
        root = Path(target.root)
        params = dict(active.get("params", {}))
        plan = InstallPlan(BACKEND_ID, None, str(root), kind="configure",
                           params=dict(params, preset=preset, custom=dict(custom or {})))
        feeder = recipe.uses_feeder
        cv = self.catalog.version(params.get("versions", {}).get(recipe.consumer_role, ""))
        if recipe.consumer_role == "consumer_renodx":
            rel = target.rel("ReShade.ini")
            path = root / rel
            current = path.read_text(encoding="utf-8-sig", errors="replace") if path.is_file() else ""
            values = configs.renodx_values(preset, custom, cv.settings if cv else (),
                                           cv.config_version if cv else None, feeder,
                                           renodx_overrides(game, profile, feeder))
            plan.ops.append(FileOp(rel, content=configs.reshade_ini(current, effects=False, consumer=values).encode("utf-8"),
                                   kind="config", description="RenoDX DLSS 5 preset"))
        else:
            rel = target.rel("OptiScaler.ini")
            path = root / rel
            current = path.read_text(encoding="utf-8-sig", errors="replace") if path.is_file() else ""
            component = self.catalog.component(cv.component) if cv else None
            values = configs.optiscaler_values(preset, custom, component.features if component else frozenset(),
                                               target.api, cv.settings if cv else (), feeder,
                                               dict(profile.optiscaler) if profile else None)
            if not current and staged_consumer:
                current = staged_consumer.path("OptiScaler.ini").read_text(encoding="utf-8-sig")
            plan.ops.append(FileOp(rel, content=configs.optiscaler_ini(current, values).encode("utf-8"),
                                   kind="config", description="OptiScaler DLSS 5 preset"))
        plan.summary = [msg("Apply the {preset} preset", preset=msg(configs.PRESET_LABELS.get(preset, preset)))]
        return plan


def renodx_overrides(game, profile, feeder: bool) -> dict:
    """Per-game RenoDX keys. On native routes a Streamline game needs EnableHooks=1 (the add-on's own advice)."""
    if feeder:
        return {}
    out = dict(profile.renodx) if profile else {}
    if "EnableHooks" not in out and any(m.kind == "streamline" for m in getattr(game, "existing_mods", []) or []):
        out["EnableHooks"] = "1"
    return out
