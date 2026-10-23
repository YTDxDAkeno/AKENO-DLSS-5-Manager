"""Experimental FiveM Legacy stack using the client's existing graphics-plugin loader.

Cfx source: 4c4fe28, ReShadeFixups.cpp and RenderHooks.cpp. Nothing here patches
client checks, sets a ReShade exception, edits GTA, or treats SR as Feature 18.
"""
from dataclasses import replace
from pathlib import Path
from akeno.i18n import msg
import hashlib
import json
import os
import subprocess
from akeno.backends.base import InstallPlan,FileOp
from akeno.backends import ini,runtimes
from akeno.community.runtime import regular,Category
from akeno.community.service import plan_digest
from akeno.core.models import GraphicsApi,AntiCheatRisk
from akeno.games import pe
from akeno.security.downloads import sha256_file,Cancelled
from .discovery import inspect_installation
from .models import Edition
from .paths import guard_target
from .server_config import inspect_config

CONTRACT='renodx-dlss5-config7'
BACKEND='fivem_community:renodx_nr'


def _client_exception(installation):
    # Match Cfx HashString exactly; only inspect an existing native client choice.
    value=0
    for char in os.environ.get('COMPUTERNAME','a').lower().encode('ascii',errors='strict'):
        value=(value+char)&0xffffffff;value=(value+(value<<10))&0xffffffff;value^=value>>6
    value=(value+(value<<3))&0xffffffff;value^=value>>11;value=(value+(value<<15))&0xffffffff
    expected=f'ID:{value:08x} acknowledged that ReShade 5.x has a bug that will lead to game crashes'
    path=installation.app_dir/'CitizenFX.ini'
    if not path.is_file():return False
    regular(path)
    return ini.get_values(path.read_text(encoding='utf-8-sig')).get('Addons',{}).get('ReShade5')==expected


def _policy(s,game):
    policy=s.fivem.policy(game.id);saved=s.db.get_setting('fivem_community:'+game.id,{})
    report=inspect_config(saved['server_config']) if saved.get('server_config') else None
    if report:
        policy=replace(policy,pure_level=report['pure_level'],script_hook_allowed=report['script_hook_allowed'])
    return policy,saved,report


