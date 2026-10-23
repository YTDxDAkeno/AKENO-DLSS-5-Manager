"""Application service facade: the one object the UI talks to.

It owns the database, settings and every domain service, and exposes task-sized operations the UI
runs on worker threads. It holds no Qt objects, so it is fully testable without a display.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from akeno.backends import runtimes
from akeno.backends.base import BackendAdapter, BackendContext, BackendRegistry, InstallPlan, default_registry
from akeno.benchmark.service import BenchmarkService, PresentMonRunner
from akeno.compat.engine import CompatDatabase, evaluate
from akeno.core.db import Database
from akeno.core.models import (CompatibilityResult, CompatStatus, Game, GpuInfo, Severity, SystemInfo, TrustState)
from akeno.core.settings import Settings
from akeno.games.library import GameLibrary
from akeno.hardware import gpu_db
from akeno.hardware.detect import HardwareService
from akeno.i18n import msg
from akeno.install.transaction import Installer, TxResult
from akeno.paths import backups_dir, cache_dir, db_path, is_windows
from akeno.recommend.engine import Recommendation, recommend
from akeno.security import archive
from akeno.security.catalog import Catalog, Package
from akeno.security.downloads import CancelToken, DownloadManager, sha256_file

log = logging.getLogger(__name__)


@dataclass
class CheckItem:
    key: str
    state: str          # ok | warn | fail | pending | unknown
    title: str
    detail: str


@dataclass
class Readiness:
    items: list[CheckItem] = field(default_factory=list)
    can_install: bool = False
    reasons: list[str] = field(default_factory=list)
    # True when the only blockers are dependencies that "Resolve Dependencies" can obtain automatically
    can_resolve: bool = False

# Compatibility blocker codes that mean "a dependency is missing" (the resolver may be able to obtain it).
DEPENDENCY_CODES = {"runtime_missing", "missing_nvngx_dlssnr.dll", "missing_nvngx_dlss.dll"}


class AppServices:
    def __init__(self, db: Database | None = None, hardware: HardwareService | None = None,
                 registry: BackendRegistry | None = None, catalog: Catalog | None = None,
                 downloads: DownloadManager | None = None, installer: Installer | None = None,
                 compat_db: CompatDatabase | None = None):
        self.db = db or Database(db_path())
        self.settings = Settings(self.db)
        self.hardware = hardware or HardwareService()
        self.library = GameLibrary(self.db)
        self.registry = registry or default_registry()
        self.catalog = catalog or Catalog.load_bundled()
        self.downloads = downloads or DownloadManager(self.catalog.allowed_hosts)
        self.installer = installer or Installer(self.db)
        self.compat_db = compat_db or CompatDatabase.load()
        pm = self.catalog.tool("presentmon")
        self.benchmarks = BenchmarkService(self.db, PresentMonRunner(pm, self.downloads) if pm else None)
        from akeno.deps.resolver import DependencyResolver
        self.deps = DependencyResolver(self)
        from akeno.fivem.service import FiveMService
        self.fivem = FiveMService(self)
        from akeno.community.service import CommunityService
        self.community = CommunityService(self)
        from akeno.community.bundles import OfflineBundle
        try:
            self.offline_bundle = OfflineBundle()
        except (OSError, ValueError) as exc:
            log.warning("Offline bundle unavailable: %s", exc)
            self.offline_bundle = OfflineBundle(Path(__file__).parent / '_absent_bundle')
            self.offline_bundle.errors.append(str(exc))
        from akeno.pipeline.service import PipelineService
        self.pipelines = PipelineService(self)
        from akeno.pipeline.dlls import DllService
        self.dlls = DllService(self)
        from akeno.diagnostics.selftest import NeuralSelfTest
        self.neural_selftest = NeuralSelfTest(self)
        from akeno.quality.comparison import VisualComparisons
        self.visual_comparisons = VisualComparisons(self)
        self.system: SystemInfo | None = None
        cached = self.db.get_setting("_last_system")
        if cached:
            try:
                self.system = _system_from_dict(cached)
            except (TypeError, ValueError, KeyError):
                self.system = None

        from akeno.stack.service import StackManager
        self.stack = StackManager(self)

    # -- hardware ------------------------------------------------------------------------------
    def scan_hardware(self) -> SystemInfo:
        self.system = self.hardware.detect()
        self.db.set_setting("_last_system", self.system.to_dict())
        return self.system

    def selected_gpu(self) -> GpuInfo | None:
        if not self.system or not self.system.gpus:
            return None
        idx = self.settings.get("selected_gpu_index")
        if isinstance(idx, int) and 0 <= idx < len(self.system.gpus):
            return self.system.gpus[idx]
        return gpu_db.pick_primary(self.system.gpus)

    # -- games -------------------------------------------------------------------------------
    def selected_game(self) -> Game | None:
        g = self.library.get(self.settings.get("selected_game_id"))
        if g:
            return g
        games = self.library.games()
        return games[0] if games else None

    def select_game(self, game_id: str) -> None:
        self.settings.set("selected_game_id", game_id)

    # -- backends ----------------------------------------------------------------------------
    def backend_for(self, game: Game | None) -> BackendAdapter | None:
        if game is None:
            return None
        if game.launcher == "fivem":
            installation = self.library.fivem_installations.get(game.id)
            if installation is None:
                from akeno.fivem.discovery import inspect_installation
                installation = inspect_installation(game.install_dir)
                if installation:
                    self.library.fivem_installations[game.id] = installation
            if installation is None:
                return None
            policy = self.fivem.policy(game.id)
            backend_id = self.db.get_setting("fivem_backend:" + game.id, "dlss5_feeder")
            from akeno.fivem.compatibility import REVIEWED_BACKENDS
            review = next((r for r in REVIEWED_BACKENDS if r.backend_id == backend_id
                           and r.client_fingerprint == installation.fingerprint), None)
            return self.fivem.adapter(installation, backend_id, policy, review)
        ov = self.db.game_overrides(game.id).get("backend")
        if ov and self.registry.get(ov):
            return self.registry.get(ov)
        active = self.installer.active_install(game.id)
        if active and self.registry.get(active["backend_id"]):
            return self.registry.get(active["backend_id"])
        if game.upscaler_inputs:
            return self.registry.get("optiscaler_nr")
        return self.registry.get("dlss5_feeder")

    def set_backend(self, game: Game, backend_id: str | None) -> None:
        ov = self.db.game_overrides(game.id)
        if backend_id:
            ov["backend"] = backend_id
        else:
            ov.pop("backend", None)
        self.db.set_game_overrides(game.id, ov)

    def runtime_lookup(self, name: str) -> dict | None:
        local = runtimes.best_runtime(self.db, name, allow_unverified=not is_windows() and
                                     bool(os.environ.get("AKENO_ALLOW_UNVERIFIED_RUNTIME_FOR_TESTS")))
        if local:return local
        rows=self.db.get_setting('official_dll_inventory',{})
        matching=[r for r in rows.values() if r.get('name')==name and r.get('trust')=='nvidia_authenticated']
        if matching:
            return dict(max(matching,key=lambda r:runtimes._version_key(r.get('version'))),trust='verified')
        return self.offline_bundle.official_runtime(name)

    def context(self, game: Game, staging: Path | None = None, allow_disable: list[str] | None = None) -> BackendContext:
        if game.launcher == "fivem":
            adapter = self.backend_for(game)
            if adapter:
                return self.fivem.context(adapter, staging)
        managed = self.installer.managed_files(game.id, game.target_dir) if game.target_dir else set()
        from akeno.pipeline.proxy import observed_loader_paths
        return BackendContext(system=self.system, gpu=self.selected_gpu(), runtime_lookup=self.runtime_lookup,
                              staging_dir=staging, managed_files=managed, allow_disable=list(allow_disable or []),
                              strict_conflicts=bool(self.settings.get("block_unknown_conflicts")),
                              loaded_modules=tuple(observed_loader_paths(self,game)))

    def compatibility(self, game: Game, adapter: BackendAdapter | None = None) -> CompatibilityResult | None:
        adapter = adapter or self.backend_for(game)
        if adapter is None:
            return None
        if game.launcher == "fivem":
            return self.fivem.compatibility(adapter)
        return evaluate(self.compat_db, game, adapter, self.context(game))

    def recommendation(self, game: Game | None) -> Recommendation:
        adapter = self.backend_for(game)
        compat = self.compatibility(game, adapter) if game and adapter else None
        return recommend(game, self.system, self.selected_gpu(), adapter, compat,
                         self.benchmarks.sessions(game.id) if game else [], int(self.settings.get("target_fps")))

    # -- dependencies ------------------------------------------------------------------------
    def discover_dependencies(self):
        """Search this PC for NVIDIA runtimes (disk walk: call from a worker thread)."""
        return self.deps.discover()

    def dependency_plan(self, game: Game | None):
        """Cheap: uses the last discovery results, never walks the disk."""
        return self.deps.plan(game, self.backend_for(game))

    def resolve_dependencies(self, game: Game, progress=None, token: CancelToken | None = None):
        adapter = self.backend_for(game)
        if adapter is None:
            raise ValueError("No backend is available for this game.")
        return self.deps.resolve(game, adapter, progress, token)

    # -- packages ----------------------------------------------------------------------------
    def package_for(self, adapter: BackendAdapter, game: Game | None = None) -> Package | None:
        if adapter.id.startswith("fivem_legacy:"):
            return adapter.package
        pid=self.db.game_overrides(game.id).get('mod_package.'+adapter.id) if game else None
        pid = pid or self.settings.get(f"package.{adapter.id}")
        p = self.catalog.package(pid) if pid else None
        return p or next(iter(self.catalog.packages_for(adapter.id)),None)

    def package_cached(self, package: Package) -> bool:
        if not package.sha256:
            return False
        p = self.downloads.cached_path(package.sha256, os.path.basename(package.url or package.id))
        return p.is_file()

    def stage(self, game: Game, adapter: BackendAdapter, package: Package,
              progress: Callable[[int, int | None], None] | None = None, token: CancelToken | None = None) -> Path:
        """Download (or reuse) the verified package and extract the files this game needs."""
        if not package.installable:
            raise PermissionError(f"{package.name} {package.version} is not a reviewed, verified package "
                                  f"({package.trust.value}); automatic installation is disabled.")
        bundled = self.offline_bundle.backend_archive(package)
        if bundled is None:
            if self.offline_bundle.distribution == 'private-local-use':
                raise PermissionError('The private offline bundle is missing the selected pinned backend archive: ' + package.id)
            res = self.downloads.fetch(package.url, package.sha256, os.path.basename(package.url), package.size,
                                       progress, token)
            source = res.path
        else:
            source = bundled
        wanted = adapter.required_package_files(game, package)
        missing_pins = [k for k, v in wanted.items() if not v]
        if missing_pins:
            raise PermissionError("Package files without a reviewed hash: " + ", ".join(missing_pins))
        staging = cache_dir() / "staging" / f"{package.id}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        archive.extract(source, staging, wanted, package.root)
        return staging

    def _community_active(self, game: Game) -> bool:
        active = self.installer.active_install(game.id)
        return bool(active and active["backend_id"].startswith("community:"))

    def plan_install(self, game: Game, adapter: BackendAdapter, package: Package, params: dict[str, Any],
                     staging: Path, allow_disable: list[str] | None = None) -> InstallPlan:
        if game.launcher == "fivem":
            raise PermissionError("Use the FiveM setup wizard and its prepared transaction workflow")
        if self._community_active(game):
            raise PermissionError(msg("Use Community Runtime Manager or restore the active experiment first."))
        ctx = self.context(game, staging, allow_disable)
        return adapter.plan(game, package, params, ctx)

    def install(self, game: Game, plan: InstallPlan, log_cb=None) -> TxResult:
        if game.launcher == "fivem":
            raise PermissionError("Use the FiveM prepared transaction workflow")
        if plan.backend_id.startswith("fivem_legacy:"):
            raise PermissionError("Use the FiveM prepared transaction workflow")
        if plan.backend_id.startswith("community:") or "_community_runtime" in plan.params:
            raise PermissionError("Use the prepared community experiment workflow")
        if self._community_active(game):
            raise PermissionError(msg("Use Community Runtime Manager or restore the active experiment first."))
        adapter = self.registry.get(plan.backend_id)
        result = self.installer.apply(game, plan, log_cb)
        self.installer.prune(game.id, int(self.settings.get("backup_keep_per_game")))
        if result.ok and adapter:
            fresh = self.library.reanalyze(game.id) or game
            installed = {f["rel_path"]: f["installed_sha256"] for f in self.db.tx_files(result.tx_id)}
            problems = [f for f in adapter.validate(fresh, installed) if f.severity == Severity.BLOCKER]
            if problems:
                rb = self.installer.restore(result.tx_id, force=True)
                self.library.reanalyze(game.id)
                return TxResult(False, result.tx_id, "rolled_back",
                                ["Validation failed: " + "; ".join(p.message for p in problems),
                                 "Changes were rolled back." if rb.ok else "Rollback incomplete; see Backups."])
            if plan.params:
                name = plan.params.get("_profile", "custom")
                clean = {k: v for k, v in plan.params.items() if not k.startswith("_")}
                self.db.save_profile(game.id, name, adapter.id, clean, active=True)
            result.messages.append("Installation validated.")
        else:
            self.library.reanalyze(game.id)
        return result

    def configure(self, game: Game, adapter: BackendAdapter, params: dict[str, Any], profile_name: str) -> TxResult:
        if game.launcher == "fivem":
            return TxResult(False, None, "failed", ["FiveM configuration changes require a reviewed installation plan."])
        if self._community_active(game):
            return TxResult(False, None, "failed", [msg("Use Community Runtime Manager or restore the active experiment first.")])
        active = self.installer.active_install(game.id, adapter.id)
        if not active:
            return TxResult(False, None, "failed", [msg("{short} is not installed in this game.", short=adapter.short)])
        cfg_path = Path(active["target_dir"]) / "OptiScaler.ini"
        current = cfg_path.read_text(encoding="utf-8-sig", errors="replace") if cfg_path.is_file() else None
        plan = adapter.plan_configure(game, self.catalog.package(active["package_id"] or ""), params,
                                      self.context(game), current)
        if plan is None:
            return TxResult(False, None, "failed", ["This backend has no settings AKENO can change."])
        plan.params = dict(params, _profile=profile_name)
        res = self.installer.apply(game, plan)
        if res.ok:
            self.db.save_profile(game.id, profile_name, adapter.id, params, active=True)
        return res

    def restore(self, tx_id: int, force: bool = False) -> TxResult:
        tx = self.db.get_transaction(tx_id)
        if tx and tx["backend_id"].startswith("fivem_legacy:"):
            from akeno.fivem.discovery import inspect_installation
            installation = inspect_installation(Path(tx["target_dir"]).parent.parent)
            if installation is None:
                raise PermissionError("The FiveM client must be inspected before restore")
            return self.fivem.restore(installation, tx_id, force)
        res = self.installer.restore(tx_id, force=force)
        if tx:
            self.library.reanalyze(tx["game_id"])
        return res

    def restore_snapshot(self, tx_id: int, force: bool = False) -> TxResult:
        tx = self.db.get_transaction(tx_id)
        game = self.library.get(tx["game_id"]) if tx else None
        if game and game.launcher == "fivem":
            from akeno.fivem.discovery import inspect_installation
            installation = inspect_installation(game.install_dir)
            if installation is None:
                raise PermissionError("The FiveM client must be inspected before restore")
            return self.fivem.restore_snapshot(installation, tx_id, force)
        return self.installer.restore_snapshot(tx_id, force)

    def create_snapshot(self, game: Game) -> TxResult:
        """Manual backup of every graphics-mod related file next to the game executable."""
        if game.launcher == "fivem":
            adapter = self.backend_for(game)
            if adapter:
                return self.fivem.snapshot(adapter.installation)
        if not game.target_dir:
            return TxResult(False, None, "failed", ["The game executable is unknown."])
        from akeno.games.analyzer import KNOWN_FILES, PROXY_NAMES
        rels: list[str] = []
        for m in game.existing_mods:
            if os.path.dirname(os.path.normcase(m.path)) == os.path.normcase(game.target_dir):
                rels.append(m.name)
        for n in sorted(PROXY_NAMES | set(KNOWN_FILES)):
            p = Path(game.target_dir) / n
            if p.is_file() and n not in [r.lower() for r in rels]:
                rels.append(p.name)
        for extra in ("ReShade.ini", "ReShadePreset.ini", "OptiScaler.ini"):
            if (Path(game.target_dir) / extra).is_file() and extra not in rels:
                rels.append(extra)
        unique = list({r.lower(): r for r in reversed(rels)}.values())
        res = self.installer.snapshot(game, game.target_dir, sorted(unique, key=str.lower))
        if res.ok:
            self.installer.prune(game.id, int(self.settings.get("backup_keep_per_game")))
        return res

    # -- readiness (Security / Verification panel) ----------------------------------------------
    def readiness(self, game: Game | None) -> Readiness:
        r = Readiness()
        if game is None:
            r.items.append(CheckItem("game", "pending", "No game selected", "Select a game to check readiness."))
            r.reasons.append("No game selected.")
            return r
        if self._community_active(game):
            reason = msg("Use Community Runtime Manager or restore the active experiment first.")
            r.items.append(CheckItem("community", "warn", "Community Runtime Manager", reason))
            r.reasons.append(reason)
            return r
        adapter = self.backend_for(game)
        if adapter is None:
            r.reasons.append("No backend available.")
            return r
        pkg = self.package_for(adapter)
        if pkg is None:
            r.items.append(CheckItem("source", "fail", "No package", "No reviewed package is listed for this backend."))
        elif pkg.trust == TrustState.VERIFIED:
            r.items.append(CheckItem("source", "ok", "Source verified",
                                     msg("{name} {version} from {upstream} (reviewed {reviewed}).",
                                         name=pkg.name, version=pkg.version, upstream=pkg.upstream,
                                         reviewed=pkg.reviewed)))
        else:
            r.items.append(CheckItem("source", "fail", "Source needs review",
                                     f"{pkg.name}: {pkg.trust.value.replace('_', ' ')}. {pkg.review_notes}"))
        if pkg and pkg.installable:
            cached = self.downloads.cached_path(pkg.sha256, os.path.basename(pkg.url))
            if cached.is_file():
                ok = sha256_file(cached) == pkg.sha256
                r.items.append(CheckItem("hash", "ok" if ok else "fail",
                                         "Hash check passed" if ok else "Hash check failed",
                                         "SHA-256 of the downloaded package matches the pinned value." if ok else
                                         "The cached package does not match its pinned hash and will be discarded."))
            else:
                r.items.append(CheckItem("hash", "pending", "Hash check pending",
                                         "The package is verified against its pinned SHA-256 when it is downloaded."))
        # NVIDIA runtime
        names = {m.name.lower() for m in game.existing_mods}
        rt = self.runtime_lookup(runtimes.NR_RUNTIME)
        plan = self.dependency_plan(game)
        rt_dep = plan.get("nr_runtime")
        if runtimes.NR_RUNTIME in names:
            r.items.append(CheckItem("runtime", "warn", "NVIDIA runtime present in game",
                                     "nvngx_dlssnr.dll already exists in the game folder (not installed by AKENO)."))
        elif rt:
            r.items.append(CheckItem("runtime", "ok", "NVIDIA runtime ready",
                                     msg("nvngx_dlssnr.dll {v} signed by {s}.", v=rt.get("version") or "",
                                         s=rt.get("signer") or "?")))
        elif rt_dep is not None and rt_dep.state == "resolvable_local":
            # Found but not imported yet: blue "info", never green.
            r.items.append(CheckItem("runtime", "info", "NVIDIA runtime found", rt_dep.detail))
        else:
            r.items.append(CheckItem("runtime", "fail", "NVIDIA runtime missing",
                                     rt_dep.detail if rt_dep is not None and rt_dep.detail else
                                     "Import nvngx_dlssnr.dll from your NVIDIA driver on the Secure Install page."))
        # Backups
        bdir = backups_dir()
        try:
            free = shutil.disk_usage(bdir).free
            ok = os.access(bdir, os.W_OK) and free > 200 * 1024 * 1024
            r.items.append(CheckItem("backup", "ok" if ok else "fail", "Backup ready" if ok else "Backup not possible",
                                     "Game files will be backed up and verified before installation." if ok else
                                     "The backup folder is not writable or the disk is almost full."))
        except OSError as exc:
            r.items.append(CheckItem("backup", "fail", "Backup not possible", str(exc)))
        compat = self.compatibility(game, adapter)
        if compat:
            label = {CompatStatus.CONFIRMED: ("ok", "Confirmed compatible"),
                     CompatStatus.EXPERIMENTAL: ("warn", "Experimental"),
                     CompatStatus.INSUFFICIENT: ("warn", "Compatibility unknown"),
                     CompatStatus.INCOMPATIBLE: ("fail", "Known incompatible"),
                     CompatStatus.BLOCKED: ("fail", "Blocked for safety")}[compat.status]
            r.items.append(CheckItem("compat", label[0], label[1],
                                     compat.blockers[0].message if compat.blockers else
                                     (compat.warnings[0].message if compat.warnings else
                                      f"{adapter.short}: {compat.status.value}.")))
            r.reasons = [f.message for f in compat.blockers]
        if game.is_running:
            r.reasons.append("Installation blocked: the game is currently running.")
        r.items.append(CheckItem("community", "warn", "Experimental community mod",
                                 "Not officially supported by NVIDIA. Use at your own risk."))
        r.can_install = not r.reasons and bool(pkg and pkg.installable)
        if pkg and not pkg.installable and not r.reasons:
            r.reasons.append("The package is not a reviewed, verified release.")
        if not r.can_install and compat and pkg and pkg.installable and not game.is_running:
            codes = {f.code for f in compat.blockers}
            r.can_resolve = bool(codes) and codes <= DEPENDENCY_CODES and plan.ready_after_resolve
        return r


def _system_from_dict(d: dict[str, Any]) -> SystemInfo:
    from akeno.core.models import DisplayInfo, Vendor
    gpus = []
    for g in d.get("gpus", []):
        g = dict(g)
        g["vendor"] = Vendor(g.get("vendor", "unknown"))
        gpus.append(GpuInfo(**g))
    info = SystemInfo(**{k: v for k, v in d.items() if k not in ("gpus", "displays")})
    info.gpus = gpus
    info.displays = [DisplayInfo(**x) for x in d.get("displays", [])]
    return info


def dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)
