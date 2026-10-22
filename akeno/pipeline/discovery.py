"""Cheap cached game facts and bounded inventories; no normal-refresh disk walks."""
from pathlib import Path
from .capabilities import PipelineFacts
from .conflicts import inspect_conflicts
from .proxy import resolve_proxy
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
    permitted=bool(offline and game.launcher!='fivem' and game.anti_cheat==AntiCheatRisk.NONE_FOUND
        and not services.compat_db.anti_cheat_entry(game) and 'gta online' not in game.name.casefold())
    return PipelineFacts(game.id,game.api.value,game.architecture,native,why,
        gpu.vendor.value if gpu else 'unknown',gpu.device_id if gpu else None,gpu.generation if gpu else None,
        gpu.driver_version if gpu else None,(runtime or {}).get('sha256'),(runtime or {}).get('version'),
        (runtime or {}).get('category'),components or {},consumers,resolve_proxy(game,managed) if game.target_dir else None,
        conflicts,permitted,bool(game.is_running))


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
