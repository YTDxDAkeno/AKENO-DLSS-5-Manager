"""Cheap cached game facts and bounded inventories; no normal-refresh disk walks."""
from pathlib import Path
from .capabilities import PipelineFacts
from .conflicts import inspect_conflicts
from .proxy import resolve_proxy,load_candidates
from akeno.core.models import AntiCheatRisk
from akeno.games import pe


def native_support(game, managed=()):
    if game.launcher=='steam' and str(game.launcher_id)=='1285190' and game.exe_path:
        try:rel=Path(game.exe_path).relative_to(Path(game.install_dir)).as_posix().casefold()
        except ValueError:rel=''
        if rel=='oakgame/binaries/win64/borderlands4.exe':
            return True,'Borderlands 4 Steam1285190 exact executable profile establishes engine DLSS support, not current calls or NR execution.'
    if not game.exe_path:return None,'Select the actual game executable.'
    imports=set(pe.parse(game.exe_path,with_version=False).imports)
    if 'nvngx_dlss.dll' in imports and 'nvngx_dlss.dll' not in {x.casefold() for x in managed}:
        return True,'Game PE imports native nvngx_dlss.dll; current invocation still requires live evidence.'
    return None,'DLSS files present are inventory only; native engine integration is unresolved.'


def facts_for(services,game,offline=False,runtime=None,components=None):
    gpu=services.selected_gpu(); managed=services.installer.managed_files(game.id,game.target_dir) if game.target_dir else set()
    native,why=native_support(game,managed)
    consumers,conflicts=inspect_conflicts(game,managed)
    components=dict(components or {})
    imports=load_candidates(game)
    components['reshade_load_route']='dxgi.dll' in imports
    components['blocked_backends']={backend:any(row['status'] in ('blocked','incompatible') for row in services.compat_db.entries_for(game,backend)) for backend in ('optiscaler_nr','dlss5_feeder')}
    components['native_dlss_files_present']=any(m.kind=='dlss' and m.name.casefold() not in managed for m in game.existing_mods)
    evidence=services.db.get_setting('dll_load_evidence:'+game.id,{})
    from akeno.diagnostics.neural import identity
    try:
        components['observed_official_component']=bool(game.exe_path and evidence.get('paths') and evidence.get('exe_path')==str(Path(game.exe_path).resolve())
            and tuple(evidence.get('exe_metadata',()))==identity(game.exe_path)
            and any(tuple(evidence.get('path_metadata',{}).get(name,()))==identity(name) for name in evidence['paths']))
    except (OSError,ValueError):components['observed_official_component']=False
    from .proxy import observed_loader_paths
    observed=observed_loader_paths(services,game)
    permitted=bool(offline and game.launcher!='fivem' and game.anti_cheat==AntiCheatRisk.NONE_FOUND
        and not services.compat_db.anti_cheat_entry(game) and 'gta online' not in game.name.casefold())
    facts=PipelineFacts(game.id,game.api.value,game.architecture,native,why,
        gpu.vendor.value if gpu else 'unknown',gpu.device_id if gpu else None,gpu.generation if gpu else None,
        gpu.driver_version if gpu else None,(runtime or {}).get('sha256'),(runtime or {}).get('version'),
        (runtime or {}).get('category'),components,consumers,resolve_proxy(game,managed,observed=observed) if game.target_dir else None,
        conflicts,permitted,bool(game.is_running))
    facts.runtime_profile=(runtime or {}).get('review') or {}
    if runtime and runtime.get('sha256'):
        from akeno.hardware.support import runtime_verdict
        verdict=runtime_verdict(runtime.get('version'),facts.gpu_generation,services.compat_db.nr_runtimes(),build_sha256=runtime['sha256'])
        facts.components['runtime_known_incompatible']=verdict.state=='fails'
    return facts


def dll_inventory(game):
    families={'nvngx_dlss.dll':'super_resolution','nvngx_dlssd.dll':'ray_reconstruction','nvngx_dlssg.dll':'frame_generation','nvngx_dlssnr.dll':'neural_rendering'}
    result=[]
    for mod in game.existing_mods:
        name=mod.name.casefold()
        family=families.get(name) or ('streamline' if name.startswith('sl.') and name.endswith('.dll') else None)
        if family:
            result.append({'family':family,'path':mod.path,'version':mod.version,'active_path':'unresolved',
                'detail':'Presence does not identify the DLL used by the game loader.'})
    return result
