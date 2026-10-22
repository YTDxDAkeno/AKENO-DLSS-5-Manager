"""Official component families, content-addressed imports and exact observed-target swaps."""
from dataclasses import dataclass
from pathlib import Path
import json
import shutil
from akeno.games import pe
from akeno.security import authenticode
from akeno.security.downloads import sha256_file
from akeno.paths import cache_dir
from akeno.community.runtime import regular,regular_game_file
from akeno.backends.base import FileOp,InstallPlan
from akeno.community.service import plan_digest
from akeno.core import processes
from akeno.pipeline.discovery import dll_inventory
from akeno.core.models import AntiCheatRisk

FAMILIES={'nvngx_dlss.dll':'super_resolution','nvngx_dlssd.dll':'ray_reconstruction','nvngx_dlssg.dll':'frame_generation','nvngx_dlssnr.dll':'neural_rendering'}


def identify(info):
    if not info.valid or not info.is_dll or info.machine!='x64':raise ValueError('Official components must be x64 PE DLLs.')
    names={str(info.strings.get(k,'')).casefold() for k in ('OriginalFilename','InternalName')}
    if info.export_dll_name:names.add(info.export_dll_name.casefold())
    recognized={n for n in names if n in FAMILIES or n.startswith('sl.') and n.endswith('.dll')}
    if len(recognized)!=1:raise ValueError('An exact unambiguous DLSS/Streamline family identity is required.')
    name=next(iter(recognized))
    if not info.file_version:raise ValueError('A component version resource is required.')
    if name in FAMILIES and not any('NVSDK_NGX' in e for e in info.exports):raise ValueError('NGX exports are missing.')
    return name,FAMILIES.get(name,'streamline')


@dataclass(frozen=True)
class PreparedSwap:
    game_id:str
    plan:object
    pin:str
    exe_sha256:str