def assess(s,game):
    conditions=[]
    def need(kind,code,ok,message,remedy):
        if not ok:conditions.append(dict(kind=kind,code=code,message=message,remedy=remedy))
    installation=inspect_installation(game.install_dir)
    need('technical_incompatibility','fivem_identity',bool(installation and installation.edition==Edition.LEGACY and not installation.errors),'Identify a complete FiveM Legacy client.','Rescan the installed client; Enhanced uses a different contract.')
    if not installation:return {'conditions':conditions}
    try:guard_target(installation.plugin_dir,installation.app_dir)
    except (ValueError,PermissionError) as error:need('file_conflict','fivem_target',False,str(error),'Restore an ordinary client plugin directory.')
    need('technical_incompatibility','fivem_renderer',game.api==GraphicsApi.D3D11,'This FiveM route uses the Legacy D3D11 game renderer.','Inspect the actual GTAProcess. Feeder uses a separate private D3D12 device.')
    need('technical_incompatibility','windows',bool(s.system and s.system.os_name.lower().startswith('windows') and s.system.is_64bit),'Deployment needs 64-bit Windows.','Use the Windows release.')
    try:s.fivem._stopped()
    except PermissionError as error:need('file_conflict','fivem_running',False,str(error),'Close FiveM and every GTAProcess.')
    policy,saved,report=_policy(s,game)
    need('platform_prohibited','fivem_permission',bool(policy.server_id and policy.permission_reference and policy.graphics_plugins_allowed is True and policy.experimental_consent),'Supply permission for this exact graphics stack and test server.','Save the operator policy in FiveM settings.')
    need('platform_prohibited','fivem_client_permission',saved.get('client_plugins_permitted') is True,'Client graphics-plugin compatibility has not been confirmed.','Inspect the client restrictions and confirm the permitted graphics mechanism.')
    need('platform_prohibited','fivem_pure',policy.pure_level in (0,1),'Pure Mode is unknown or prohibits these modifications.','Inspect the effective server configuration; AKENO never changes Pure Mode.')
    if report:need('missing_input','server_config',report['complete'], 'Server configuration includes could not be resolved.','Supply the complete effective configuration tree.')
    records=s.pipelines.components.records(game.id);bundled=s.mods.bundled_components()
    for role in ('reshade','motion_provider','neural_consumer'):
        item=records.get(role) or bundled.get(role)
        need('resolvable_dependency' if item else 'missing_component',role,bool(item),msg('A compatible {role} is required.',role=role),'Resolve bundled components or import the licensed consumer once.')
    consumer=records.get('neural_consumer')
    if consumer:need('technical_incompatibility','consumer_contract',consumer.get('contract')==CONTRACT,'The consumer must match the exact ConfigVersion 7 NR contract.','Import the matching permitted RenoDX NR add-on; generic HDR add-ons are different.')
    reshade=records.get('reshade') or bundled.get('reshade')
    if reshade:
        from akeno.pipeline.capabilities import version
        v=version(reshade.get('version')) or ()
        need('technical_incompatibility','reshade_version',v>=(6,8,0),'Feeder 1.19 requires ReShade 6.8 or newer with full add-on support.','Resolve the pinned full add-on build.')
        # Cfx has a native crash-risk acknowledgement for v5+. Never write it or disguise this DLL.
        need('platform_prohibited','client_reshade',_client_exception(installation),'The client currently rejects ReShade 5 and newer without its own crash-risk acknowledgement.','Review the FiveM client console and its supported configuration. AKENO does not alter that check.')
    pins={r.get('sha256') for r in records.values() if r.get('role')!='motion_provider'}
    if policy.pure_level==1:
        # An operator assertion cannot add a binary to the client's known-mod list.
        need('platform_prohibited','pure_known',False,'Pure level 1 requires client-recognized graphics mods; this full add-on stack has no reviewed allowlist contract.','Use a client/server configuration that permits this exact stack without bypasses.')
    need('missing_component','official_sr',bool(s.mods.official_sr()),'Feeder requires an authenticated NVIDIA SR runtime.','Find a permitted local SR runtime automatically.')
    target=installation.plugin_dir
    managed=s.installer.managed_files(game.id,str(target))
    if target.is_dir():
        expected={'d3d11.dll','dlss5-feed.addon64','renodx-dlss5.addon64','nvngx_dlssnr.dll','nvngx_dlss.dll','ReShade.ini','AKENO-Feeder.ini','dlss5-feed.cfg','akeno-install.json'}
        for path in target.iterdir():
            if path.is_file() and path.name.casefold() not in managed and (path.name in expected or path.suffix.casefold() in ('.dll','.asi','.addon64')):
                # Existing exact ReShade is preserved after byte identity and native metadata checks.
                match=path.name=='d3d11.dll' and reshade and sha256_file(path)==reshade['sha256']
                need('file_conflict','fivem_existing',match,msg('An unmanaged FiveM component needs a reversible coexistence review: {name}',name=path.name),'Keep its original backup; do not overwrite another mod.')
    return {'conditions':conditions,'installation':installation,'policy':policy,'settings':saved,'server_config':report,
        'renderer':{'game':'d3d11','feeder_transport':'private_d3d12','source':'Cfx Legacy renderer source; actual live renderer remains separately observable'}}


def _binding(s,game,details,plan):
    return hashlib.sha256(json.dumps({'client':details['installation'].fingerprint,'policy':repr(details['policy']),
        'settings':details['settings'],'server':details['server_config'],'components':s.pipelines.components.records(game.id),
        'environment':s.community._environment(game,plan)},sort_keys=True,default=str).encode()).hexdigest()


def _consumer(s,sha):
    candidate=s.community._candidate(sha);gpu=s.selected_gpu()
    import re
    if not gpu or gpu.vendor.value!='nvidia' or gpu.has_tensor_cores is not True or gpu.is_software or not re.fullmatch(r'\d{3}\.\d{2}',gpu.driver_version or ''):raise PermissionError('A detected NVIDIA RTX GPU and driver are required.')
    if candidate.category not in (Category.COMMUNITY,Category.ADVANCED,Category.OFFICIAL):raise PermissionError(candidate.detail)
    from akeno.community.profiles import eligibility
    if candidate.review and candidate.review.get('schema')==3:eligibility(candidate.review,gpu,'renodx_nr','d3d12')
    elif candidate.category!=Category.OFFICIAL:raise PermissionError('Use a runtime profile that explicitly covers the RenoDX NR consumer and its D3D12 transport.')
    from akeno.hardware.support import runtime_verdict
    verdict=runtime_verdict(candidate.version,gpu.generation,s.compat_db.nr_runtimes(),build_sha256=sha)
    if verdict.state=='fails':raise PermissionError(verdict.text)
    return candidate


