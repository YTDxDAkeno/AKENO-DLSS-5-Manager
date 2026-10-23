"""Verify the complete staged operation set without loading any executable code."""
from pathlib import Path
from akeno.community.runtime import regular
from akeno.games import pe
from akeno.security.downloads import sha256_file


def validate_sources(plan,architecture='x64'):
    names=set()
    for op in plan.ops:
        name=op.rel_path.casefold()
        if name in names:raise ValueError('Duplicate stack destination: '+op.rel_path)
        names.add(name)
        if Path(name).suffix in ('.ps1','.bat','.cmd','.py'):raise PermissionError('Downloaded installer scripts are not part of a managed payload.')
        if not op.source:continue
        regular(op.source)
        if not op.expected_sha256 or sha256_file(op.source)!=op.expected_sha256:raise PermissionError('Pinned stack source changed: '+op.rel_path)
        suffix=Path(name).suffix
        if suffix in ('.dll','.asi','.addon64','.addon32','.exe'):
            expected='x86' if suffix=='.addon32' else architecture
            info=pe.parse(op.source,with_version=False)
            if not info.valid or info.machine!=expected or info.is_dll!=(suffix!='.exe'):raise PermissionError('Stack PE architecture/type mismatch: '+op.rel_path)
