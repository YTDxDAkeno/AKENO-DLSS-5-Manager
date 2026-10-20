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
from akeno.core.models import CompatibilityResult, CompatStatus, Severity
from akeno.install.transaction import TxResult
from akeno.security.downloads import sha256_file
from .adapter import FiveMLegacyAdapter
from .compatibility import BackendReview
from .discovery import classify_process, discover, inspect_installation
from .models import FiveMInstallation, ServerPolicy


def _plan_digest(plan: InstallPlan) -> str:
    data = {"backend": plan.backend_id, "target": plan.target_dir, "kind": plan.kind,
            "package": vars(plan.package) if plan.package else None,
            "rename": plan.rename_away, "params": plan.params,
            "ops": [(o.rel_path, str(o.source) if o.source else None,
                     hashlib.sha256(o.content).hexdigest() if o.content is not None else None,
                     o.expected_sha256, o.kind) for o in plan.ops]}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class PreparedFiveMPlan:
    installation: FiveMInstallation
    adapter: FiveMLegacyAdapter
    plan: InstallPlan
    digest: str


class FiveMService:
    """Uses AppServices' registry, catalog, staging, compatibility DB and Installer.

    It is opt-in: no edits to default discovery, backend registration or launcher UI.
    Do not submit FiveM plans to the generic UI install path; use this facade so the
    identity, current server permission, process and fresh-file checks also execute.
    """
    def __init__(self, services, process_reader: Callable | None = None):
        self.services = services
        self.process_reader = process_reader or processes.list_processes

    def discover(self, extra_roots=()):
        return discover(extra_roots, self.process_reader())

    def adapter(self, installation: FiveMInstallation, backend_id: str, policy: ServerPolicy,
                review: BackendReview | None = None, package=None) -> FiveMLegacyAdapter:
        delegate = self.services.registry.get(backend_id)
        if delegate is None:
            raise ValueError("Unknown backend")
        package = package or self.services.package_for(delegate)
        return FiveMLegacyAdapter(delegate, installation, policy, package, review, self.services.compat_db)

    def context(self, adapter: FiveMLegacyAdapter, staging: Path | None = None) -> BackendContext:
        return BackendContext(system=self.services.system, gpu=self.services.selected_gpu(),
            runtime_lookup=self.services.runtime_lookup, staging_dir=staging,
            managed_files=self.services.installer.managed_files(adapter.installation.game.id, str(adapter.target)),
            strict_conflicts=True)

    def compatibility(self, adapter: FiveMLegacyAdapter) -> CompatibilityResult:
        result = evaluate(self.services.compat_db, adapter.installation.game, adapter, self.context(adapter))
        result.status = CompatStatus.BLOCKED if result.blockers or result.status in (
            CompatStatus.BLOCKED, CompatStatus.INCOMPATIBLE) else CompatStatus.EXPERIMENTAL
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
        return PreparedFiveMPlan(adapter.installation, adapter, plan, _plan_digest(plan))

    def _stopped(self) -> None:
        try:
            busy = [p for p in self.process_reader() if classify_process(p.name) == "fivem"]
        except Exception as exc:
            raise PermissionError("Cannot establish that FiveM is stopped") from exc
        if busy:
            raise PermissionError("Close every FiveM client and GTAProcess before changing plugins")

    def install(self, prepared: PreparedFiveMPlan, current_policy: ServerPolicy, log_cb=None) -> TxResult:
        self._stopped()
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
                problems = [f for f in a.validate(fresh.game, installed) if f.severity == Severity.BLOCKER]
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
        # Restore does not require renewed backend/server approval: it removes the modification.
        return self.services.installer.restore(tx_id, force=force, game_running_dir=str(installation.root))

    def snapshot(self, installation: FiveMInstallation) -> TxResult:
        self._stopped()
        target = installation.plugin_dir
        if target.is_symlink() or target.resolve().parent != installation.app_dir:
            raise PermissionError("Unsafe snapshot target")
        paths = [str(f.path.relative_to(target)) for f in installation.inventory if f.path.is_relative_to(target)]
        return self.services.installer.snapshot(installation.game, str(target), paths, "FiveM plugin inventory")

    def restore_snapshot(self, installation: FiveMInstallation, tx_id: int, force: bool = False) -> TxResult:
        self._stopped()
        tx = self.services.db.get_transaction(tx_id)
        if not tx or tx["game_id"] != installation.game.id or tx["kind"] != "snapshot":
            raise PermissionError("Snapshot does not belong to this FiveM client")
        if installation.plugin_dir.is_symlink() or Path(tx["target_dir"]).resolve() != installation.plugin_dir.resolve():
            raise PermissionError("Snapshot target changed")
        return self.services.installer.restore_snapshot(tx_id, force)
