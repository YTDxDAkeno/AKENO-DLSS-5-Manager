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
