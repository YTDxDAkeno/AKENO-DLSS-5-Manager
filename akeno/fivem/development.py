"""First-test evidence kits are separate from production recipes; no client files are modified."""
from dataclasses import asdict
import json
from pathlib import Path
from akeno.core.models import Severity
from akeno.fivem.compatibility import policy_findings
from akeno.diagnostics.privacy import redact


def validation_plan(installation, policy, gpu, backend, contract_source='', loading_allowed=False, protections_compatible=False):
    blockers = [f.message for f in policy_findings(policy, None, None) if f.severity == Severity.BLOCKER]
    if backend not in policy.allowed_backends:
        blockers.append('The selected backend is not covered by operator permission.')
    if installation.errors:
        blockers.extend(installation.errors)
    if installation.edition.value != 'legacy':
        blockers.append('The client is not positively identified as Legacy.')
    if not contract_source.startswith('https://'):
        blockers.append('A documented permitted client/plugin loading contract is required.')
    if loading_allowed is not True:
        blockers.append('Allowed loading of this exact plugin through the documented client interface is unresolved.')
    if protections_compatible is not True:
        blockers.append('Compatibility with active client protections is unresolved; protections must remain enabled.')
    if installation.game.anti_cheat.value != 'none_found':
        blockers.append('Client protection inspection needs resolution.')
    return {'schema': 1, 'purpose': 'isolated development validation; not production approval',
        'client_fingerprint': installation.fingerprint, 'renderer': installation.game.api.value,
        'gpu': gpu.to_dict() if gpu else None, 'backend': backend,
        'server_policy': {**asdict(policy), 'allowed_backends': sorted(policy.allowed_backends),
            'known_graphics_hashes': sorted(policy.known_graphics_hashes)},
        'contract_source': contract_source, 'first_test_eligible': not blockers,
        'blockers': blockers, 'production_approved': False, 'neural_execution_verified': False,
        'inventory': [{'name': f.path.name, 'kind': f.kind, 'sha256': f.sha256} for f in installation.inventory],
        'steps': ['Use a separate operator-controlled permitted Legacy test environment; preserve all active protections.',
            'Resolve the exact loader contract, dependencies and Pure Mode permission before any plugin loading.',
            'Use the documented permitted client interface only. This kit does not inject or deploy DLLs.',
            'Start a fresh Execution evidence observation before launching the isolated test client.',
            'Record exact client/artifact/GPU/driver pins, initialization, Feature 18 creation and evaluation, and errors.',
            'Export evidence and actual PresentMon measurements. Successful NR evaluation is a test outcome, not a prerequisite.',
            'Promote a separately reviewed production recipe only after applicable execution and protection evidence.']}


def write_kit(destination, report):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Choose a new isolated validation folder.')
    destination.mkdir(parents=True)
    (destination / 'validation-plan.json').write_text(json.dumps(redact(report), indent=2), encoding='utf-8')
    (destination / 'README.txt').write_text('\n'.join(report['steps']) + '\n\nBlockers:\n' + '\n'.join(report['blockers']), encoding='utf-8')
    return destination
