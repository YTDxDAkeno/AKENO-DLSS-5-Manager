"""Select only a backend-supported, evidenced loader path, without clobbering mods."""
from pathlib import Path
from akeno.games import pe

SUPPORTED = ('winmm.dll','version.dll','dbghelp.dll','wininet.dll','winhttp.dll','dxgi.dll')


def load_candidates(game):
    if not game.exe_path: return set()
    # Direct and bounded game-local import chain. No system library / drive walks.
    root = Path(game.install_dir).resolve()
    paths, seen, imports = [Path(game.exe_path)], set(), set()
    for _ in range(3):
        following=[]
        for path in paths[:128]:
            try:
                real=path.resolve()
                if real in seen or not real.is_relative_to(root) or path.is_symlink():continue
                seen.add(real); info=pe.parse(path, with_version=False)
                for name in info.imports:
                    imports.add(name.casefold())
                    local=path.parent/name
                    if local.is_file():following.append(local)
            except OSError:continue
        paths=following
    return imports


def resolve_proxy(game, managed=(), supported=SUPPORTED, observed=()):
    imports=load_candidates(game)
    # Caller-provided module observations must be from the selected process lifetime.
    proven=imports|{Path(p).name.casefold() for p in observed}
    try:existing={p.name.casefold():p for p in Path(game.target_dir or game.install_dir).iterdir()}
    except OSError:return None
    owned={n.casefold() for n in managed}
    for name in supported:
        if name.casefold() not in proven:continue
        if name.casefold() not in existing or name.casefold() in owned:return name
    # An ASI loader is an evidenced loader only when its own module is actually imported/observed.
    if any(m.kind=='asi_loader' and m.name.casefold() in proven for m in game.existing_mods):
        if 'optiscaler.asi' not in existing or 'optiscaler.asi' in owned:return 'OptiScaler.asi'
    return None


def observed_loader_paths(services,game):
    """Reuse path observations only while the selected EXE and observed owned module stay unchanged."""
    from akeno.diagnostics.neural import identity
    session=getattr(services,'community',None)
    session=session.sessions.get(game.id) if session else None
    if not session or not game.exe_path or not game.target_dir:return []
    executable=str(Path(game.exe_path).resolve())
    if session.observation.executable!=executable:return []
    pins=dict(session.observation.file_identities)
    try:
        if identity(executable)!=pins.get(executable):return []
        root=Path(game.target_dir).resolve();found=[]
        for module in session.modules.get('modules',[]):
            path=Path(module['path']).resolve()
            if path.parent==root and identity(path)==pins.get(str(path)):found.append(str(path))
        return found
    except (OSError,ValueError):return []
