"""Offline-only experiments through existing adapters and the transactional installer."""
from __future__ import annotations
from akeno.i18n import msg

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

from akeno.backends import runtimes
from akeno.backends.base import BackendContext, FileOp, InstallPlan
from akeno.community.runtime import Category, RuntimeManager, regular
from akeno.core import processes
from akeno.core.models import AntiCheatRisk, CompatStatus, GraphicsApi, Vendor
from akeno.diagnostics import neural
from akeno.games import pe
from akeno.security.downloads import sha256_file
from akeno.paths import app_home


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def plan_digest(plan: InstallPlan) -> str:
    return digest({"backend": plan.backend_id, "package": asdict(plan.package) if plan.package else None,
        "target": plan.target_dir, "kind": plan.kind, "params": plan.params, "renames": plan.rename_away,
        "ops": [(o.rel_path, str(o.source), o.expected_sha256,
                 hashlib.sha256(o.content).hexdigest() if o.content is not None else None, o.kind) for o in plan.ops]})


@dataclass(frozen=True)
class PreparedExperiment:
    game_id: str
    runtime_sha256: str
    backend_id: str
    plan: InstallPlan
    plan_pin: str
    environment_pin: str
    offline_confirmed: bool
    consent: bool


class CommunityService:
    def __init__(self, services, manager=None):
        self.s = services
        self.runtimes = manager or RuntimeManager(services.db)
        self.observations = {}

    def _game(self, game_id):
        game = self.s.library.reanalyze(game_id)
        if game is None:
            raise PermissionError(msg("Select an existing analyzed game."))
        return game

    def _check(self, game, candidate, backend_id, offline_confirmed, consent, staging=None):
        if offline_confirmed is not True or consent is not True:
            raise PermissionError(msg("Explicit offline/single-player confirmation and experimental consent are required."))
        if game.launcher == "fivem" or "gta5" in Path(game.exe_path or "").name.lower() or "gta online" in game.name.lower():
            raise PermissionError(msg("This community route does not support FiveM, GTA Online or ambiguous GTA sessions."))
        if game.anti_cheat != AntiCheatRisk.NONE_FOUND or self.s.compat_db.anti_cheat_entry(game):
            raise PermissionError(msg("Multiplayer/anti-cheat status is blocked or unresolved."))
        if game.is_running or self.s.installer.running_check(game.install_dir):
            raise PermissionError(msg("Close the game before planning or installing."))
        if not game.exe_path or not game.target_dir or not Path(game.install_dir).is_dir():
            raise PermissionError(msg("The game location is unresolved."))
        exe, install = Path(game.exe_path), Path(game.install_dir).resolve()
        regular(exe)
        if not exe.resolve().is_relative_to(install) or exe.resolve().is_relative_to(app_home().resolve()):
            raise PermissionError(msg("The game executable must be inside its own installation."))
        info = pe.parse(str(exe))
        if not info.valid or info.is_dll or info.machine != "x64" or game.architecture != "x64":
            raise PermissionError(msg("The game must have a valid x64 executable."))
        adapter = self.s.registry.get(backend_id)
        if adapter is None or backend_id not in ("optiscaler_nr", "dlss5_feeder"):
            raise PermissionError(msg("A supported neural backend is required."))
        gpu = self.s.selected_gpu()
        if not gpu or gpu.vendor != Vendor.NVIDIA or gpu.has_tensor_cores is not True or not gpu.driver_version:
            raise PermissionError(msg("A detected RTX GPU and driver are required."))
        if game.api not in adapter.supported_apis or game.api == GraphicsApi.UNKNOWN:
            raise PermissionError(msg("The selected renderer is not supported by this backend."))
        if candidate.category not in (Category.COMMUNITY, Category.OFFICIAL):
            raise PermissionError(candidate.detail)
        if candidate.category == Category.COMMUNITY:
            targets = candidate.review["targets"]
            match = [t for t in targets if (t["generation"], t["driver"], t["backend"], t["api"]) ==
                     (gpu.generation, gpu.driver_version, backend_id, game.api.value)]
            if not match or any(t["status"] == "incompatible" for t in match):
                raise PermissionError(msg("No eligible exact runtime/GPU/driver/backend/renderer review target."))
        rt = {"stored_path": candidate.path, "sha256": candidate.sha256, "version": candidate.version}
        lookup = lambda name: rt if name == runtimes.NR_RUNTIME else self.s.runtime_lookup(name)
        managed = self.s.installer.managed_files(game.id, game.target_dir)
        ctx = BackendContext(self.s.system, gpu, lookup, staging, managed, strict_conflicts=True)
        compat = self.s.compat_db.entries_for(game, backend_id)
        if any(e["status"] in (CompatStatus.BLOCKED.value, CompatStatus.INCOMPATIBLE.value) for e in compat):
            raise PermissionError(msg("The selected game/backend has a blocking compatibility record."))
        findings = adapter.check(game, ctx)
        blockers = [f.message for f in findings if f.severity.value == "blocker"]
        if blockers:
            raise PermissionError("\n".join(blockers))
        # Foreign runtimes may not piggyback on a reviewed import.
        nr = Path(game.target_dir) / runtimes.NR_RUNTIME
        if nr.exists() and runtimes.NR_RUNTIME not in managed:
            raise PermissionError(msg("An unmanaged neural runtime already exists; inspect it without replacing it."))
        return adapter, ctx

    def _candidate(self, sha):
        data = self.s.db.get_setting("community-runtime:" + sha)
        if not data:
            raise PermissionError(msg("Import the reviewed runtime before planning."))
        result = self.runtimes.inspect(data["path"], data.get("review"))
        if result.sha256 != sha:
            raise PermissionError(msg("The imported runtime changed."))
        return result

    def _environment(self, game, plan):
        paths = {game.exe_path, *(m.path for m in game.existing_mods)}
        paths.update(str(Path(plan.target_dir) / o.rel_path) for o in plan.ops)
        files = []
        for name in sorted(paths):
            p = Path(name)
            if p.exists():
                regular(p)
                files.append((str(p.resolve()), sha256_file(p)))
            else:
                files.append((str(p.absolute()), None))
        return digest({"game": game.id, "install": str(Path(game.install_dir).resolve()), "exe": game.exe_path,
            "api": game.api.value, "inputs": game.upscaler_inputs, "files": files,
            "gpu": self.s.selected_gpu().to_dict() if self.s.selected_gpu() else None})

    def prepare(self, game_id, sha, backend_id, params, offline_confirmed=False, consent=False, staging=None):
        game, candidate = self._game(game_id), self._candidate(sha)
        adapter, ctx = self._check(game, candidate, backend_id, offline_confirmed, consent)
        package = self.s.package_for(adapter)
        if not package or not package.installable:
            raise PermissionError(msg("A reviewed, pinned backend package is required."))
        # An optional staging argument is for existing verified staging/tests; all members are rehashed below.
        staging = Path(staging) if staging is not None else self.s.stage(game, adapter, package)
        for name, pin in adapter.required_package_files(game, package).items():
            p = staging / name
            regular(p)
            if not pin or sha256_file(p) != pin:
                raise PermissionError(msg("The backend package does not match its reviewed member hashes."))
        ctx.staging_dir = staging
        clean_params = {s.key: s.coerce(params.get(s.key, s.default)) for s in adapter.param_specs(game)}
        plan = adapter.plan(game, package, clean_params, ctx)
        # Preserve SR requirements independently; a community NR file cannot satisfy SR's official gate.
        if any(k == "dlss_runtime" and required for k, required in adapter.dependency_specs(game)):
            sr = self.s.runtime_lookup(runtimes.DLSS_RUNTIME)
            if not sr:
                raise PermissionError(msg("This backend also needs an independently verified Super Resolution runtime."))
            signed = runtimes.evaluate(sr["stored_path"], runtimes.DLSS_RUNTIME, verifier=self.runtimes.verifier)
            if signed.trust.value != "verified":
                raise PermissionError(msg("The Super Resolution runtime must pass the official NVIDIA gate."))
            plan.ops.append(FileOp(runtimes.DLSS_RUNTIME, source=Path(sr["stored_path"]), expected_sha256=signed.sha256))
        plan.backend_id = "community:" + backend_id
        plan.params.update(_community_runtime=sha, _trust_category=candidate.category.value,
                           _review=candidate.review, _offline_attested=True)
        plan.summary.extend(["Offline experimental deployment; no GPU execution is claimed.", candidate.detail])
        for op in plan.ops:
            dest = Path(plan.target_dir) / op.rel_path
            if not dest.resolve().is_relative_to(Path(plan.target_dir).resolve()):
                raise PermissionError(msg("The plan attempts to escape the game target."))
            if dest.exists() and op.rel_path.lower() not in ctx.managed_files:
                raise PermissionError(msg("The plan conflicts with an unmanaged game file."))
            if dest.with_name(dest.name + ".akeno-tmp").exists():
                raise PermissionError(msg("A foreign transaction temporary file exists."))
        return PreparedExperiment(game.id, sha, backend_id, plan, plan_digest(plan),
                                  self._environment(game, plan), offline_confirmed, consent)

    def install(self, prepared, offline_confirmed=False, consent=False):
        if not prepared.consent or not prepared.offline_confirmed or plan_digest(prepared.plan) != prepared.plan_pin:
            raise PermissionError(msg("The prepared experiment was changed."))
        game, candidate = self._game(prepared.game_id), self._candidate(prepared.runtime_sha256)
        adapter, _ = self._check(game, candidate, prepared.backend_id, offline_confirmed, consent)
        if self._environment(game, prepared.plan) != prepared.environment_pin:
            raise PermissionError(msg("Game files, renderer or hardware changed; prepare a fresh plan."))
        if self.s.package_for(adapter) != prepared.plan.package:
            raise PermissionError(msg("The selected backend package changed."))
        for op in prepared.plan.ops:
            if op.source:
                regular(Path(op.source))
                if not op.expected_sha256 or sha256_file(op.source) != op.expected_sha256:
                    raise PermissionError(msg("A staged source changed; prepare a fresh plan."))
        result = self.s.installer.apply(game, prepared.plan)
        if result.ok:
            written = {f["rel_path"]: f["installed_sha256"] for f in self.s.db.tx_files(result.tx_id)}
            problems = [f for f in adapter.validate(game, written) if f.severity.value == "blocker"]
            for op in prepared.plan.ops:
                expected = op.expected_sha256 or hashlib.sha256(op.content or b"").hexdigest()
                if written.get(op.rel_path) != expected:
                    from akeno.core.models import Finding, Severity
                    problems.append(Finding(Severity.BLOCKER, "post_copy_hash", "An installed file failed its exact hash check."))
            if problems:
                rollback = self.s.installer.restore(result.tx_id, force=True)
                result.ok, result.state = False, "rolled_back" if rollback.ok else "failed"
                result.messages.extend(rollback.messages)
                result.messages.extend(f.message for f in problems)
        return result

    def observe(self, game_id):
        game = self._game(game_id)
        tx = self.s.installer.active_install(game.id)
        if not tx or not tx["backend_id"].startswith("community:"):
            raise PermissionError(msg("Install an experimental plan before starting its observation."))
        names = [f["rel_path"] for f in self.s.db.tx_files(tx["id"]) if f["action"] != "rename_away"]
        self.observations[game_id] = neural.begin(game.id, game.target_dir, game.exe_path, names)
        return self.report(game_id)

    def report(self, game_id):
        game = self._game(game_id)
        observation = self.observations.get(game_id)
        live = [p.path for p in processes.processes_in_dir(game.install_dir) if p.path]
        evidence = neural.inspect(game.target_dir, observation, game.id, live)
        sessions = self.s.benchmarks.sessions(game.id)
        tx = self.s.installer.active_install(game.id)
        return {"game": game.to_dict(), "gpu": self.s.selected_gpu().to_dict() if self.s.selected_gpu() else None,
            "driver": self.s.selected_gpu().driver_version if self.s.selected_gpu() else None,
            "renderer": game.api.value, "transaction": tx, "neural": asdict(evidence),
            "runtime": self.s.db.get_setting("community-runtime:" + tx["params"].get("_community_runtime", "")) if tx else None,
            "observation": asdict(observation) if observation else None, "measured_sessions": sessions,
            "limitations": ["User offline attestation is not live session detection.",
                "Log attribution is not cryptographic GPU execution proof.",
                "No absence of errors, file install, SR or FG success proves neural evaluation."]}
