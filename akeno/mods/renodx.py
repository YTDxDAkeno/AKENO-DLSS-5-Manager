"""Exact local RenoDX NR consumer: native inputs or the pinned Feeder transport."""
from pathlib import Path
import json
from akeno.backends.base import InstallPlan,FileOp
from akeno.backends import ini,runtimes
from akeno.community.service import plan_digest
from akeno.community.runtime import regular,regular_game_file
from akeno.games import pe
from akeno.security.downloads import sha256_file,Cancelled
from akeno.fivem.community_stack import _consumer,preset_settings,CONTRACT
from .configuration import feeder_configuration


def prepare(s,game,recipe,sha,preset,offline,consent,token=None,progress=None,custom=None):
    from .service import PreparedMod
    if game.launcher=='fivem' or not offline or consent is not True:raise PermissionError('Use the dedicated FiveM route or confirm an eligible offline session.')
    if not s.system or not s.system.os_name.lower().startswith('windows') or not s.system.is_64bit:raise PermissionError('Deployment requires 64-bit Windows.')
    regular_game_file(Path(game.exe_path));exe=pe.parse(game.exe_path)
    if not exe.valid or exe.is_dll or exe.machine!='x64':raise PermissionError('Select the actual x64 game executable.')
    target=Path(game.target_dir)
    if not Path(game.exe_path).resolve().is_relative_to(Path(game.install_dir).resolve()):raise PermissionError('Executable escaped the game installation.')
    candidate=_consumer(s,sha);feeder_route=recipe['family']=='renodx_feeder'
    roles=('reshade','neural_consumer','motion_provider') if feeder_route else ('reshade','neural_consumer')
    components={role:s.pipelines.components.verified(role,game.id) for role in roles}
    if not all(components.values()) or components['neural_consumer'].get('contract')!=CONTRACT:raise PermissionError('The exact licensed consumer/dependency bytes are missing or changed.')
    managed=s.installer.managed_files(game.id,str(target));plan=InstallPlan('community:renodx_nr',None,str(target),params={
        '_mod_recipe':recipe['id'],'_mod_packages':recipe['packages'],'_mod_preset':preset,'_community_runtime':sha,
        '_consumer_contract':CONTRACT,'_mod_consumer_version':components['neural_consumer']['version'],
        '_mod_components':components,'_transport':'d3d11_private_d3d12' if feeder_route and game.api.value=='d3d11' else 'native_d3d12',
        '_execution':'not_tested'},summary=['Experimental '+recipe['name']+'. Exactly one NR consumer; Feature 18 execution remains untested.'])
    def add(destination,item):
        source=Path(item['stored_path']);regular(source);pin=item['sha256']
        path=target/destination
        if path.is_file() and destination.casefold() not in managed and sha256_file(path)==pin:return
        plan.ops.append(FileOp(destination,source=source,expected_sha256=pin))
    add('dxgi.dll',components['reshade']);add('renodx-dlss5.addon64',components['neural_consumer'])
    add(runtimes.NR_RUNTIME,{'stored_path':candidate.path,'sha256':sha})
    if feeder_route:
        package=s.catalog.package('dlss5-feeder-1.19.0');plan.package=package
        staged=s.stage(game,s.registry.get('dlss5_feeder'),package,progress,token)
        for rel in ('dlss5-feed.addon64','reshade-shaders/Shaders/DLSS5_Feed.fx'):add(rel,{'stored_path':str(staged/rel),'sha256':package.files[rel]})
        motion=components['motion_provider'];add(motion['destination'],motion)
        for item in motion.get('assets',[]):add(item['destination'],item)
        sr=s.mods.official_sr();verified=runtimes.evaluate(sr['stored_path'],runtimes.DLSS_RUNTIME,verifier=s.community.runtimes.verifier)
        if verified.trust.value!='verified':raise PermissionError('SR runtime signature changed.')
        add(runtimes.DLSS_RUNTIME,sr);plan.ops.extend(feeder_configuration(s,game,motion))
    else:
        old=(target/'ReShade.ini').read_text(encoding='utf-8-sig') if (target/'ReShade.ini').is_file() else ''
        plan.ops.append(FileOp('ReShade.ini',content=ini.set_values(old,{'ADDON':{'AddonPath':'.\\'}}).encode(),kind='config'))
    config=next(op for op in plan.ops if op.rel_path=='ReShade.ini')
    config.content=ini.set_values(config.content.decode(),{'RenoDX.DLSS5':preset_settings(preset,custom)}).encode()
    plan.ops.append(FileOp('akeno-install.json',content=json.dumps({'schema':2,'recipe':recipe['id'],'runtime':sha}).encode(),kind='marker'))
    for op in plan.ops:
        path=target/op.rel_path
        if not path.resolve().is_relative_to(target.resolve()) or path.is_symlink():raise PermissionError('Linked or escaped stack target.')
        if path.exists() and op.rel_path.casefold() not in managed:raise PermissionError('Unmanaged mod needs a coexistence review: '+op.rel_path)
    if token and token.cancelled:raise Cancelled('Consumer staging cancelled.')
    return PreparedMod(game.id,recipe['id'],tuple(recipe['packages']),sha,plan,plan_digest(plan),s.community._environment(game,plan),s.mods.selection_pin(game.id))


def install(s,prepared):
    game=s.library.reanalyze(prepared.game_id)
    row=next(r for r in s.mods.assessments(game.id,True) if r['id']==prepared.recipe_id)
    if not row['eligible']:raise PermissionError('RenoDX recipe eligibility changed.')
    _consumer(s,prepared.runtime_sha256)
    if s.community._environment(game,prepared.plan)!=prepared.environment_pin:raise PermissionError('Game files, renderer or hardware changed after preview.')
    return s.installer.apply(game,prepared.plan)
