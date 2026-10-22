"""One process lifetime per verification observation; PID reuse never starts a second session."""
from dataclasses import asdict
from pathlib import Path
import time
import uuid
from akeno.core import processes
from akeno.diagnostics.neural import NeuralMonitor


class VerificationSession:
    def __init__(self, observation, transaction, already_running=False):
        self.id = uuid.uuid4().hex
        self.observation = observation
        self.transaction = transaction
        self.monitor = NeuralMonitor(observation)
        self.process = None
        self.state = 'waiting_for_game'
        self.ended = False
        self.already_running = already_running
        self.modules = {'state': 'unavailable', 'modules': [], 'detail': 'Game process has not been observed.'}
        self.module_checked = 0.0
        self.last_evidence = None
        self.verified = False
        self.polls = 0
        self.started = time.monotonic()

    def poll(self, live, transaction):
        if self.ended:
            return self.last_evidence
        if not transaction or transaction['id'] != self.transaction['id']:
            self.ended = True; self.state = 'installation_changed'
            if self.last_evidence:
                self.last_evidence.state = 'stale'
                self.last_evidence.stages['evaluations_attributed_to_game'] = False
            return self.last_evidence
        exact = [p for p in live if p.path and str(Path(p.path).resolve()) == self.observation.executable]
        selected = exact[0] if len(exact) == 1 else None
        birth = processes.creation_identity(selected.pid) if selected else None
        current = {'pid': selected.pid, 'created_at': birth, 'name': selected.name,
            'executable_sha256': self.observation.executable_sha256} if selected else None
        if self.process and current != self.process:
            self.ended = True
            self.state = 'game_restarted' if current else 'game_exited'
            return self.last_evidence
        if current and not self.process:
            self.process = current
        if selected and time.monotonic() - self.module_checked >= 15:
            self.modules = processes.module_inventory(selected.pid, birth)
            self.module_checked = time.monotonic()
        installed_paths = {str(Path(self.observation.target, n).resolve()) for n, _ in self.observation.artifacts}
        diagnostic_paths={str(Path(self.observation.target,name).resolve()) for name in self.observation.diagnostic_modules}
        diagnostic_loaded=any(str(Path(m['path']).resolve()) in diagnostic_paths for m in self.modules['modules'])
        evidence = self.monitor.poll([selected.path] if selected else [], attribution_allowed=bool(birth),
            process_identity=current,diagnostic_loaded=diagnostic_loaded)
        self.polls += 1
        installed_paths = {str(Path(self.observation.target, n).resolve()) for n, _ in self.observation.artifacts}
        loaded = [m['name'] for m in self.modules['modules'] if str(Path(m['path']).resolve()) in installed_paths]
        # Path-matched module presence does not imply NGX initialization or neural evaluation.
        backend = [name for name in loaded if name.casefold() != 'nvngx_dlss.dll']
        if backend:
            evidence.stages['backend_loaded'] = True
        if 'nvngx_dlssnr.dll' in [name.casefold() for name in loaded]:
            evidence.stages['runtime_loaded'] = True
        self.verified = evidence.state == 'verified'
        if evidence.state == 'stale': self.state = 'integrity_changed'; self.ended = True
        elif not selected: self.state = 'waiting_for_game'
        elif evidence.state == 'failed': self.state = 'neural_evaluation_failed'
        elif self.verified: self.state = 'neural_evaluation_detected'
        elif evidence.stages.get('backend_loaded'): self.state = 'neural_initialization_pending'
        else: self.state = 'backend_not_detected'
        if evidence.execution:
            evidence.execution['stages']['runtime_module_loaded']=evidence.stages.get('runtime_loaded',False)
            evidence.execution['session_duration_s']=round(time.monotonic()-self.started,3)
            evidence.execution['artifact_sha256']=dict(self.observation.artifacts)
            evidence.execution['executable_sha256']=self.observation.executable_sha256
            evidence.execution['consumer']={'diagnostic_builds':list(self.observation.diagnostic_builds),
                'module_sha256':{name:pin for name,pin in self.observation.artifacts if name in self.observation.diagnostic_modules}}
            evidence.execution['runtime']={'sha256':dict(self.observation.artifacts).get('nvngx_dlssnr.dll')}
        self.last_evidence = evidence
        return evidence

    def summary(self):
        return {'id': self.id, 'state': self.state, 'process': self.process, 'ended': self.ended,
            'monitoring_active': not self.ended, 'started_after_game_launch': self.already_running,
            'initialization_note': 'Monitoring started while the game was already running; restart or backend reinitialization may be needed to capture initial NGX events.' if self.already_running else None,
            'transaction_id': self.transaction['id'], 'module_inventory': self.modules,
            'attribution_confidence': 'temporal_log_and_process_identity' if self.verified else ('process_identity_only' if self.process and self.process['created_at'] else 'unconfirmed'),
            'limitation': 'Backend log attribution is temporal evidence, not cryptographic proof of GPU execution.',
            'polls': self.polls}