def preset_settings(preset,custom=None):
    if preset=='custom':
        values=dict(custom or {});limits={'NRResolutionScale':(.25,1.),'NRPasses':(1,4),'NRStyle':(0,2),'NeuralUplift':(0,1)}
        if set(values)-set(limits):raise ValueError('Unsupported consumer setting.')
        for key,value in values.items():
            if not isinstance(value,(int,float)) or not limits[key][0]<=value<=limits[key][1] or key!='NRResolutionScale' and int(value)!=value:raise ValueError('Consumer setting outside supported range: '+key)
        return dict(preset_settings('quality'),**{key:str(value) for key,value in values.items()})
    if preset not in ('performance','balanced','quality','ultra','disabled'):raise ValueError('Unsupported ConfigVersion 7 preset.')
    scale,passes={'performance':(.5,1),'balanced':(.67,1),'quality':(1.,1),'ultra':(1.,2),'disabled':(1.,1)}[preset]
    return {'ConfigVersion':'7','EnableHooks':'2','NeuralUplift':'0' if preset=='disabled' else '1',
        'NREnableUpscaling':'0','NRResolutionScale':str(scale),'NRPasses':str(passes),'NRStyle':'0'}


def prepare(s,game,recipe,sha,preset,token=None,progress=None,custom=None):
    from akeno.mods.service import PreparedMod
    details=assess(s,game);hard=[c for c in details['conditions'] if c['kind']!='resolvable_dependency']
    if hard:raise PermissionError('\n'.join(c['message'] for c in hard))
    candidate=_consumer(s,sha);installation=details['installation'];target=installation.plugin_dir
    feeder=s.catalog.package('dlss5-feeder-1.19.0');adapter=s.registry.get('dlss5_feeder')
    staging=s.stage(game,adapter,feeder,progress,token)
    components={role:s.pipelines.components.verified(role,game.id) for role in ('reshade','motion_provider','neural_consumer')}
    if not all(components.values()):raise PermissionError('Imported component bytes changed; import them again.')
    reshade=pe.parse(components['reshade']['stored_path'])
    if reshade.strings.get('ProductName')!='ReShade' or not {'ReShadeRegisterAddon','ReShadeUnregisterAddon'}<=set(reshade.exports):raise PermissionError('The native loader requires genuine full add-on ReShade identity.')
    plan=InstallPlan(BACKEND,feeder,str(target),params={'_mod_recipe':recipe['id'],'_mod_packages':recipe['packages'],'_mod_preset':preset,
        '_community_runtime':sha,'_consumer_contract':CONTRACT,'_mod_consumer_version':components['neural_consumer']['version'],'_mod_components':components,'_transport':'d3d11_private_d3d12','_execution':'not_tested'},
        summary=['Experimental FiveM Legacy: D3D11 game → ReShade → depth / estimated motion → Feeder private D3D12 → local NR consumer.',
        'Only FiveM.app/plugins is changed. Installation success does not prove Feature 18 execution.'])
    def add(dest,item):
        source=Path(item.get('stored_path') or item['path']);pin=item['sha256']
        existing=target/dest
        if existing.is_file() and dest.casefold() not in s.installer.managed_files(game.id,str(target)) and sha256_file(existing)==pin:return
        plan.ops.append(FileOp(dest,source=source,expected_sha256=pin))
    add('d3d11.dll',components['reshade']);add('renodx-dlss5.addon64',components['neural_consumer'])
    motion=components['motion_provider'];add(motion['destination'],motion)
    for item in motion.get('assets',[]):add(item['destination'],item)
    for rel in ('dlss5-feed.addon64','reshade-shaders/Shaders/DLSS5_Feed.fx'):add(rel,{'stored_path':str(staging/rel),'sha256':feeder.files[rel]})
    sr=s.mods.official_sr();verified=runtimes.evaluate(sr['stored_path'],runtimes.DLSS_RUNTIME,verifier=s.community.runtimes.verifier)
    if verified.trust.value!='verified':raise PermissionError('SR runtime signature changed.')
    add(runtimes.DLSS_RUNTIME,sr);add(runtimes.NR_RUNTIME,{'stored_path':candidate.path,'sha256':sha})
    from akeno.mods.configuration import feeder_configuration
    plan.ops.extend(feeder_configuration(s,game,motion,target))
    config=next(op for op in plan.ops if op.rel_path=='ReShade.ini')
    config.content=ini.set_values(config.content.decode(),{'RenoDX.DLSS5':preset_settings(preset,custom)}).encode()
    plan.ops.append(FileOp('akeno-install.json',content=json.dumps({'schema':2,'recipe':recipe['id'],'runtime':sha,'consumer':components['neural_consumer']['sha256']}).encode(),kind='marker'))
    guard_target(target,installation.app_dir,[op.rel_path for op in plan.ops])
    if token and token.cancelled:raise Cancelled('FiveM staging cancelled.')
    return PreparedMod(game.id,recipe['id'],tuple(recipe['packages']),sha,plan,plan_digest(plan),_binding(s,game,details,plan),s.mods.selection_pin(game.id),fiveM={'root':str(installation.root)})


