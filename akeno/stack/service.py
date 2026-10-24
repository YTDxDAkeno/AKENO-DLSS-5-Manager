"""StackManager: everything the My Games, FiveM, Installed Mods and Downloads pages do.

Typical flow: ``state(game_id)`` lists the methods for the game with their recommended versions and what
is missing, ``prepare()`` downloads/stages the selected versions and builds a reviewable plan, ``install()``
applies it as one backed-up transaction, ``apply_preset()`` changes the consumer's settings, and
``restore_previous()`` / ``uninstall()`` go back. All of it runs on worker threads; nothing here touches Qt.
"""

from __future__ import annotations

from akeno.i18n import msg

import logging
import os
import re
import signal
import subprocess
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from akeno.core import processes
from akeno.core.models import AntiCheatRisk, Game, GpuInfo, GraphicsApi, Vendor
from akeno.install.transaction import TxResult
from akeno.paths import is_windows
from akeno.security.downloads import CancelToken, Cancelled, sha256_file

from . import configs, transports
from .catalog import ROLES, ComponentVersion, GameProfile, Recipe, StackCatalog
from .errors import GameRunning, StackError, explain
from .planner import BACKEND_ID, LOG_ARTIFACTS, Planner, Prepared, PreviewRow
from .releases import GitHubReleases
from .sources import ComponentStore, Staged, reshade_identity
from .targets import FiveMTarget, target_for

log = logging.getLogger(__name__)
SELECTION_KEY = "stack"


@dataclass
class Issue:
    level: str               # block | warn | info
    code: str
    message: str
    remedy: str = ""
    action: str | None = None


@dataclass
class RoleChoice:
    role: str
    title: str
    version: ComponentVersion | None
    options: list[ComponentVersion]
    state: str               # ready | download | unavailable
    download_bytes: int = 0
    note: str = ""


@dataclass
class Method:
    recipe: Recipe
    rank: int
    issues: list[Issue] = field(default_factory=list)
    roles: list[RoleChoice] = field(default_factory=list)

    @property
    def blockers(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "block"]

    @property
    def available(self) -> bool:
        return not self.blockers

    @property
    def download_bytes(self) -> int:
        return sum(r.download_bytes for r in self.roles)


@dataclass
class GameState:
    game: Game
    profile: GameProfile | None
    gpu: GpuInfo | None
    api: str
    methods: list[Method]
    selected: Method | None
    recommended: Method | None
    preset: str
    custom: dict
    installed: dict | None
    legacy: dict | None
    issues: list[Issue] = field(default_factory=list)


@dataclass
class Result:
    ok: bool
    messages: list[str]
    tx_id: int | None = None
    conflicts: list = field(default_factory=list)


def _gen(gpu: GpuInfo | None) -> str | None:
    return gpu.generation if gpu else None


