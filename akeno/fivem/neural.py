"""Explicit, read-only FiveM NR feasibility. No guessed loader or base-game writes."""
from pathlib import Path
import re
from akeno.i18n import msg
from akeno.core import processes
from akeno.community.runtime import regular_game_file
from akeno.diagnostics.neural import identity
from akeno.games import pe
from akeno.security.downloads import sha256_file

MODES=('offline_development','authorized_server','blocked_session')
CFX_SOURCE='https://github.com/citizenfx/fivem-docs/blob/c43ff1122035c1b417ea67f0fe6c9bdc5b761726/content/docs/client-manual/_index.md'


def inspect_client(installation, live=None, module_reader=None, birth_reader=None):
    """Explicit Analyze action only. Module APIs are candidates, not render activity.

    No process handles for writing, remote threads, injection or registry changes.
    A private D3D12 Feeder device can coexist with D3D11 game rendering.
    """
    live=processes.list_processes() if live is None else live
    module_reader=module_reader or processes.module_inventory
    birth_reader=birth_reader or processes.creation_identity
    root=installation.root.resolve();plugins=installation.plugin_dir.resolve()
    out={'schema':1,'read_only':True,'edition':installation.edition.value,
        'configured_build_preference':installation.saved_build,'client_fingerprint':installation.fingerprint,
        'plugin_directory':str(plugins),'documented_plugin_mechanism':'.asi plugins; exact ReShade/consumer loading still requires review',
        'documentation':CFX_SOURCE,'process':{},'client_version':None,
        'game_renderer':'unknown','game_render_activity':'not_measured','loaded_api_modules':[],
        'helper_api':'unknown','renderer_device_adapter':'unknown','modules':[],
        'observed_plugin_paths':[], 'loader_verified':False,'process_state':'not_observed',
        'server_policy_observation':'unknown; no server attestation obtained',
        'inventory':[{'path':str(f.path),'kind':f.kind,'sha256':f.sha256} for f in installation.inventory]}
    exact=[]
    for p in live:
        if p.path and re.fullmatch(r'FiveM(?:_b\d+)?_GTAProcess\.exe',p.name,re.I):
            path=Path(p.path)
            if path.resolve().is_relative_to(root):exact.append(p)
    if len(exact)!=1:
        out['process_state']='ambiguous' if exact else 'not_observed'
        return out
    selected=exact[0];path=Path(selected.path);regular_game_file(path)
    before=identity(path);pin=sha256_file(path);birth=birth_reader(selected.pid)
    if not birth:out['process_state']='birth_unavailable';return out
    modules=module_reader(selected.pid,birth)
    if before!=identity(path) or birth_reader(selected.pid)!=birth:
        out['process_state']='changed_during_inspection';return out
    out['process']={'name':selected.name,'path':str(path.resolve()),'pid':selected.pid,
        'created_at':birth,'sha256':pin,'file_identity':list(before)}
    out['client_version']=pe.parse(path).file_version
    out['process_state']=modules.get('state','unavailable')
    out['modules']=modules.get('modules',[])[:4096]
    for m in out['modules']:
        module=Path(m['path']);name=m.get('name',module.name).casefold()
        if name in ('d3d9.dll','d3d10.dll','d3d11.dll','d3d12.dll','vulkan-1.dll','opengl32.dll'):
            out['loaded_api_modules'].append(name)
        if module.resolve().is_relative_to(plugins):out['observed_plugin_paths'].append(str(module.resolve()))
    out['loaded_api_modules']=sorted(set(out['loaded_api_modules']))
    # API libraries alone do not identify the swapchain/device owner, loader contract,
    # server permission or which pixels reached Present. Keep these values Unknown.
    return out


def evidence_current(report, installation, birth_reader=None):
    """An update, plugin/config change, restart or PID reuse invalidates observations."""
    process=report.get('process',{});birth_reader=birth_reader or processes.creation_identity
    try:
        return bool(process and report.get('client_fingerprint')==installation.fingerprint
            and tuple(process['file_identity'])==identity(process['path'])
            and birth_reader(process['pid'])==process['created_at'])
    except (OSError,ValueError,KeyError):return False


def assess(installation, policy, inspection, *, mode='offline_development', review=None):
    if mode not in MODES:raise ValueError('Unknown FiveM workflow mode.')
    blockers=[]
    if installation.edition.value!='legacy':blockers.append(msg('Only a positively identified Legacy client is eligible; Enhanced has separate mandatory Pure Mode restrictions.'))
    blockers.extend(installation.errors)
    if inspection.get('process_state')!='observed':blockers.append(msg('Inspect the exact running FiveM GTAProcess through read-only module enumeration.'))
    if inspection.get('game_renderer')=='unknown':blockers.append(msg('The actual game renderer/device is unknown. Loaded D3D libraries do not establish render activity or a loader path.'))
    if mode=='blocked_session':blockers.append(msg('This session is unsupported or blocked. Restore managed files or use supported game graphics settings.'))
    if mode=='authorized_server':
        if policy.pure_level==2:blockers.append(msg('sv_pureLevel 2 blocks modified client files.'))
        elif policy.pure_level==1:blockers.append(msg('sv_pureLevel 1 requires recognized exact graphics modifications; no verified component allowlist is shipped.'))
        if not policy.server_id or not policy.permission_reference or not policy.graphics_plugins_allowed:
            blockers.append(msg('Explicit operator permission and effective server policy are missing.'))
        blockers.append(msg('Operator-entered settings are declarations, not independently verified client/server authorization.'))
    if not review:blockers.append(msg('No complete reviewed FiveM Legacy NR recipe is shipped: exact client loader, D3D11/D3D12 transport, motion/depth provider and licensed consumer remain unresolved.'))
    if not inspection.get('loader_verified'):blockers.append(msg('Cfx documents .asi plugins, but the exact selected ReShade/NR loading contract has not been verified. No DLL name or path will be guessed.'))
    return {'schema':1,'route':'fivem_legacy_neural','mode':mode,'deployment_eligible':False,
        'status':'blocked' if mode=='blocked_session' else 'missing_prerequisite','blockers':blockers,
        'installation_target':str(installation.plugin_dir),'base_game_writes':False,
        'server_policy_source':'operator_declaration','inspection':inspection,
        'required_components':['documented exact plugin loader','ReShade with add-on support',
            'reviewed API transport','Feeder','permitted motion-vector and depth providers',
            'exactly one licensed NR consumer','NVIDIA-authenticated SR runtime','reviewed NR runtime'],
        'next_action':'Export this read-only report. Resolve loader/renderer/transport contracts before deployment; begin with documented singleplayer development. A checkbox cannot enable a recipe.'}
