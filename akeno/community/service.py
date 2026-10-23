"""Offline-only experiments through existing adapters and the transactional installer."""
from __future__ import annotations
from akeno.i18n import msg

from dataclasses import asdict, dataclass
import hashlib
import configparser
import json
import re
import time
import threading
from pathlib import Path

from akeno.backends import runtimes
from akeno.backends.base import BackendContext, FileOp, InstallPlan
from akeno.community.runtime import Category, RuntimeManager, regular, regular_game_file
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
        "target": plan.target_dir, "kind": plan.kind, "params": plan.params, "expected_originals": plan.expected_originals, "renames": plan.rename_away,
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
        self.seen_live = set()
        self.sessions = {}
        self.session_history = {}
        self._monitor_lock = threading.RLock()

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
        regular_game_file(exe)
        if not exe.resolve().is_relative_to(install) or exe.resolve().is_relative_to(app_home().resolve()):
            raise PermissionError(msg("The game executable must be inside its own installation."))
        info = pe.parse(str(exe))
        if not info.valid or info.is_dll or info.machine != "x64" or game.architecture != "x64":
            raise PermissionError(msg("The game must have a valid x64 executable."))
        adapter = self.s.registry.get(backend_id)
        if adapter is None or backend_id not in ("optiscaler_nr", "dlss5_feeder"):
            raise PermissionError(msg("A supported neural backend is required."))
        if not self.s.system or not (self.s.system.os_name or '').lower().startswith('windows') or self.s.system.is_64bit is not True:
            raise PermissionError('Experimental deployment requires a detected 64-bit Windows environment.')
        gpu = self.s.selected_gpu()
        if not gpu or gpu.vendor != Vendor.NVIDIA or gpu.has_tensor_cores is not True or gpu.is_software or not re.fullmatch(r"\d{3}\.\d{2}", gpu.driver_version or ""):
            raise PermissionError(msg("A detected RTX GPU and driver are required."))
        if game.api not in adapter.supported_apis or game.api == GraphicsApi.UNKNOWN:
            raise PermissionError(msg("The selected renderer is not supported by this backend."))
        if candidate.category not in (Category.COMMUNITY, Category.OFFICIAL, Category.ADVANCED):
            raise PermissionError(candidate.detail)
        if candidate.review and candidate.review.get("schema") == 3:
            from akeno.community.profiles import eligibility
            eligibility(candidate.review, gpu, backend_id, game.api.value)
        elif candidate.category == Category.COMMUNITY:
            from akeno.community import candidates
            named = candidates.by_hash(candidate.sha256)
            if named and gpu.device_id not in named.experimental_device_ids:
                raise PermissionError(msg("The detected GPU is outside the named candidate's unverified RTX 4080 profile."))
            targets = candidate.review["targets"]
            match = [t for t in targets if (t["generation"], t["driver"], t["backend"], t["api"]) ==
                     (gpu.generation, gpu.driver_version, backend_id, game.api.value)]
            if named:
                match = [t for t in match if t.get("gpu_device_id") == gpu.device_id]
            if not match or any(t["status"] == "incompatible" for t in match):
                raise PermissionError(msg("No eligible exact runtime/GPU/driver/backend/renderer review target."))
        from akeno.hardware.support import runtime_verdict
        verdict = runtime_verdict(candidate.version, gpu.generation, self.s.compat_db.nr_runtimes(),
            build_sha256=candidate.sha256)
        if verdict.state == 'fails':
            raise PermissionError(verdict.text)
        rt = {"stored_path": candidate.path, "sha256": candidate.sha256, "version": candidate.version}
        lookup = lambda name: rt if name == runtimes.NR_RUNTIME else self.s.runtime_lookup(name)
        managed = self.s.installer.managed_files(game.id, game.target_dir)
        from akeno.pipeline.proxy import observed_loader_paths
        ctx = BackendContext(self.s.system, gpu, lookup, staging, managed, strict_conflicts=True,
                             loaded_modules=tuple(observed_loader_paths(self.s,game)))
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

    def runtime_record(self, sha):
        data=self.s.db.get_setting('community-runtime:'+str(sha))
        if data:return data
        item=self.s.db.get_setting('official_dll_inventory',{}).get(sha)
        if item and item.get('name')==runtimes.NR_RUNTIME and item.get('family')=='neural_rendering' and item.get('trust')=='nvidia_authenticated':
            return dict(item,path=item['stored_path'],category=Category.OFFICIAL.value,
                source='Explicitly imported official component; signature rechecked before execution.')
        for row in self.s.db.runtimes(runtimes.NR_RUNTIME):
            if row.get('sha256')==sha and row.get('trust')=='verified' and row.get('stored_path'):
                return dict(row,path=row['stored_path'],category=Category.OFFICIAL.value)
        return None

    def _candidate(self, sha):
        data = self.runtime_record(sha)
        if not data:
            raise PermissionError(msg("Import the reviewed runtime before planning."))
        result = self.runtimes.inspect(data["path"], data.get("review"))
        if result.sha256 != sha:
            raise PermissionError(msg("The imported runtime changed."))
        if data.get('category')==Category.OFFICIAL.value and result.category!=Category.OFFICIAL:
            raise PermissionError('The selected official runtime no longer has an intact NVIDIA signature.')
        return result

    def _environment(self, game, plan):
        paths = {game.exe_path, *(m.path for m in game.existing_mods)}
        paths.update(str(Path(plan.target_dir) / o.rel_path) for o in plan.ops)
        files = []
        for name in sorted(paths):
            p = Path(name)
            if p.exists():
                regular_game_file(p)
                files.append((str(p.resolve()), sha256_file(p)))
            else:
                files.append((str(p.absolute()), None))
        return digest({"game": game.id, "install": str(Path(game.install_dir).resolve()), "exe": game.exe_path,
            "api": game.api.value, "inputs": game.upscaler_inputs, "files": files,
            "gpu": self.s.selected_gpu().to_dict() if self.s.selected_gpu() else None})

    def prepare(self, game_id, sha, backend_id, params, offline_confirmed=False, consent=False, staging=None):
        game, candidate = self._game(game_id), self._candidate(sha)
        adapter, ctx = self._check(game, candidate, backend_id, offline_confirmed, consent)
        package = self.s.package_for(adapter,game)
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
        if self.s.package_for(adapter,game) != prepared.plan.package:
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

    def launch_test(self, game_id, offline_confirmed=False, consent=False):
        """Explicit local action: pin observation before asking the normal OS launcher to run."""
        from akeno.paths import is_windows
        if not is_windows():raise PermissionError(msg('Game launch testing requires Windows.'))
        game=self._game(game_id);tx=self.s.installer.active_install(game_id)
        if not tx or not tx['backend_id'].startswith('community:'):
            raise PermissionError(msg('Install an experimental plan before starting its observation.'))
        candidate=self._candidate(tx['params'].get('_community_runtime',''))
        backend=tx['backend_id'].split(':')[-1]
        self._check(game,candidate,backend,offline_confirmed,consent)
        # Signature, driver/profile and file checks run before launch; observation hashes
        # every installed artifact and records existing log cursors first.
        report=self.observe(game_id)
        try:
            if game.launcher=='steam' and re.fullmatch(r'[0-9]+',str(game.launcher_id or '')):
                import os
                os.startfile('steam://rungameid/'+str(game.launcher_id))
                method='normal_steam_launcher'
            else:
                import subprocess
                subprocess.Popen([str(Path(game.exe_path).resolve())],cwd=str(Path(game.exe_path).parent))
                method='normal_executable_launch'
        except Exception:
            self.stop_observation(game_id)
            raise
        return {'launch_requested':True,'method':method,'report':report,
            'note':'Offline mode must be configured in the game/launcher. A launch request is not NR or gameplay evidence.'}

    def observe(self, game_id):
        # One full verification at this explicit session boundary. Polls use cached game/file identities.
        game = self._game(game_id)
        tx = self.s.installer.active_install(game.id)
        if not tx or not tx["backend_id"].startswith("community:"):
            raise PermissionError(msg("Install an experimental plan before starting its observation."))
        files = [f for f in self.s.db.tx_files(tx['id']) if f['action'] != 'rename_away']
        expected = {f['rel_path']: f['installed_sha256'] for f in files}
        # A game's overlay may legitimately save INI settings. Hash and report the effective config,
        # but keep binary/marker pins mandatory. Deployment and restore policies are unchanged.
        binary_pins = {name: pin for name,pin in expected.items() if name.casefold() != 'optiscaler.ini'}
        observation = neural.begin(game.id, game.target_dir, game.exe_path, list(expected), binary_pins, diagnostic_builds=([tx['params']['_diagnostic_build']] if tx['params'].get('_diagnostic_build') else []),
            diagnostic_modules=([tx['params']['_diagnostic_module']] if tx['params'].get('_diagnostic_module') in expected else []))
        from akeno.diagnostics.session import VerificationSession
        with self._monitor_lock:
            previous = self.sessions.get(game_id)
            if previous:
                self.session_history.setdefault(game_id, []).append(previous.summary())
                self.session_history[game_id] = self.session_history[game_id][-10:]
            live = processes.processes_in_dir(game.install_dir)
            exact = [p for p in live if p.path and str(Path(p.path).resolve()) == observation.executable]
            self.sessions[game_id] = VerificationSession(observation, tx, bool(exact))
            self.sessions[game_id].installation_hashes = expected
            self.observations[game_id] = observation
            self.seen_live.discard(game_id)
        return self.report(game_id)

    def stop_observation(self, game_id):
        with self._monitor_lock:
            session = self.sessions.get(game_id)
            if session and not session.ended:
                session.state = 'stopped'; session.ended = True
        return self.report(game_id)

    def capture_context(self, game_id, pid=None):
        """Snapshot only what is known at capture time, never retrofit it to old CSVs."""
        game = self.s.library.get(game_id)
        tx = self.s.installer.active_install(game_id)
        session = self.sessions.get(game_id)
        identity = session.process if session and not session.ended else None
        unchanged = False
        if session:
            try:
                unchanged = all(neural.identity(name) == expected for name, expected in session.observation.file_identities)
                if identity and processes.creation_identity(identity['pid']) != identity['created_at']:
                    identity = None
            except (OSError, ValueError): pass
        if pid is not None and (not identity or identity['pid'] != pid):
            identity = None
        return {'game_id': game_id, 'transaction_id': tx['id'] if tx else None,
            'transaction_created_at': tx['created_at'] if tx else None,
            'runtime_sha256': tx['params'].get('_community_runtime') if tx else None,
            'backend_id': tx['backend_id'] if tx else None,
            'package_id': tx.get('package_id') if tx else None,
            'package_version': tx.get('package_version') if tx else None,
            'observation_id': session.id if session and not session.ended else None,
            'process': identity, 'renderer': game.api.value if game else None,
            'gpu': self.s.selected_gpu().to_dict() if self.s.selected_gpu() else None,
            'effective_backend_settings': getattr(session, 'effective_settings', {}),
            'artifact_hashes_at_session_start': dict(session.observation.artifacts) if session else {},
            'confirmed': bool(session and identity and identity.get('created_at') and not session.ended
                and unchanged and session.monitor.stale is None and tx and session.transaction['id'] == tx['id'])}

    def report(self, game_id):
        started = time.perf_counter()
        with self._monitor_lock:
            return self._report_locked(game_id, started)

    def _report_locked(self, game_id, started):
        # Deliberately do not call reanalyze(): no PE parsing, library walk or runtime hashing per tick.
        game = self.s.library.get(game_id)
        if game is None:
            raise PermissionError(msg("Select an existing analyzed game."))
        tx = self.s.installer.active_install(game.id)
        observation = self.observations.get(game_id)
        session = self.sessions.get(game_id)
        live = processes.processes_in_dir(game.install_dir) if not session or not session.ended else []
        if session:
            evidence = session.poll(live, tx) or neural.NeuralEvidence('stale', detail='Installation changed.')
        else:
            evidence = neural.inspect(game.target_dir)
        exact_live = bool(session and session.process and not session.ended) if session else any(p.path and str(Path(p.path).resolve()) == str(Path(game.exe_path).resolve()) for p in live)
        if exact_live: self.seen_live.add(game.id)
        stability = 'process_exit_observed' if session and session.state in ('game_exited','game_restarted') else ('process_observed' if exact_live else ('process_exit_observed' if game.id in self.seen_live else 'not_observed'))
        sessions = self.s.benchmarks.sessions(game.id)
        groups = self.s.benchmarks.attributed_groups(sessions, tx, session.id if session else None)
        current_ids = [s['id'] for s in groups['current_installation']]
        # Diagnostics read only the small effective configuration, never load any binary.
        config = None
        cfg = Path(game.target_dir) / 'OptiScaler.ini'
        try:
            if cfg.is_file() and not cfg.is_symlink() and cfg.stat().st_size <= 64 * 1024:
                config = cfg.read_text(encoding='utf-8-sig', errors='replace')
        except OSError:
            pass
        findings, effective = [], {}
        if config is not None:
            try:
                parsed = configparser.ConfigParser(interpolation=None, strict=False)
                parsed.read_string(config)
                for section, key in (('DlssNr','Enabled'), ('DlssNr','PreSr'), ('Upscalers','Dx12Upscaler'), ('Log','LogToFile'), ('Log','LogFileName')):
                    if parsed.has_option(section,key): effective[section+'.'+key] = parsed.get(section,key)
                if effective.get('DlssNr.Enabled','').casefold() in ('false','0','no'):
                    findings.append({'code':'nr_disabled_in_config', 'detail':'The effective [DlssNr] Enabled value is false; no NR evaluation is expected.'})
                if effective.get('Log.LogToFile','').casefold() in ('false','0','no'):
                    findings.append({'code':'backend_file_logging_disabled', 'detail':'The effective configuration disables file logging; log-based verification may be unavailable.'})
            except configparser.Error as error:
                findings.append({'code':'configuration_parse_error','detail':str(error)})
        if session: session.effective_settings = effective
        runtime_record = self.runtime_record(tx['params'].get('_community_runtime','')) if tx else None
        if runtime_record and runtime_record.get('category') == 'reviewed_community':
            runtime_record = dict(runtime_record, category=Category.COMMUNITY.value,
                review_scope='Legacy source/consent record; no independent security certification.',
                independent_security_review_verified=False)
        if evidence.execution:
            evidence.execution['gpu']=self.s.selected_gpu().to_dict() if self.s.selected_gpu() else {}
            if runtime_record:
                evidence.execution['runtime']={k:runtime_record.get(k) for k in ('sha256','version','category','signature','source','signer')}
        report = {'game': dict(game.to_dict(), is_running=exact_live),
            'gpu': self.s.selected_gpu().to_dict() if self.s.selected_gpu() else None,
            'driver': self.s.selected_gpu().driver_version if self.s.selected_gpu() else None,
            'renderer': game.api.value, 'transaction': tx, 'neural': asdict(evidence),
            'runtime': runtime_record,
            'observation': asdict(observation) if observation else None,
            'session': session.summary() if session else {'state':'ready', 'monitoring_active':False},
            'historical_observations': self.session_history.get(game_id, []),
            'measured_sessions': groups['current_installation'], 'benchmark_groups': groups,
            'current_session_benchmark_ids': [s['id'] for s in groups['current_session']],
            'current_installation_benchmark_ids': current_ids,
            'benchmark_status': 'Current installation measurements available.' if current_ids else 'No benchmark has been captured for the current installation.',
            'measurement_attribution': 'Historical benchmarks are not automatically matched to the current installation; baseline and unconfirmed captures are separated.',
            'installed_files': [{'name': n, 'sha256': h,
                'matches_installation': h == getattr(session, 'installation_hashes', {}).get(n), 'verification': 'fully hashed at session start; metadata watched thereafter'} for n,h in observation.artifacts] if observation else [],
            'backend_configuration': config, 'effective_settings': effective, 'diagnostic_findings': findings,
            'expected_proxy': next((n for n,h in observation.artifacts if n.lower() in ('winmm.dll','dxgi.dll','version.dll','optiscaler.asi')), None) if observation else None,
            'missing_events': [k for k,v in evidence.stages.items() if not v],
            'stability': {'state': stability, 'crash_verified': False,
                'errors': [line for line in evidence.lines if line['stage'] == 'failed'],
                'note': 'A process exit is not a verified crash.'},
            'monitoring_overhead': dict(evidence.overhead, collection_ms=round((time.perf_counter()-started)*1000,3)),
            'limitations': ['Cached metadata checks are not fresh cryptographic verification.',
                'No actual GPU execution is inferred from installed files or FPS.',
                'Read-only module enumeration can be unavailable due to process permissions.',
                'Skynizz OptiScaler 1.3.0 default DX12 success path does not emit an explicit per-frame evaluation success log; absence of a recognized success event is inconclusive.']}
        if observation:
            from akeno.diagnostics.support import safe_report
            from akeno.paths import sub
            destination = sub('reports') / ('experiment-' + digest(game.id)[:16] + '.json')
            public = safe_report(report)
            # Unchanged evidence does not rewrite the report every three seconds.
            stable = json.loads(json.dumps(public, default=str)); stable.pop('monitoring_overhead', None)
            stable.get('session', {}).pop('polls', None)
            stable.get('neural', {}).pop('overhead', None)
            stable.get('neural', {}).get('execution', {}).pop('session_duration_s',None)
            pin = digest(stable)
            if getattr(session, '_saved_pin', None) != pin:
                temporary = destination.with_suffix('.tmp')
                temporary.write_text(json.dumps(public, indent=2, default=str), encoding='utf-8')
                temporary.replace(destination)
                if session: session._saved_pin = pin
        report['monitoring_overhead']['report_ms'] = round((time.perf_counter()-started)*1000,3)
        return report