class StackManager:
    def __init__(self, services, catalog: StackCatalog | None = None, store: ComponentStore | None = None,
                 releases: GitHubReleases | None = None):
        self.s = services
        self.catalog = catalog or StackCatalog.load()
        self.store = store or ComponentStore(services.db, self.catalog)
        repos = {c.repository for c in self.catalog.components.values() if c.repository and c.release}
        self.releases = releases or GitHubReleases(services.db, repos,
                                                   token_getter=lambda: services.settings.get("github_token") or "")
        self.planner = Planner(self.catalog)
        self.lock = threading.RLock()

    # -- selection persistence ---------------------------------------------------------------------------
    def selection(self, game_id: str) -> dict:
        return dict(self.s.db.game_overrides(game_id).get(SELECTION_KEY) or {})

    def save_selection(self, game_id: str, **values) -> dict:
        ov = self.s.db.game_overrides(game_id)
        sel = dict(ov.get(SELECTION_KEY) or {})
        for k, v in values.items():
            if v is None:
                sel.pop(k, None)
            else:
                sel[k] = v
        ov[SELECTION_KEY] = sel
        self.s.db.set_game_overrides(game_id, ov)
        return sel

    def choose_recipe(self, game_id: str, recipe_id: str) -> None:
        if recipe_id not in self.catalog.recipes:
            raise StackError("Unknown installation method.", code="unknown_recipe")
        self.save_selection(game_id, recipe=recipe_id)

    def choose_version(self, game_id: str, role: str, key: str | None) -> None:
        sel = self.selection(game_id)
        versions = dict(sel.get("versions") or {})
        if key:
            versions[role] = key
        else:
            versions.pop(role, None)
        self.save_selection(game_id, versions=versions)

    def choose_preset(self, game_id: str, preset: str, custom: dict | None = None) -> None:
        if preset not in configs.PRESETS:
            raise StackError("Unknown preset.", code="bad_preset")
        self.save_selection(game_id, preset=preset, custom=dict(custom or {}) if preset == "custom" else None)

    # -- versions ---------------------------------------------------------------------------------------------
    def all_versions(self, component_id: str, include_unreviewed: bool = True) -> list[ComponentVersion]:
        c = self.catalog.component(component_id)
        out = list(c.versions)
        out.extend(v for v in self.store.local_versions() if v.component == component_id)
        if include_unreviewed:
            out.extend(self.releases.discovered_versions(c))
        return out

    def model_for(self, gpu: GpuInfo | None) -> ComponentVersion | None:
        """The DLSS 5 model build My Games preselects for this GPU."""
        options = [v for c in self.catalog.components.values() if c.role == "nr_runtime"
                   for v in self.all_versions(c.id, include_unreviewed=False)]
        return self._default_version(options, "nr_runtime", gpu)

    def _default_version(self, options: list[ComponentVersion], role: str, gpu: GpuInfo | None) -> ComponentVersion | None:
        gen = _gen(gpu)
        usable = [v for v in options if v.supports_gpu(gen) is not False and v.reviewed]
        if role == "nr_runtime":
            preferred = [v for v in usable if gen and gen in v.recommended_for]
            if not preferred and gen is None:
                # Unknown GPU: the build listed for every RTX generation.
                preferred = [v for v in usable if len(v.gpus) >= 4]
            usable = preferred or [v for v in usable if v.supports_gpu(gen) is True] or usable
        else:
            usable = [v for v in usable if v.recommended] or [v for v in usable if v.channel == "stable"] or usable
        if not usable:
            return None
        best = usable[0]
        # Prefer an identical copy that is already on this PC (no download needed).
        for v in options:
            if v.source == "local" and v.reviewed and v.version == best.version and v.signed == best.signed \
                    and v.supports_gpu(gen) is not False and self.store.is_ready(v):
                return v
        return best

    def _role_choice(self, recipe: Recipe, role: str, sel: dict, gpu: GpuInfo | None, issues: list[Issue]) -> RoleChoice:
        options: list[ComponentVersion] = []
        for cid in recipe.roles[role]:
            options.extend(self.all_versions(cid))
        chosen_key = (sel.get("versions") or {}).get(role)
        chosen = next((v for v in options if v.key == chosen_key), None)
        if chosen is None or chosen.component not in recipe.roles[role]:
            chosen = self._default_version(options, role, gpu)
        title = recipe.title(role)
        if chosen is None:
            issues.append(Issue("block", "no_version:" + role, msg("No usable {title} for this GPU.", title=msg(title)),
                                "Import a compatible file from this PC.", "import:" + role))
            return RoleChoice(role, title, None, options, "unavailable")
        gen = _gen(gpu)
        note = ""
        if chosen.supports_gpu(gen) is False:
            issues.append(Issue("block", "gpu_version:" + role,
                                msg("{version} does not run on {gpu} GPUs.", version=chosen.display, gpu=gen),
                                "Choose a build listed for your GPU.", "versions"))
        if chosen.source == "bundled" and not self.store.bundled_available(chosen):
            alt = next((v for v in options if v.source != "bundled"), None)
            if alt is not None and not chosen_key:
                chosen = alt
            else:
                issues.append(Issue("block", "bundle:" + role,
                                    msg("{version} is not included in this build.", version=chosen.display),
                                    "Choose another source for it.", "versions"))
                return RoleChoice(role, title, chosen, options, "unavailable")
        if not chosen.reviewed:
            issues.append(Issue("warn", "unreviewed:" + role,
                                msg("{version} is a newer upstream release AKENO has not reviewed yet.",
                                    version=chosen.display),
                                "It is checked by file name and type only."))
        if chosen.signed == "modified":
            note = "Community build of NVIDIA's model, not signed by NVIDIA."
        ready = self.store.is_ready(chosen)
        size = 0 if ready else self.store.download_size(chosen)
        state = "ready" if ready else ("download" if chosen.source in ("download", "reshade_setup") else "unavailable")
        if state == "unavailable":
            issues.append(Issue("block", "missing:" + role, msg("{version} is not available.", version=chosen.display),
                                "Import it again or choose another version.", "import:" + role))
        return RoleChoice(role, title, chosen, options, state, size, note)

    # -- evaluation -------------------------------------------------------------------------------------------
    def game_api(self, game: Game, profile: GameProfile | None) -> str:
        if game.launcher == "fivem":
            return "d3d11"          # Cfx rage-graphics-five creates a D3D11 device for GTA V Legacy
        chosen = self.s.db.game_overrides(game.id).get("api")
        if chosen:
            return str(chosen)
        if profile and profile.api:
            # Import tables are ambiguous for many engines; the profile names the renderer of this exact .exe.
            return profile.api
        return game.api.value

    def _game_issues(self, game: Game, gpu: GpuInfo | None, sel: dict) -> list[Issue]:
        issues = []
        if gpu is None:
            issues.append(Issue("warn", "no_gpu", "The graphics card has not been detected yet.",
                                "Run a hardware scan so AKENO picks the right model build.", "scan_hardware"))
        elif gpu.vendor != Vendor.NVIDIA or gpu.has_tensor_cores is False:
            issues.append(Issue("block", "gpu", msg("{gpu} cannot run DLSS 5 Neural Rendering.", gpu=gpu.name),
                                "An NVIDIA GeForce RTX card is required."))
        if game.launcher != "fivem":
            if not game.exe_path:
                issues.append(Issue("block", "no_exe", "The game executable is unknown.",
                                    "Select the game's .exe.", "select_exe"))
            if game.anti_cheat == AntiCheatRisk.DETECTED and not sel.get("anti_cheat_ack"):
                issues.append(Issue("block", "anti_cheat",
                                    msg("This game ships anti-cheat: {names}.",
                                        names=", ".join(game.anti_cheat_details[:2])),
                                    "Graphics mods are only safe in offline/single-player mode. Confirm that you will "
                                    "not play online with the mod installed.", "ack_anti_cheat"))
        if game.is_running:
            issues.append(Issue("warn", "running", "The game is running.", "Close it before installing.",
                                "refresh"))
        return issues

    def _rank(self, game: Game, profile: GameProfile | None, recipe: Recipe, upscalers: set[str]) -> int:
        if profile and recipe.id in profile.recipes:
            return profile.recipes.index(recipe.id)
        order = (["optiscaler", "renodx", "feeder", "feeder_optiscaler"] if "dlss" in upscalers else
                 ["optiscaler", "feeder", "feeder_optiscaler", "renodx"] if upscalers else
                 ["feeder", "feeder_optiscaler", "optiscaler", "renodx"])
        return 10 + (order.index(recipe.id) if recipe.id in order else 9)

    def evaluate(self, game: Game, recipe: Recipe, sel: dict, gpu: GpuInfo | None, profile: GameProfile | None,
                 api: str, upscalers: set[str]) -> Method:
        issues: list[Issue] = []
        labels = {a.value: a.label for a in GraphicsApi}
        if api not in recipe.apis:
            need = " / ".join(labels.get(a, a) for a in recipe.apis)
            if api == "unknown":
                issues.append(Issue("block", "api_unknown", "AKENO could not tell which graphics API the game uses.",
                                    "Choose it in the game's settings panel.", "choose_api"))
            else:
                issues.append(Issue("block", "api", msg("This method needs {need}; the game uses {api}.", need=need,
                                                        api=labels.get(api, "?")),
                                    "Choose another method, or switch the game's renderer and select its executable."))
        arch = game.architecture
        if recipe.target == "game" and arch and arch not in recipe.architectures:
            issues.append(Issue("block", "arch", "This method is for 32-bit games." if recipe.helper else
                                "This method needs a 64-bit game executable.",
                                "" if recipe.helper else "32-bit games use the DLSS5-Feeder 32-bit helper method."))
        elif recipe.target == "game" and recipe.helper and not arch:
            issues.append(Issue("block", "arch_unknown", "AKENO could not read whether the game is 32-bit.",
                                "Select the game's .exe.", "select_exe"))
        if recipe.target == "game" and recipe.uses_reshade and api in recipe.apis and (arch or "x64") in recipe.architectures:
            issues.extend(self._transport_issues(game, api, arch or "x64"))
        if recipe.needs_upscaler == "any" and not upscalers:
            issues.append(Issue("block", "no_upscaler", "No DLSS, FSR 2+ or XeSS was found in this game.",
                                "Use the DLSS5-Feeder method for games without an upscaler."))
        elif recipe.needs_upscaler == "dlss" and "dlss" not in upscalers:
            issues.append(Issue("block", "no_dlss", "This game has no DLSS for the add-on to hook.",
                                "Use OptiScaler (FSR/XeSS games) or the DLSS5-Feeder method."))
        if recipe.uses_feeder and "dlss" in upscalers and recipe.target == "game":
            issues.append(Issue("info", "native_better", "This game has its own DLSS: the native methods use the "
                                "game's real motion vectors instead of estimated ones."))
        roles = [self._role_choice(recipe, role, sel, gpu, issues) for role in recipe.roles_for(api)]
        if recipe.target == "fivem":
            issues.extend(self.fivem_issues(game, sel))
        return Method(recipe, self._rank(game, profile, recipe, upscalers), issues, roles)

    def _transport_issues(self, game: Game, api: str, arch: str) -> list[Issue]:
        """How ReShade gets into this game, and whether an existing machine-wide Vulkan layer can be used."""
        try:
            tr = transports.choose(api, arch, game.target_dir)
        except ValueError as exc:
            return [Issue("block", "transport", str(exc), "Choose another renderer for this game.", "choose_api")]
        out = [] if tr.id == "dxgi" else [Issue("info", "transport", tr.describe())]
        if tr.layer and tr.machine_layer:
            dll = transports.machine_layer_dll(tr.machine_layer)
            ok, version, why = reshade_identity(dll, arch) if dll else (False, None, "its DLL is missing")
            if not ok or tuple(int(x) for x in str(version or "0").split(".")[:2] if x.isdigit()) < (6, 8):
                out.append(Issue("block", "vk_layer",
                                 msg("A machine-wide ReShade Vulkan layer is installed but cannot load add-ons "
                                     "({reason}).", reason=why if not ok else "version " + str(version)),
                                 "Run ReShade's official setup with add-on support once, or uninstall that "
                                 "machine-wide ReShade; AKENO then uses its own per-user layer."))
        return out

    def fivem_issues(self, game: Game, sel: dict) -> list[Issue]:
        out = []
        try:
            target = self._target(game)
        except StackError as exc:
            return [Issue("block", exc.code, exc.message, exc.remedy, "rescan")]
        inst = target.installation
        from akeno.fivem.models import Edition
        if inst.edition != Edition.LEGACY:
            out.append(Issue("block", "fivem_edition",
                             "FiveM's GTA V path does not point to a GTA V Legacy installation (GTA5.exe).",
                             "Set FiveM to your GTA V Legacy folder in FiveM's settings, then rescan."
                             if inst.edition.value == "unknown" else
                             "FiveM Legacy uses GTA V Legacy; the Enhanced edition is a different game."))
        if target.reshade_ack() != "present":
            out.append(Issue("info", "fivem_ack", "FiveM blocks ReShade 5 and newer until you acknowledge its "
                             "crash-risk warning in CitizenFX.ini.",
                             "AKENO adds FiveM's own acknowledgement line during installation (removed again when "
                             "you remove the mod)."))
        return out

    def state(self, game_id: str) -> GameState:
        game = self.s.library.get(game_id)
        if game is None:
            raise StackError("This game is no longer in the library.", "Scan games again.", "no_game")
        profile = self.catalog.profile_for(game)
        gpu = self.s.selected_gpu()
        sel = self.selection(game_id)
        api = self.game_api(game, profile)
        upscalers = set(game.upscaler_inputs) | set(profile.upscalers if profile else ())
        issues = self._game_issues(game, gpu, sel)
        methods = []
        for recipe in self.catalog.recipes.values():
            if (recipe.target == "fivem") != (game.launcher == "fivem"):
                continue
            if recipe.target == "game" and game.architecture and game.architecture not in recipe.architectures:
                continue                  # a 32-bit game only sees the 32-bit route, a 64-bit game never sees it
            methods.append(self.evaluate(game, recipe, sel, gpu, profile, api, upscalers))
        methods.sort(key=lambda m: (not m.available, m.rank))
        recommended = next((m for m in methods if m.available), methods[0] if methods else None)
        active = self.active(game_id)
        if active and self.catalog.recipes.get(active["params"].get("recipe")) and \
                self.catalog.recipe(active["params"]["recipe"]).helper and self.helper_orphaned(game):
            issues.append(Issue("warn", "helper_running", "The 64-bit DLSS 5 helper is still running although the "
                                "game is closed.", "Stop it before you change or remove the mod.", "stop_helper"))
        chosen_id = sel.get("recipe") or (active or {}).get("params", {}).get("recipe")
        selected = next((m for m in methods if m.recipe.id == chosen_id), None) or recommended
        preset = sel.get("preset") or (active or {}).get("params", {}).get("preset") or self.default_preset(gpu, game)
        return GameState(game, profile, gpu, api, methods, selected, recommended, preset, dict(sel.get("custom") or {}),
                         active, self.legacy_install(game), issues)

    def _target(self, game: Game):
        """The install target with the renderer My Games uses (profile or the user's choice) and its transport."""
        if game.launcher == "fivem":
            return target_for(game)
        return target_for(game, api=self.game_api(game, self.catalog.profile_for(game)))

    def default_preset(self, gpu: GpuInfo | None, game: Game) -> str:
        gen = _gen(gpu)
        if game.architecture == "x86":
            # the 32-bit route runs DLAA at native size plus the 32-to-64-bit hop (the Feeder: about half the fps)
            return "balanced" if gen == "RTX 50" else "performance"
        if gen == "RTX 50":
            return "quality"
        if game.launcher == "fivem" and gen in ("RTX 40", "RTX 30", "RTX 20"):
            return "performance"
        return "balanced" if gen else "quality"

    # -- installed state -------------------------------------------------------------------------------------
    def active(self, game_id: str) -> dict | None:
        return self.s.installer.active_install(game_id, BACKEND_ID)

    def legacy_install(self, game: Game) -> dict | None:
        """An active installation made by an older AKENO version (v1/v2 backends) in the same folder."""
        for t in self.s.installer.db.transactions(game.id):
            if t["state"] == "installed" and t["kind"] in ("install", "swap") and t["backend_id"] != BACKEND_ID:
                return t
        return None

    def installed(self) -> list[dict]:
        out = []
        for t in self.s.db.transactions():
            if t["state"] == "installed" and t["kind"] in ("install", "swap"):
                out.append(t)
        return out

    def _previous_files(self, active: dict | None) -> dict[str, dict]:
        if not active:
            return {}
        chain = self.s.installer._chain(active)
        originals, latest = self.s.installer._file_views(chain)
        out = {}
        for rel, rec in latest.items():
            orig = originals.get(rel, rec)
            out[rel] = {"installed": rec.get("installed_sha256"), "file_kind": rec.get("file_kind") or orig.get("file_kind"),
                        "original_action": orig.get("action"), "backup_path": orig.get("backup_path"),
                        "original_sha": orig.get("original_sha256")}
        return out

    # -- prepare / install -----------------------------------------------------------------------------------
    def prepare(self, game_id: str, recipe_id: str | None = None, versions: dict | None = None,
                preset: str | None = None, custom: dict | None = None,
                progress: Callable[[str, int, int | None], None] | None = None,
                token: CancelToken | None = None) -> Prepared:
        with self.lock:
            game = self.s.library.reanalyze(game_id) or self.s.library.get(game_id)
            if game is None:
                raise StackError("This game is no longer in the library.", code="no_game")
            if recipe_id:
                self.choose_recipe(game_id, recipe_id)
            if versions:
                for role, key in versions.items():
                    self.choose_version(game_id, role, key)
            if preset:
                self.choose_preset(game_id, preset, custom)
            st = self.state(game_id)
            method = st.selected
            if method is None:
                raise StackError("No installation method fits this game.", code="no_method")
            blockers = [i for i in st.issues if i.level == "block"] + method.blockers
            if blockers:
                b = blockers[0]
                raise StackError(b.message, b.remedy, b.code)
            if st.legacy:
                raise StackError("An installation made by an older AKENO version is active in this game.",
                                 "Remove it first (Installed Mods → Remove); AKENO restores the original files.",
                                 "legacy_install")
            target = self._target(game)
            staged: dict[str, Staged] = {}
            total = sum(r.download_bytes for r in method.roles)
            done_bytes = 0
            for choice in method.roles:
                if token and token.cancelled:
                    raise Cancelled("Cancelled.")
                label = f"{choice.title}: {choice.version.display}"
                base = done_bytes

                def cb(cur, tot, label=label, base=base):
                    if progress:
                        progress(label, base + cur, total or tot)
                if progress:
                    progress(label, done_bytes, total or None)
                staged[choice.role] = self.store.ensure(choice.version, cb, token)
                done_bytes += choice.download_bytes
            active = self.active(game_id)
            managed = self.s.installer.managed_files(game.id, str(target.root))
            prepared = self.planner.build(game=game, target=target, recipe=method.recipe, staged=staged,
                                          preset=st.preset, custom=st.custom, profile=st.profile, managed=managed,
                                          previous=(active or {}).get("params"),
                                          previous_files=self._previous_files(active))
            prepared.download_bytes = total
            if isinstance(target, FiveMTarget) and target.reshade_ack() != "present":
                prepared.fivem_ack = True
                prepared.rows.append(PreviewRow("CitizenFX.ini", "config",
                                                "Adds FiveM's own [Addons] ReShade5 acknowledgement; removed on uninstall."))
            return prepared

    def install(self, prepared: Prepared, approve: bool = False, force: bool = False, log_cb=None) -> Result:
        with self.lock:
            game = self.s.library.get(prepared.game_id)
            if game is None:
                raise StackError("This game is no longer in the library.", code="no_game")
            target = self._target(game)
            target.ensure_stopped()
            if prepared.approvals and not approve:
                raise StackError("Files of other tools need your approval before they are replaced or disabled.",
                                 "Review the list and confirm.", "approval_needed")
            root = Path(target.root)
            for rel, sha in prepared.fingerprint.items():
                p = root / rel
                if (sha256_file(p) if p.is_file() else None) != sha:
                    raise StackError("Files in the game folder changed after the preview.", "Prepare the "
                                     "installation again.", "changed_after_preview")
            plan = prepared.plan
            plan.force = force
            before = (self.active(game.id) or {}).get("params", {}).get("vk_layer")
            res: TxResult = self.s.installer.apply(game, plan, log_cb)
            messages = list(res.messages)
            if res.ok:
                layer = plan.params.get("vk_layer")
                if before and before != layer:
                    transports.unregister_user_layer(before)
                if layer:
                    transports.register_user_layer(layer)
                    messages.append("ReShade's Vulkan layer is registered for your Windows user; it only "
                                    "activates in this game.")
                if prepared.fivem_ack:
                    messages.append(self._add_fivem_ack(target, res.tx_id))
                self.s.installer.prune(game.id, int(self.s.settings.get("backup_keep_per_game") or 5))
                self.save_selection(game.id, recipe=prepared.recipe_id, versions=prepared.selection["versions"],
                                    preset=prepared.selection["preset"],
                                    custom=prepared.selection.get("custom") or None)
                self.s.library.reanalyze(game.id)
                messages.append("Installation complete. Start the game and turn the mod's effect on as described.")
            return Result(res.ok, messages, res.tx_id, res.conflicts)

    def _add_fivem_ack(self, target: FiveMTarget, tx_id: int) -> str:
        """Write FiveM's own documented acknowledgement; the original CitizenFX.ini is kept in the backup."""
        try:
            backup = Path(self.s.db.get_transaction(tx_id)["backup_dir"]) / "CitizenFX.ini.original"
            if target.citizen_ini.is_file():
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(target.citizen_ini.read_bytes())
            content = target.reshade_ack_content()
            tmp = target.citizen_ini.with_name("CitizenFX.ini.akeno-tmp")
            tmp.write_bytes(content)
            tmp.replace(target.citizen_ini)
            tx = self.s.db.get_transaction(tx_id)
            self.s.db.update_transaction(tx_id, params=dict(tx["params"], fivem_ack_added=True))
            self.s.db.append_tx_log(tx_id, "Added FiveM's [Addons] ReShade5 acknowledgement to CitizenFX.ini.")
            return "FiveM's ReShade acknowledgement was added to CitizenFX.ini."
        except OSError as exc:
            return msg("Could not update CitizenFX.ini ({error}); FiveM will block ReShade until it is acknowledged.",
                       error=str(exc))

    def _remove_fivem_ack(self, game: Game) -> str | None:
        try:
            target = self._target(game)
        except StackError:
            return None
        if not isinstance(target, FiveMTarget) or target.reshade_ack() != "present":
            return None
        from akeno.backends import ini
        text = target.citizen_ini.read_text(encoding="utf-8-sig", errors="replace")
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("ReShade5=")]
        cleaned = "\n".join(lines) + "\n"
        if not ini.get_values(cleaned).get("Addons"):
            cleaned = "\n".join(ln for ln in cleaned.splitlines() if ln.strip() != "[Addons]") + "\n"
        target.citizen_ini.write_text(cleaned, encoding="utf-8")
        return "Removed the ReShade acknowledgement from CitizenFX.ini."

    # -- presets ------------------------------------------------------------------------------------------------
    def apply_preset(self, game_id: str, preset: str, custom: dict | None = None) -> Result:
        with self.lock:
            self.choose_preset(game_id, preset, custom)
            active = self.active(game_id)
            if not active:
                return Result(True, ["Preset saved. It is applied when you install."])
            game = self.s.library.get(game_id)
            target = self._target(game)
            target.ensure_stopped()
            recipe = self.catalog.recipe(active["params"]["recipe"])
            versions = active["params"].get("versions", {})
            consumer = self.catalog.version(versions.get(recipe.consumer_role, ""), self.store.local_versions())
            staged = self.store.staged(consumer) if consumer else None
            plan = self.planner.configure(game=game, target=target, recipe=recipe, active=active,
                                          staged_consumer=staged, preset=preset, custom=custom,
                                          profile=self.catalog.profile_for(game))
            res = self.s.installer.apply(game, plan)
            return Result(res.ok, list(res.messages) + ([msg("{preset} preset applied.",
                                                             preset=msg(configs.PRESET_LABELS[preset]))]
                                                        if res.ok else []), res.tx_id, res.conflicts)

    # -- going back --------------------------------------------------------------------------------------------
    def previous_versions(self, game_id: str) -> list[dict]:
        active = self.active(game_id)
        if not active:
            return []
        chain = self.s.installer._chain(active)
        seen, out = {repr(active["params"].get("versions"))}, []
        for t in chain:
            if t["id"] == active["id"] or t["kind"] != "install":
                continue
            key = repr(t["params"].get("versions"))
            if key in seen:
                continue
            seen.add(key)
            out.append(t)
        return out

    def restore_previous(self, game_id: str, progress=None, token=None) -> Prepared:
        """Prepare the previous managed version again (from the download cache when possible)."""
        prev = self.previous_versions(game_id)
        if not prev:
            raise StackError("There is no previous version to go back to.",
                             "Use Remove mod to restore the original files.", "no_previous")
        p = prev[0]["params"]
        return self.prepare(game_id, p.get("recipe"), p.get("versions"), p.get("preset"), p.get("custom"),
                            progress, token)

    def uninstall(self, game_id: str, force: bool = False) -> Result:
        with self.lock:
            game = self.s.library.get(game_id)
            active = self.active(game_id) or (self.legacy_install(game) if game else None)
            if not game or not active:
                return Result(False, ["Nothing is installed in this game."])
            try:
                target = self._target(game)
                target.ensure_stopped()
            except GameRunning:
                raise
            except StackError:
                target = None
            conflicts = self.s.installer.check_restore(active["id"])
            hard = [c for c in conflicts if "backup" in c.reason]
            if hard:
                return Result(False, ["A backup is missing or damaged; nothing was changed."], active["id"], hard)
            if conflicts and not force:
                return Result(False, ["Some installed files were changed after installation. Confirm to remove "
                                      "them anyway."], active["id"], conflicts)
            res = self.s.restore(active["id"], force=True)
            messages = list(res.messages)
            if res.ok:
                for layer in {t.get("params", {}).get("vk_layer") for t in [active, *self.s.installer._chain(active)]}:
                    if layer:
                        transports.unregister_user_layer(layer)
                if active.get("params", {}).get("fivem_ack_added") or any(
                        t.get("params", {}).get("fivem_ack_added") for t in self.s.installer._chain(active)):
                    note = self._remove_fivem_ack(game)
                    if note:
                        messages.append(note)
                if target is not None:
                    keep = set(active.get("params", {}).get("preexisting_logs", []))
                    for name in LOG_ARTIFACTS:
                        p = Path(target.root) / target.rel(name)
                        if name not in keep and p.is_file():
                            try:
                                p.unlink()
                            except OSError:
                                pass
                messages.append("The original files are back. The mod is removed completely.")
            return Result(res.ok, messages, active["id"], res.conflicts)

    def remove_legacy(self, game_id: str) -> Result:
        game = self.s.library.get(game_id)
        legacy = self.legacy_install(game) if game else None
        if not legacy:
            return Result(True, ["No older installation is active."])
        res = self.s.restore(legacy["id"], force=True)
        return Result(res.ok, list(res.messages), legacy["id"], res.conflicts)

    # -- the 32-bit route's 64-bit helper -----------------------------------------------------------------------
    HELPER_EXE = "dlss5-feed-host64.exe"
    HELPER_TEST = re.compile(r"--test finished: (\d+)/(\d+) evaluates succeeded")

    def helper_processes(self, game: Game) -> list:
        if not game.target_dir:
            return []
        try:
            return [p for p in processes.processes_in_dir(os.path.join(game.target_dir, "host64"))
                    if p.name.lower() == self.HELPER_EXE]
        except Exception:                     # process listing is best effort
            return []

    def helper_orphaned(self, game: Game) -> bool:
        """The helper outlived its game (a crash): it keeps host64 files open and has nothing to serve."""
        helpers = self.helper_processes(game)
        if not helpers:
            return False
        try:
            others = [p for p in processes.processes_in_dir(game.install_dir) if p.pid not in {h.pid for h in helpers}]
        except Exception:
            return False
        return not others

    def stop_helper(self, game_id: str) -> Result:
        game = self.s.library.get(game_id)
        if game is None:
            raise StackError("This game is no longer in the library.", code="no_game")
        helpers = self.helper_processes(game)
        if not helpers:
            return Result(True, ["The helper is not running."])
        if not self.helper_orphaned(game):
            return Result(False, ["Close the game first; the helper closes with it."])
        for p in helpers:
            try:
                os.kill(p.pid, signal.SIGTERM)    # TerminateProcess on Windows
            except OSError as exc:
                return Result(False, [msg("Could not stop the helper: {error}", error=str(exc))])
        return Result(True, ["The 64-bit helper was stopped."])

    def test_helper(self, game_id: str, timeout: int = 240) -> Result:
        """Run the helper's own self-test (``dlss5-feed-host64.exe --test``: 300 NGX evaluations without the game)
        and report its result line from host64\\dlss5-feed-host.log."""
        game = self.s.library.get(game_id)
        active = self.active(game_id) if game else None
        recipe = self.catalog.recipes.get((active or {}).get("params", {}).get("recipe")) if active else None
        if not game or not recipe or not recipe.helper:
            raise StackError("The 32-bit route is not installed in this game.", code="no_helper")
        folder = Path(game.target_dir) / "host64"
        exe = folder / self.HELPER_EXE
        if not exe.is_file():
            raise StackError("host64\\dlss5-feed-host64.exe is missing.", "Install the mod again.", "no_helper")
        if not is_windows():
            raise StackError("The helper test runs on Windows.", code="not_windows")
        self._target(game).ensure_stopped()
        log_file = folder / "dlss5-feed-host.log"
        try:
            subprocess.run([str(exe), "--test"], cwd=str(folder), timeout=timeout, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return Result(False, ["The helper test did not finish in time."])
        except OSError as exc:
            return Result(False, [msg("The helper could not start: {error}", error=str(exc))])
        text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.is_file() else ""
        hits = self.HELPER_TEST.findall(text)
        if not hits:
            return Result(False, ["The helper test wrote no result. See host64\\dlss5-feed-host.log."])
        good, total = (int(x) for x in hits[-1])
        if good == total:
            return Result(True, [msg("Helper test passed: {good}/{total} DLSS evaluations succeeded.",
                                     good=good, total=total)])
        return Result(False, [msg("Helper test: only {good}/{total} DLSS evaluations succeeded. Check the driver and "
                                  "the DLSS 5 model build for this GPU.", good=good, total=total)])

    # -- launch ---------------------------------------------------------------------------------------------------
    def launch(self, game_id: str, connect: str | None = None) -> str:
        game = self.s.library.get(game_id)
        if game is None:
            raise StackError("This game is no longer in the library.", code="no_game")
        target = self._target(game)
        if connect is not None:
            self.save_selection(game_id, server=connect or None)
        target.launch(connect or None, self.s.db.game_meta(game_id).get("launch_args") or None)
        if (self.active(game_id) or {}).get("params", {}).get("recipe") == "optiscaler":
            return msg("Game started. Open OptiScaler's menu with Insert to check DLSS 5.")
        return msg("Game started. Open the ReShade overlay with Home to check the add-ons.")

    # -- downloads page ---------------------------------------------------------------------------------------------
    def download(self, key: str, progress=None, token=None) -> Staged:
        v = self.catalog.version(key, self.store.local_versions())
        if v is None:
            for c in self.catalog.components.values():
                v = next((x for x in self.releases.discovered_versions(c) if x.key == key), None)
                if v:
                    break
        if v is None:
            raise StackError("Unknown version.", code="unknown_version")
        return self.store.ensure(v, progress, token)

    def cached_versions(self) -> list[ComponentVersion]:
        out = []
        for c in self.catalog.components.values():
            for v in self.all_versions(c.id):
                if v.source in ("download", "reshade_setup") and self.store.is_ready(v):
                    out.append(v)
        return out

    def delete_cached(self, key: str) -> None:
        v = self.catalog.version(key, self.store.local_versions())
        if v is None:
            return
        import shutil
        shutil.rmtree(self.store.folder(v), ignore_errors=True)
        if v.sha256:
            self.store.downloads.cached_path(v.sha256, v.asset or "download").unlink(missing_ok=True)


def explain_error(exc: BaseException) -> str:
    return str(explain(exc))
