"""Transaction facade for future launcher integration, with fresh pre-apply checks."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Callable

from akeno.backends.base import BackendContext, InstallPlan
from akeno.compat.engine import evaluate
from akeno.core import processes
from akeno.core.models import CompatibilityResult, CompatStatus, Finding, Severity
from akeno.install.transaction import TxResult
from akeno.security.downloads import sha256_file
from .adapter import FiveMLegacyAdapter
from .compatibility import BackendReview
from .discovery import classify_process, discover, inspect_installation
from .models import FiveMInstallation, ServerPolicy
from .paths import guard_target


def _plan_digest(plan: InstallPlan) -> str:
    data = {"backend": plan.backend_id, "target": plan.target_dir, "kind": plan.kind,
            "package": vars(plan.package) if plan.package else None,
            "rename": plan.rename_away, "params": plan.params,
            "ops": [(o.rel_path, str(o.source) if o.source else None,
                     hashlib.sha256(o.content).hexdigest() if o.content is not None else None,
                     o.expected_sha256, o.kind) for o in plan.ops]}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _binding_digest(adapter: FiveMLegacyAdapter) -> str:
    material = repr((adapter.id, adapter.delegate.id, adapter.policy, adapter.review,
                     adapter.installation.root, adapter.installation.fingerprint))
    return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True)
class PreparedFiveMPlan:
    installation: FiveMInstallation
    adapter: FiveMLegacyAdapter
    plan: InstallPlan
    digest: str
    binding_digest: str


class FiveMService:
    """Uses AppServices' registry, catalog, staging, compatibility DB and Installer.

    The v1.2 launcher routes FiveM discovery and its embedded wizard here.
    Do not submit FiveM plans to the generic UI install path; use this facade so the
    identity, current server permission, process and fresh-file checks also execute.
    """
    def __init__(self, services, process_reader: Callable | None = None):
        self.services = services
        self.process_reader = process_reader or processes.list_processes

    def diagnose_existing(self, game) -> dict:
        from dataclasses import asdict
        from akeno.backends import runtimes
        from akeno.diagnostics import neural
        from akeno.games import pe
        installation = self.services.library.fivem_installations.get(game.id) if game.launcher == "fivem" else None
        target = installation.plugin_dir if installation else Path(game.target_dir or game.install_dir)
        mods = installation.inventory if installation else ()
        paths = {str(f.path) for f in mods}
        if target.is_dir():
            for p in list(target.iterdir())[:2000]:
                if p.suffix.lower() in (".dll", ".asi", ".addon64", ".ini"):
                    paths.add(str(p))
        paths.update(m.path for m in game.existing_mods)
        for name in runtimes.SUPPORTED_RUNTIMES:
            if (target / name).is_file():
                paths.add(str(target / name))
        components = []
        for path in sorted(paths):
            p = Path(path)
            if p.is_symlink() or not p.is_file():
                continue
            info = pe.parse(p)
            record = {"path": str(p), "sha256": sha256_file(p), "version": info.file_version,
                      "metadata": info.strings, "exports": info.exports[:64], "provenance": "local file; origin not attested"}
            if p.name.lower() in runtimes.SUPPORTED_RUNTIMES:
                try:
                    record["runtime"] = asdict(runtimes.evaluate(str(p)))
                    if p.name.lower() == runtimes.NR_RUNTIME:
                        record["community_inspection"] = self.services.community.runtimes.inspect(str(p)).to_dict()
                except (OSError, ValueError) as exc:
                    record["runtime_error"] = str(exc)
            record["catalog_matches"] = [{"package": pkg.id, "upstream": pkg.upstream, "member": name,
                                           "trust": pkg.trust.value}
                                          for pkg in self.services.catalog.packages
                                          for name, pin in pkg.files.items() if pin == record["sha256"]]
            components.append(record)
        gpu = self.services.selected_gpu()
        extra = {}
        if installation:
            adapter = self.services.backend_for(game)
            compatibility = self.compatibility(adapter) if adapter else None
            policy = asdict(self.policy(game.id))
            for key in ("allowed_backends", "known_graphics_hashes"):
                policy[key] = sorted(policy[key])
            from akeno.community.candidates import BANDUKIDS
            extra = {"bandukids_qualification": {"candidate": BANDUKIDS.to_dict(),
                         "deployment_eligible": bool(compatibility and not compatibility.blockers),
                         "recipe_available": bool(adapter and adapter.review),
                         "note": "Local hash match/ComfyUI processing is not a FiveM recipe. Exact client/plugin loading, protection compatibility, offline and FiveM neural evaluation plus operator permission are required."},
                     "server_policy": policy, "edition": installation.edition.value,
                     "compatibility": asdict(compatibility) if compatibility else None}
        return {"schema": 1, "read_only": True, "evidence_level": "local_inventory", **extra, "game_id": game.id, "game": game.name,
                "gpu": gpu.to_dict() if gpu else None, "target": str(target), "components": components,
                "neural": asdict(neural.inspect(target)),
                "note": "Local inventory is not a redistribution license or proof of neural execution. No files were imported or replaced."}

    def policy(self, game_id: str) -> ServerPolicy:
        value = self.services.db.get_setting("fivem_policy:" + game_id) or {}
        try:
            data = dict(value)
            for key in ("allowed_backends", "known_graphics_hashes"):
                data[key] = frozenset(data.get(key, []))
            return ServerPolicy(**data)
        except (TypeError, ValueError):
            return ServerPolicy()

    def save_policy(self, game_id: str, policy: ServerPolicy, backend_id: str) -> None:
        from dataclasses import asdict
        if backend_id not in ("dlss5_feeder", "optiscaler_nr"):
            raise ValueError("Unsupported FiveM backend")
        data = asdict(policy)
        data["allowed_backends"] = sorted(policy.allowed_backends)
        data["known_graphics_hashes"] = sorted(policy.known_graphics_hashes)
        self.services.db.set_setting("fivem_policy:" + game_id, data)
        self.services.db.set_setting("fivem_backend:" + game_id, backend_id)

    def discover(self, extra_roots=()):
        return discover(extra_roots, self.process_reader())

    def adapter(self, installation: FiveMInstallation, backend_id: str, policy: ServerPolicy,
                review: BackendReview | None = None, package=None) -> FiveMLegacyAdapter:
        delegate = self.services.registry.get(backend_id)
        if delegate is None:
            raise ValueError("Unknown backend")
        package = package or self.services.package_for(delegate)
        return FiveMLegacyAdapter(delegate, installation, policy, package, review, self.services.compat_db,
                                  self.services.community.runtimes.inspect)

    def context(self, adapter: FiveMLegacyAdapter, staging: Path | None = None) -> BackendContext:
        lookup = self.services.runtime_lookup
        if adapter.review and adapter.review.runtime_policy == "reviewed_bandukids":
            def lookup(name):
                if name != "nvngx_dlssnr.dll":
                    return self.services.runtime_lookup(name)
                pin = dict(adapter.review.runtime_hashes).get(name)
                data = self.services.db.get_setting("community-runtime:" + (pin or ""))
                if not data:
                    return None
                return {"stored_path": data.get("path"), "sha256": data.get("sha256"),
                    "trust": data.get("category"), "review": data.get("review"), "version": data.get("version")}
        return BackendContext(system=self.services.system, gpu=self.services.selected_gpu(),
            runtime_lookup=lookup, staging_dir=staging,
            managed_files=self.services.installer.managed_files(adapter.installation.game.id, str(adapter.target)),
            strict_conflicts=True)

    def compatibility(self, adapter: FiveMLegacyAdapter) -> CompatibilityResult:
        try:
            context = self.context(adapter)
        except (OSError, ValueError) as exc:
            return CompatibilityResult(CompatStatus.BLOCKED, adapter.id,
                [Finding(Severity.BLOCKER, "fivem_target", str(exc))], confidence="low")
        result = evaluate(self.services.compat_db, adapter.installation.game, adapter, context)
        codes = {f.code for f in result.blockers}
        security = {"anti_cheat", "fivem_protection", "fivem_runtime_integrity", "fivem_existing_runtime",
                    "fivem_target", "fivem_unreviewed_plugin", "fivem_external_hook", "fivem_identity"}
        denied = adapter.policy.graphics_plugins_allowed is False or adapter.policy.pure_level == 2
        if codes & security or denied:
            result.status = CompatStatus.BLOCKED
        elif adapter.installation.edition.value == "enhanced":
            result.status = CompatStatus.INCOMPATIBLE
        elif result.blockers:
            result.status = CompatStatus.INSUFFICIENT
        else:
            result.status = CompatStatus.EXPERIMENTAL
        result.confidence = "low"
        return result

    def prepare(self, adapter: FiveMLegacyAdapter, params=None, staging: Path | None = None) -> PreparedFiveMPlan:
        result = self.compatibility(adapter)
        if result.blockers or result.status != CompatStatus.EXPERIMENTAL:
            raise PermissionError("; ".join(f.message for f in result.blockers) or "Backend is blocked")
        game, package = adapter.installation.game, adapter.package
        # Uses the existing allowlisted downloader, ZIP validator and per-member pinned hashes.
        if staging is None:
            staging = self.services.stage(game, adapter, package)
        plan = adapter.plan(game, package, params if params is not None else adapter.default_params(game),
                            self.context(adapter, staging))
        return PreparedFiveMPlan(adapter.installation, adapter, plan, _plan_digest(plan), _binding_digest(adapter))

    def _stopped(self) -> None:
        try:
            busy = [p for p in self.process_reader() if classify_process(p.name) == "fivem"]
        except Exception as exc:
            raise PermissionError("Cannot establish that FiveM is stopped") from exc
        if busy:
            raise PermissionError("Close every FiveM client and GTAProcess before changing plugins")

    def install(self, prepared: PreparedFiveMPlan, current_policy: ServerPolicy, log_cb=None) -> TxResult:
        self._stopped()
        if _binding_digest(prepared.adapter) != prepared.binding_digest:
            raise PermissionError("Backend or server evidence changed after planning")
        if current_policy != prepared.adapter.policy:
            raise PermissionError("Server permission changed; prepare and review a new plan")
        if _plan_digest(prepared.plan) != prepared.digest:
            raise PermissionError("The reviewed installation plan was changed")
        fresh = inspect_installation(prepared.installation.root)
        if fresh is None or fresh.fingerprint != prepared.installation.fingerprint:
            raise PermissionError("FiveM files changed after planning; inspect and prepare again")
        a = self.adapter(fresh, prepared.adapter.delegate.id, current_policy,
                         prepared.adapter.review, prepared.adapter.package)
        result = self.compatibility(a)
        if result.blockers:
            raise PermissionError("; ".join(f.message for f in result.blockers))
        if Path(prepared.plan.target_dir).resolve() != a.target.resolve():
            raise PermissionError("Plan target is not the inspected FiveM plugin folder")
        guard_target(a.target, fresh.app_dir, [o.rel_path for o in prepared.plan.ops])
        # Pin every source again, including runtimes; Installer does a further pre-copy check.
        for op in prepared.plan.ops:
            if op.source is not None and (not op.expected_sha256 or op.source.is_symlink()
                    or sha256_file(op.source) != op.expected_sha256):
                raise PermissionError(f"Source changed after planning: {op.rel_path}")
        created = not a.target.exists()
        if created:
            a.target.mkdir()  # app directory already exists; no recursive directory creation
        try:
            applied = self.services.installer.apply(fresh.game, prepared.plan, log_cb)
            if applied.ok:
                installed = {f["rel_path"]: f["installed_sha256"]
                             for f in self.services.db.tx_files(applied.tx_id)}
                try:
                    problems = [f for f in a.validate(fresh.game, installed) if f.severity == Severity.BLOCKER]
                except (OSError, ValueError) as exc:
                    problems = [Finding(Severity.BLOCKER, "fivem_validation", str(exc))]
                if problems:
                    restored = self.services.installer.restore(applied.tx_id, game_running_dir=str(fresh.root))
                    return TxResult(False, applied.tx_id, "rolled_back" if restored.ok else "failed",
                                    ["Validation failed: " + "; ".join(f.message for f in problems), *restored.messages])
            return applied
        finally:
            if created:
                try:
                    a.target.rmdir()  # remove only an empty directory after failure/rollback
                except OSError:
                    pass

    def restore(self, installation: FiveMInstallation, tx_id: int, force: bool = False) -> TxResult:
        self._stopped()
        tx = self.services.db.get_transaction(tx_id)
        if not tx or tx["game_id"] != installation.game.id or not tx["backend_id"].startswith("fivem_legacy:"):
            raise PermissionError("Transaction does not belong to this FiveM integration")
        target = installation.plugin_dir
        if target.is_symlink() or Path(tx["target_dir"]).resolve() != target.resolve() or target.resolve().parent != installation.app_dir:
            raise PermissionError("Restore target changed or escaped the client")
        guard_target(target, installation.app_dir, [f["rel_path"] for f in self.services.db.tx_files(tx_id)])
        # Restore does not require renewed backend/server approval: it removes the modification.
        return self.services.installer.restore(tx_id, force=force, game_running_dir=str(installation.root))

    def snapshot(self, installation: FiveMInstallation) -> TxResult:
        self._stopped()
        target = installation.plugin_dir
        if target.is_symlink() or target.resolve().parent != installation.app_dir:
            raise PermissionError("Unsafe snapshot target")
        paths = [str(f.path.relative_to(target)) for f in installation.inventory if f.path.is_relative_to(target)]
        guard_target(target, installation.app_dir, paths)
        return self.services.installer.snapshot(installation.game, str(target), paths, "FiveM plugin inventory")

    def restore_snapshot(self, installation: FiveMInstallation, tx_id: int, force: bool = False) -> TxResult:
        self._stopped()
        tx = self.services.db.get_transaction(tx_id)
        if not tx or tx["game_id"] != installation.game.id or tx["kind"] != "snapshot":
            raise PermissionError("Snapshot does not belong to this FiveM client")
        if installation.plugin_dir.is_symlink() or Path(tx["target_dir"]).resolve() != installation.plugin_dir.resolve():
            raise PermissionError("Snapshot target changed")
        guard_target(installation.plugin_dir, installation.app_dir,
                     [f["rel_path"] for f in self.services.db.tx_files(tx_id)])
        return self.services.installer.restore_snapshot(tx_id, force)
