"""Deterministic route policy. Planning and installing call this same resolver."""
from .recipes import RECIPES
from .results import Requirement, RouteResult, RouteState
from .capabilities import version


def resolve(facts):
    results=[]
    for recipe in RECIPES:
        checks=[]; warnings=[]; state=RouteState.EXPERIMENTAL
        def need(code, ok, detail):checks.append(Requirement(code,bool(ok),detail))
        need('permission', facts.permitted, 'Eligible offline/single-player context with explicit permission.')
        need('closed', not facts.running, 'Close the game before deployment.')
        need('architecture',facts.architecture in recipe.architectures,'Required architecture: '+', '.join(recipe.architectures))
        need('api',facts.api in recipe.apis,'Supported API: '+', '.join(recipe.apis))
        if recipe.id=='official_swap':need('observed_component',facts.components.get('observed_official_component') is True,'Observe active component paths for this exact executable before swapping.')
        if recipe.native_calls:
            need('native_calls',facts.native_calls is True,facts.native_evidence)
        if recipe.id not in ('official_swap','configuration'):
            need('rtx',facts.gpu_vendor=='nvidia' and facts.gpu_generation in ('RTX 20','RTX 30','RTX 40','RTX 50'),'Detected NVIDIA RTX GPU; generation alone does not prove NR.')
            need('driver',version(facts.driver) is not None,'Detected exact driver version required.')
            need('runtime_pin',bool(facts.runtime_sha256),'Select an imported exact NR runtime hash.')
            profile=facts.runtime_profile
            if profile.get('schema')==3:
                backend='renodx_nr' if recipe.id=='native_renodx' else ('dlss5_feeder' if recipe.id=='legacy_bridge' else 'optiscaler_nr')
                need('runtime_profile',backend in profile.get('backends',[]) and facts.api in profile.get('apis',[]),'Runtime profile must cover this exact backend/API.')
                need('runtime_gpu',facts.gpu_generation not in profile.get('incompatible_generations',[]) and facts.gpu_device_id not in profile.get('incompatible_device_ids',[]) and (facts.gpu_generation not in ('RTX 20','RTX 30') or facts.gpu_generation in profile.get('validated_generations',[])),'Runtime profile must permit this GPU; lower RTX generations require applicable validated profiles.')
                if profile.get('minimum_driver'):
                    minimum=version(profile['minimum_driver']);actual=version(facts.driver)
                    need('runtime_driver',minimum and actual and actual>=minimum,'Runtime minimum driver '+profile['minimum_driver'])
            elif profile.get('targets'):
                backend='renodx_nr' if recipe.id=='native_renodx' else ('dlss5_feeder' if recipe.id=='legacy_bridge' else 'optiscaler_nr')
                targets=[t for t in profile['targets'] if (t.get('generation'),t.get('driver'),t.get('backend'),t.get('api'))==(facts.gpu_generation,facts.driver,backend,facts.api)]
                need('runtime_profile',targets and all(t.get('status')!='incompatible' for t in targets),'Legacy runtime review must match the exact GPU generation/driver/backend/API.')
            need('runtime_compatibility',not facts.components.get('runtime_known_incompatible'),'No applicable exact runtime/GPU incompatibility is recorded.')
            consumer_backend='dlss5_feeder' if recipe.id=='legacy_bridge' else 'optiscaler_nr'
            need('backend_compatibility',not facts.components.get('blocked_backends',{}).get(consumer_backend),'No blocking game/backend compatibility record applies.')
            need('exclusive_consumer',len(facts.consumers)<=1 and (not recipe.consumer or not facts.consumers or facts.consumers==[recipe.consumer]),'Exactly one compatible neural consumer per process.')
            need('conflicts',not facts.conflicts,'; '.join(facts.conflicts) or 'No conflicting consumer or unknown proxy detected.')
            for role in recipe.components:
                component=facts.components.get(role)
                need('component:'+role,component is not None,'Pinned compatible component: '+role)
                if component:
                    if component.get('minimum_driver'):
                        minimum=version(component['minimum_driver']); actual=version(facts.driver)
                        need('driver:'+role,minimum and actual and actual>=minimum,'Driver minimum '+component['minimum_driver'])
                    if component.get('apis'):need('api:'+role,facts.api in component['apis'],'Component API contract: '+role)
                    if component.get('architectures'):need('arch:'+role,facts.architecture in component['architectures'],'Component bitness: '+role)
            if recipe.id=='native_optiscaler':need('proxy',facts.proxy is not None,'A free supported proxy with an evidenced game load path is required.')
            if recipe.id=='feeder':
                need('reshade_loader',facts.components.get('reshade_load_route') is True,'ReShade requires an evidenced dxgi.dll load route.')
                need('feeder_api',facts.api=='d3d12','Integrated Feeder + OptiScaler contract currently requires D3D12; other transports need a reviewed recipe.')
                need('proxy',facts.proxy is not None,'An evidenced separate OptiScaler loader is required.')
                need('consumer',facts.components.get('selected_consumer') in ('optiscaler_nr','renodx_nr','chicken'),'Select one pinned, integrated NR consumer.')
                if facts.native_calls is True:warnings.append('Native DLSS inputs are available; a Feeder adds transport and conflict risk.')
            if recipe.id=='legacy_bridge':
                need('bridge_contract',False,'No reviewed complete legacy bridge/version contract is shipped yet. D3D9/OpenGL/Vulkan/x86 transport alone is not an install recipe.')
        if any(not c.satisfied for c in checks):state=RouteState.MISSING
        if facts.architecture and facts.architecture not in recipe.architectures or facts.api!='unknown' and facts.api not in recipe.apis:state=RouteState.INCOMPATIBLE
        if facts.components.get('runtime_known_incompatible') and recipe.id not in ('official_swap','configuration'):state=RouteState.INCOMPATIBLE
        if not facts.permitted:state=RouteState.UNSAFE
        if recipe.id in ('official_swap','configuration') and state==RouteState.EXPERIMENTAL:state=RouteState.COMPATIBLE
        results.append(RouteResult(recipe.id,recipe.name,state,checks,warnings,recipe.consumer,facts.proxy))
    preferred='native_optiscaler' if facts.native_calls else 'feeder'
    candidate=next((r for r in results if r.route_id==preferred and r.eligible),None)
    if candidate and not (candidate.route_id=='feeder' and facts.components.get('native_dlss_files_present')):candidate.recommended=True
    return results
