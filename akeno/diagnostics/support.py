"""Bounded, explicit local support exports: text and JSON only, never binaries."""
import json
from pathlib import Path
import zipfile
from akeno.diagnostics.privacy import redact


def safe_report(report, include_excerpts=False, consent=False):
    if include_excerpts and not consent:
        raise PermissionError('Explicit consent is required for exporting redacted log excerpts.')
    def select(value):
        if isinstance(value, dict):
            return {k: ('community_experimental' if k in ('category','_trust_category','trust') and v == 'reviewed_community' else select(v)) for k,v in value.items()
                if include_excerpts or k not in ('raw_excerpts', 'line', 'backend_configuration','query_observations','gpu_diagnostics')}
        if isinstance(value, (list,tuple)):
            return [select(v) for v in value[:500]]
        if isinstance(value, str):
            return value[:64000]
        return value
    return redact(select(report))


def export_support_zip(report, destination, include_excerpts=False, consent=False):
    public = safe_report(report, include_excerpts, consent)
    payload = json.dumps(public, indent=2, default=str).encode('utf-8')
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError('The selected diagnostic report exceeds the 4-MiB support export limit.')
    with zipfile.ZipFile(Path(destination), 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('akeno-diagnostics.json', payload)
        archive.writestr('README.txt', 'Local diagnostic evidence only. No DLLs, assets or full backend logs included.\n'
            'Cached metadata checks are not fresh cryptographic verification. NR success requires attributable Feature 18 evaluation evidence.\n')
        if include_excerpts:
            lines = public.get('neural', {}).get('raw_excerpts', [])
            text = '\n'.join(f"{x.get('file','')}: {x.get('line','')}" for x in lines[-80:])
            archive.writestr('redacted-backend-excerpts.txt', text[:128000])
    return Path(destination)