def install(s,prepared):
    game=s.library.reanalyze(prepared.game_id);details=assess(s,game)
    if details['conditions']:raise PermissionError('FiveM eligibility changed; prepare again.')
    _consumer(s,prepared.runtime_sha256)
    if _binding(s,game,details,prepared.plan)!=prepared.environment_pin:raise PermissionError('Client, policy, components or files changed after preview.')
    target=details['installation'].plugin_dir;guard_target(target,details['installation'].app_dir,[o.rel_path for o in prepared.plan.ops])
    if Path(prepared.plan.target_dir)!=target:raise PermissionError('FiveM plan target changed.')
    for op in prepared.plan.ops:
        if op.source:
            regular(op.source)
            if sha256_file(op.source)!=op.expected_sha256:raise PermissionError('A selected FiveM component changed after preview.')
    target.mkdir(exist_ok=True)
    return s.installer.apply(game,prepared.plan)


def configure(s,game,active,preset,custom=None):
    target=Path(active['target_dir'])
    if game.launcher=='fivem':
        s.fivem._stopped();installation=inspect_installation(game.install_dir)
        if not installation:raise PermissionError('Inspect the FiveM client before configuration.')
        guard_target(installation.plugin_dir,installation.app_dir,['ReShade.ini'])
        if target!=installation.plugin_dir:raise PermissionError('FiveM target changed.')
    elif target!=Path(game.target_dir):raise PermissionError('Game target changed.')
    path=target/'ReShade.ini';regular(path)
    text=ini.set_values(path.read_text(encoding='utf-8-sig'),{'RenoDX.DLSS5':preset_settings(preset,custom)})
    plan=InstallPlan(active['backend_id'],None,str(target),[FileOp('ReShade.ini',content=text.encode(),kind='config')],params=dict(active['params'],_mod_preset=preset),kind='configure')
    return s.installer.apply(game,plan)


def restore(s,game,tx):
    s.fivem._stopped();installation=inspect_installation(game.install_dir)
    if not installation or Path(tx['target_dir'])!=installation.plugin_dir:raise PermissionError('Inspect the original FiveM plugin target before restoration.')
    guard_target(installation.plugin_dir,installation.app_dir,[f['rel_path'] for f in s.db.tx_files(tx['id'])])
    return s.installer.restore(tx['id'],game_running_dir=str(installation.root))


def launch(s,game,consent):
    if consent is not True:raise PermissionError('Confirm the experimental FiveM session.')
    details=assess(s,game)
    if details['conditions']:raise PermissionError('\n'.join(c['message'] for c in details['conditions']))
    active=s.installer.active_install(game.id)
    if not active or active['backend_id']!=BACKEND:raise PermissionError('Install the complete FiveM community stack first.')
    if s.installer.check_restore(active['id']):raise PermissionError('Managed stack bytes changed.')
    _consumer(s,active['params']['_community_runtime'])
    return subprocess.Popen([str(details['installation'].launcher)],cwd=str(details['installation'].root))
