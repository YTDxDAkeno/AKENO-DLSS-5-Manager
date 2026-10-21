"""Opt-in BackendAdapter bridge. No downloads, registry changes, or game launches."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any

from akeno.backends.base import BackendAdapter, BackendContext, InstallPlan
from akeno.compat.engine import CompatDatabase, evaluate
from akeno.core.models import Finding, Game, GraphicsApi, Severity
from akeno.games.analyzer import scan_dir_mods
from akeno.security.archive import normalize_member, ArchiveError
from akeno.security.catalog import Package
from akeno.security.downloads import sha256_file
from .compatibility import BackendReview, evidence_findings, policy_findings
from .models import FiveMInstallation, ServerPolicy
from .paths import guard_target


class FiveMLegacyAdapter(BackendAdapter):
    """Wrap an existing backend without modifying its registration or the launcher.

    A target view is used ONLY for planning/validation in FiveM.app/plugins. The real
    Game retains FiveM.exe as its executable and the full client as process scope.
    An exact reviewed output file set prevents generic proxy fallback locations.
    """
    experimental = True
    supported_apis = (GraphicsApi.D3D11,)
    supported_arch = ("x64",)

    def __init__(self, delegate: BackendAdapter, installation: FiveMInstallation,
                 policy: ServerPolicy, package: Package | None = None,
                 review: BackendReview | None = None, database: CompatDatabase | None = None, community_inspector=None):
        if delegate.id not in ("dlss5_feeder", "optiscaler_nr"):
            raise ValueError("Unsupported FiveM backend candidate")
        self.delegate, self.installation, self.policy = delegate, installation, policy
        self.community_inspector = community_inspector
        self.package, self.review = package, review
        self.database = database or CompatDatabase.load()
        self.id = "fivem_legacy:" + delegate.id
        self.name = "FiveM Legacy — " + delegate.name
        self.short = "FiveM " + delegate.short
        self.homepage, self.license = delegate.homepage, delegate.license
        self.description = "Experimental FiveM-specific evidence and server-policy gate. " + delegate.description

    @property
    def target(self) -> Path:
        # Only the documented plugin directory. No GTA root or proxy fallbacks.
        if self.review and self.review.target != "plugins":
            raise ValueError("Only FiveM.app/plugins is an eligible target")
        p = self.installation.plugin_dir
        guard_target(p, self.installation.app_dir)
        return p

    def target_game(self) -> Game:
        # This synthetic path supplies a directory to legacy backend planners;
        # it is never used for executable discovery or launching a process.
        g = replace(self.installation.game, exe_path=str(self.target / "FiveM-planning-only.exe"))
        g.existing_mods = scan_dir_mods(str(self.target))
        g.upscaler_inputs = [m.kind for m in g.existing_mods if m.kind in ("dlss", "fsr", "xess")]
        return g

    def _identity_findings(self, game: Game) -> list[Finding]:
        original = self.installation.game
        if (game.id, game.launcher, game.install_dir, game.exe_path) != (
                original.id, "fivem", original.install_dir, original.exe_path):
            return [Finding(Severity.BLOCKER, "fivem_identity", "The adapter is bound to one FiveM client; GTA V/Online and other clients are not targets.")]
        return []

    def check(self, game: Game, ctx: BackendContext) -> list[Finding]:
        findings = self._identity_findings(game)
        if self.review and self.review.backend_id != self.delegate.id:
            findings.append(Finding(Severity.BLOCKER, "fivem_review_backend", "The recipe names a different backend."))
        findings += policy_findings(self.policy, self.review, self.package)
        findings += evidence_findings(self.installation, ctx, self.review, self.package, self.community_inspector)
        findings.append(Finding(Severity.WARNING, "fivem_experimental",
            "Experimental FiveM integration: no RTX 4080 performance, VRAM-fit or ban-safety guarantee."))
        if self.installation.errors:
            return findings
        try:
            # Keep anti-cheat database blocks, hardware decisions and all underlying prerequisites.
            base = evaluate(self.database, self.target_game(), self.delegate, ctx)
            findings += base.findings
            if base.status.value in ("blocked", "incompatible") and not base.blockers:
                findings.append(Finding(Severity.BLOCKER, "fivem_base_compatibility", "Shared compatibility engine blocks this backend."))
        except (OSError, ValueError) as exc:
            findings.append(Finding(Severity.BLOCKER, "fivem_target", str(exc)))
        # Other hooks anywhere in the client need review; changing strict_conflicts cannot bypass this.
        for item in self.installation.inventory:
            in_target = item.path.is_relative_to(self.installation.plugin_dir)
            if item.kind in ("graphics_hook", "asi_plugin", "neural_consumer", "reshade_addon") and not in_target:
                findings.append(Finding(Severity.BLOCKER, "fivem_external_hook", f"Review the graphics hook outside the plugin target: {item.path}"))
        if self.review:
            approved = dict(self.review.prerequisite_hashes)
            for item in self.installation.inventory:
                if not item.path.is_relative_to(self.installation.plugin_dir):
                    continue
                rel = item.path.relative_to(self.installation.plugin_dir).as_posix().casefold()
                executable = item.path.suffix.lower() in (".dll", ".asi", ".addon", ".addon64", ".addon32")
                if executable and rel not in ctx.managed_files and approved.get(rel) != item.sha256:
                    findings.append(Finding(Severity.BLOCKER, "fivem_unreviewed_plugin",
                        f"Existing plugin is not covered by the recipe's exact prerequisite hashes: {rel}"))
        return findings

    def required_package_files(self, game: Game, package: Package) -> dict[str, str | None]:
        if self._identity_findings(game):
            raise ValueError("Wrong client")
        return self.delegate.required_package_files(self.target_game(), package)

    def param_specs(self, game: Game | None = None):
        return self.delegate.param_specs(game)

    def profiles(self, game: Game | None = None):
        return self.delegate.profiles(game)

    def default_params(self, game: Game | None):
        return self.delegate.default_params(game)

    def plan(self, game: Game, package: Package, params: dict[str, Any], ctx: BackendContext) -> InstallPlan:
        if self.package is None or package != self.package:
            raise PermissionError("Package differs from the reviewed adapter selection")
        blockers = [f for f in self.check(game, ctx) if f.severity == Severity.BLOCKER]
        if blockers:
            raise PermissionError("; ".join(f.message for f in blockers))
        if ctx.allow_disable:
            raise PermissionError("FiveM planning never renames/disables foreign plugins")
        wanted = self.required_package_files(game, package)
        if not wanted or ctx.staging_dir is None:
            raise ValueError("A staged package with pinned files is required")
        for rel, pin in wanted.items():
            normalized = normalize_member(rel)
            src = ctx.staging_dir / normalized
            if not pin or not src.is_file() or src.is_symlink() or not src.resolve().is_relative_to(ctx.staging_dir.resolve()):
                raise PermissionError(f"Unpinned or unsafe staged member: {rel}")
            if sha256_file(src) != pin:
                raise PermissionError(f"Staged member hash mismatch: {rel}")
        plan = self.delegate.plan(self.target_game(), package, params, ctx)
        plan.backend_id = self.id
        # Do not let a delegate pick a different target or disable another tool's files.
        if Path(plan.target_dir).resolve() != self.target.resolve() or plan.rename_away:
            raise PermissionError("Delegate escaped the approved FiveM target")
        guard_target(self.target, self.installation.app_dir, [o.rel_path for o in plan.ops])
        paths = []
        for op in plan.ops:
            rel = normalize_member(op.rel_path)
            if not rel or not (self.target / rel).resolve().is_relative_to(self.target.resolve()):
                raise PermissionError("Unsafe output path")
            paths.append(rel.casefold())
            if op.source is not None and not op.expected_sha256:
                raise PermissionError(f"Unpinned output: {rel}")
            if op.content is not None and op.kind not in ("config", "marker"):
                raise PermissionError("Only configuration and markers may be generated")
            if op.kind == "marker":
                marker = json.loads(op.content or b"{}")
                marker.update(backend=self.id, fiveM_server=self.policy.server_id,
                              experimental=True, review=self.review.source)
                op.content = json.dumps(marker, indent=2).encode()
        if len(set(paths)) != len(paths) or set(paths) != {p.casefold() for p in self.review.installed_paths}:
            raise PermissionError("Delegate outputs differ from the reviewed FiveM file layout")
        plan.summary.extend(["EXPERIMENTAL FiveM Legacy test; no real-hardware success claimed.",
                             f"Permission applies only to server {self.policy.server_id}.",
                             "Close all FiveM processes before apply; restore before joining another server.",
                             "Transactional backups and hash-checked restore use AKENO's existing installer."])
        return plan

    def validate(self, game: Game, installed: dict[str, str]) -> list[Finding]:
        findings = self._identity_findings(game)
        guard_target(self.target, self.installation.app_dir, installed)
        findings.extend(self.delegate.validate(self.target_game(), installed))
        for rel, expected in installed.items():
            try:
                name = normalize_member(rel)
                path = self.target / name
                if not path.resolve().is_relative_to(self.target.resolve()) or path.is_symlink() or sha256_file(path) != expected:
                    raise ValueError("missing or changed output")
            except (OSError, ValueError, ArchiveError) as exc:
                findings.append(Finding(Severity.BLOCKER, "fivem_validation", f"{rel}: {exc}"))
        return findings