class DllService:
    def __init__(self,services,verifier=None):self.s=services;self.verifier=verifier or authenticode.verify

    def import_official(self,path,permitted=False):
        if permitted is not True:raise PermissionError('Explicit permission for local component reuse is required.')
        path=Path(path);regular(path);info=pe.parse(path);name,family=identify(info)
        signature=self.verifier(str(path))
        if not signature.is_nvidia:raise PermissionError('Official component requires a valid NVIDIA Authenticode signature. Community NR uses its separate import workflow.')
        pin=sha256_file(path);dest=cache_dir()/'official-dlls'/pin/name;dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():shutil.copyfile(path,dest)
        if sha256_file(dest)!=pin:raise ValueError('Changed content-addressed official cache.')
        record={'name':name,'family':family,'version':info.file_version,'sha256':pin,'stored_path':str(dest),'signer':signature.signer,'trust':'nvidia_authenticated'}
        inventory=self.s.db.get_setting('official_dll_inventory',{});inventory[pin]=record;self.s.db.set_setting('official_dll_inventory',inventory)
        return record

    def observe_active_paths(self,game_id):
        game=self.s.library.get(game_id)
        exact=[p for p in processes.find_by_exe(game.exe_path) if p.path and Path(p.path).resolve()==Path(game.exe_path).resolve()]
        if len(exact)!=1:raise ValueError('Start exactly the selected game process to observe active DLL paths.')
        proc=exact[0];birth=processes.creation_identity(proc.pid);modules=processes.module_inventory(proc.pid,birth)
        if modules['state']!='observed':raise PermissionError(modules['detail'])
        root=Path(game.install_dir).resolve();paths={}
        for module in modules['modules']:
            path=Path(module['path']);name=path.name.casefold()
            if (name in FAMILIES or name.startswith('sl.') and name.endswith('.dll')) and path.resolve().is_relative_to(root):
                regular(path);paths[str(path.resolve())]=sha256_file(path)
        evidence={'exe_sha256':sha256_file(game.exe_path),'exe_path':str(Path(game.exe_path).resolve()),'pid':proc.pid,'created_at':birth,'paths':paths}
        self.s.db.set_setting('dll_load_evidence:'+game_id,evidence);return evidence

    def prepare(self,game_id,replacements,offline=False):
        game=self.s.library.reanalyze(game_id)
        if not game or not offline or game.launcher=='fivem' or game.anti_cheat!=AntiCheatRisk.NONE_FOUND or self.s.compat_db.anti_cheat_entry(game):raise PermissionError('An eligible offline game and explicit offline confirmation are required.')
        if self.s.installer.running_check(game.install_dir):raise PermissionError('Close the game after observing loaded DLLs, before preparing a swap.')
        active=self.s.installer.active_install(game.id)
        if active and active['backend_id'].startswith('community:'):raise PermissionError('Restore or use the existing experimental workflow before changing its pinned DLLs.')
        evidence=self.s.db.get_setting('dll_load_evidence:'+game.id,{})
        exe_pin=sha256_file(game.exe_path)
        if evidence.get('exe_sha256')!=exe_pin or evidence.get('exe_path')!=str(Path(game.exe_path).resolve()):raise PermissionError('Observe loaded modules for this exact executable first.')
        inventory=self.s.db.get_setting('official_dll_inventory',{})
        root=Path(game.install_dir).resolve();plan=InstallPlan('official_dlss',None,str(root),kind='swap')
        if not replacements:raise ValueError('Select at least one observed component path.')
        for original,pin in replacements.items():
            path=Path(original);regular(path)
            if not path.resolve().is_relative_to(root) or evidence.get('paths',{}).get(str(path.resolve()))!=sha256_file(path):raise PermissionError('Active component path is unobserved or changed.')
            item=inventory.get(pin)
            if not item or path.name.casefold()!=item['name']:raise ValueError('Replacement is a different DLL family.')
            src=Path(item['stored_path']);regular(src)
            if sha256_file(src)!=pin or not self.verifier(str(src)).is_nvidia:raise PermissionError('Official source failed hash/signature revalidation.')
            old=pe.parse(path)
            if item['name']=='nvngx_dlss.dll' and old.file_version and (old.file_version.startswith('1.')!=item['version'].startswith('1.')):raise ValueError('DLSS1 and later SR are not interchangeable.')
            rel=path.resolve().relative_to(root).as_posix()
            plan.ops.append(FileOp(rel,source=src,expected_sha256=pin,description='Official '+item['family']+' '+item['version']))
            plan.expected_originals[rel]=sha256_file(path)
        selected=[o for o in plan.ops if Path(o.rel_path).name.casefold().startswith('sl.')]
        for op in selected:
            folder=(root/op.rel_path).parent
            existing={p.name.casefold() for p in folder.glob('sl.*.dll')}
            chosen={Path(o.rel_path).name.casefold() for o in selected if (root/o.rel_path).parent==folder}
            versions={pe.parse(o.source).file_version for o in selected if (root/o.rel_path).parent==folder}
            if existing!=chosen or len(versions)!=1:raise ValueError('Streamline must be swapped as a complete matching version set, never one arbitrary component.')
        plan.params={'_official_swap':True,'_exe_sha256':exe_pin,'_offline_attested':True}
        plan.summary=['Replace only observed active DLL paths, with exact original-hash preconditions and verified backups.','No new DLSS/NR/MFG integration is added.']
        return PreparedSwap(game.id,plan,plan_digest(plan),exe_pin)

    def install(self,prepared):
        game=self.s.library.reanalyze(prepared.game_id)
        if plan_digest(prepared.plan)!=prepared.pin or sha256_file(game.exe_path)!=prepared.exe_sha256:raise PermissionError('Prepared swap or executable changed.')
        fresh=self.prepare(game.id,{str(Path(prepared.plan.target_dir)/o.rel_path):o.expected_sha256 for o in prepared.plan.ops},True)
        if fresh.pin!=prepared.pin:raise PermissionError('Swap conditions changed after preview.')
        return self.s.installer.apply(game,prepared.plan)

    def bulk_install(self,prepared_swaps):
        # Each game is a separate recoverable transaction; failures are explicit, never a global success.
        return [(p.game_id,self.install(p)) for p in prepared_swaps]
