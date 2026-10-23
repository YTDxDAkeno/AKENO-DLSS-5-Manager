"""Complete community mod operations on the existing download and transaction engines."""
from __future__ import annotations
from dataclasses import dataclass,replace
from pathlib import Path
from akeno.i18n import msg
import hashlib
import json
import threading
from akeno.core.models import AntiCheatRisk,GraphicsApi
from akeno.backends.base import FileOp
from akeno.backends import ini,runtimes
from akeno.community.service import plan_digest
from akeno.community.runtime import regular
from akeno.security.downloads import CancelToken,Cancelled,sha256_file
from akeno.security import archive
from akeno.paths import cache_dir,resource_dir
from .catalog import CommunityCatalog
from .releases import GitHubReleases,version_key


@dataclass(frozen=True)
class PreparedMod:
    game_id:str
    recipe_id:str
    package_ids:tuple
    runtime_sha256:str
    plan:object
    plan_pin:str
    environment_pin:str
    selection_pin:str
    pipeline:object=None
    fiveM:dict|None=None


class ModManager:
    def __init__(self,services,catalog=None):
        self.s=services;self.catalog=catalog or CommunityCatalog.bundled();self.lock=threading.RLock()
        self.s.catalog=self.catalog.install_catalog(self.s.catalog)
        self.releases=GitHubReleases(services.db,[p['repository'] for p in self.catalog.projects.values()])

    def game_profile(self,game):
        for profile in self.catalog.profiles:
            if profile.get('launcher')=='fivem' and game.launcher=='fivem':return profile
            if game.launcher=='steam' and str(game.launcher_id)==profile.get('steam_app_id'):
                try:relative=Path(game.exe_path).relative_to(game.install_dir).as_posix().casefold()
                except (TypeError,ValueError):continue
                if relative in [p.casefold() for p in profile['executables']] and game.api.value in profile['apis']:return profile
        return None

    def runtime_choices(self):
        out={}
        for key,value in self.s.db.all_settings().items():
            if key.startswith('community-runtime:') and isinstance(value,dict):out[key.split(':',1)[1]]=value
        for sha,row in self.s.db.get_setting('official_dll_inventory',{}).items():
            if row.get('name')==runtimes.NR_RUNTIME and row.get('trust')=='nvidia_authenticated':out.setdefault(sha,dict(row,category='official'))
        for row in self.s.db.runtimes(runtimes.NR_RUNTIME):
            if row.get('trust')=='verified' and row.get('stored_path'):out.setdefault(row['sha256'],dict(row,category='official'))
        return out

    def selected_runtime(self,game,recipe=None):
        choice=self.s.db.game_overrides(game.id).get('mod_runtime');gpu=self.s.selected_gpu()
        from akeno.community.profiles import eligibility
        from akeno.hardware.support import runtime_verdict
        consumer='renodx_nr' if recipe and (recipe['family'].startswith('renodx_') or recipe['family']=='fivem') else 'optiscaler_nr'
        api='d3d12' if recipe and recipe['family'] in ('feeder','renodx_feeder','fivem') else game.api.value
        eligible=[]
        for pin,item in self.runtime_choices().items():
            review=item.get('review') or {};category=item.get('category')
            if category not in ('official','community_experimental','advanced_local','reviewed_community'):continue
            if gpu and runtime_verdict(item.get('version'),gpu.generation,self.s.compat_db.nr_runtimes(),build_sha256=pin).state=='fails':continue
            if review.get('schema')==3 and gpu:
                try:eligibility(review,gpu,consumer,api)
                except (ValueError,PermissionError):continue
            elif review.get('targets') and gpu:
                targets=[t for t in review['targets'] if (t.get('generation'),t.get('driver'),t.get('backend'),t.get('api'))==(gpu.generation,gpu.driver_version,consumer,api)]
                if not targets or any(t.get('status')=='incompatible' for t in targets):continue
            if pin==choice:return pin
            # On RTX 40 prefer an eligible explicitly imported community profile over an
            # official runtime with no exact older-GPU evidence. Neither is certified here.
            priority=2 if gpu and gpu.generation=='RTX 40' and category!='official' else 1 if category=='official' else 0
            eligible.append((priority,version_key(item.get('version')),pin))
        return max(eligible)[2] if eligible else None

    def select(self,game_id,recipe_id,runtime_sha=None,preset='quality'):
        if recipe_id not in self.catalog.recipes:raise ValueError('Unknown community recipe.')
        game=self.s.library.get(game_id)
        if not game:raise ValueError('Select an existing game.')
        override=self.s.db.game_overrides(game_id)
        override.update(mod_recipe=recipe_id,mod_runtime=runtime_sha,mod_preset=preset)
        opti=next((p for p in self.catalog.recipes[recipe_id].get('packages',[]) if self.catalog.packages[p].get('backend')=='optiscaler_nr'),None)
        if opti:override['mod_package.optiscaler_nr']=opti
        feeder=next((p for p in self.catalog.recipes[recipe_id].get('packages',[]) if self.catalog.packages[p].get('backend')=='dlss5_feeder'),None)
        if feeder:override['mod_package.dlss5_feeder']=feeder
        self.s.db.set_game_overrides(game_id,override)

    def selection_pin(self,game_id):
        return hashlib.sha256(json.dumps({'game':self.s.db.game_overrides(game_id),'components':self.s.pipelines.component_selection_pin()},sort_keys=True).encode()).hexdigest()

    def assessments(self,game_id,offline=False,runtime_sha=None):
        game=self.s.library.get(game_id)
        if not game:raise ValueError('Select an existing game.')
        profile=self.game_profile(game);choices=self.runtime_choices();gpu=self.s.selected_gpu()
        results=[]
        for recipe in self.catalog.recipes.values():
            if (game.launcher=='fivem')!=(recipe['family']=='fivem'):continue
            runtime=runtime_sha if runtime_sha is not None else self.selected_runtime(game,recipe)
            conditions=[]
            def need(kind,code,ok,message,remedy):
                if not ok:conditions.append({'kind':kind,'code':code,'message':message,'remedy':remedy})
            need('platform_prohibited','context',offline or game.launcher=='fivem','Confirm an eligible offline test.','Confirm offline / singleplayer mode.')
            need('technical_incompatibility','api',game.api.value in recipe['apis'],'This recipe does not support the selected renderer.','Choose the actual supported game renderer.')
            need('technical_incompatibility','architecture',game.architecture in recipe['architectures'],'This recipe needs a matching executable architecture.','Select the actual game executable.')
            need('platform_prohibited','protection',game.anti_cheat==AntiCheatRisk.NONE_FOUND and not self.s.compat_db.anti_cheat_entry(game),'Active client protection prohibits or has not cleared this route.','Use a permitted configuration; protections remain enabled.')
            need('missing_component','runtime',bool(runtime and runtime in choices),'A permitted local NR runtime is required.','Find local runtimes or import the GPU-specific community runtime once.')
            need('technical_incompatibility','gpu',bool(gpu and gpu.vendor.value=='nvidia' and gpu.generation in ('RTX 20','RTX 30','RTX 40','RTX 50')),'An NVIDIA RTX device is required.','Select the actual NVIDIA GPU.')
            if runtime and gpu:
                from akeno.community.profiles import eligibility
                from akeno.hardware.support import runtime_verdict
                record=choices.get(runtime,{})
                review=record.get('review') or {}
                consumer='renodx_nr' if recipe['family'].startswith('renodx_') or recipe['family']=='fivem' else 'optiscaler_nr'
                api='d3d12' if recipe['family'] in ('feeder','renodx_feeder','fivem') else game.api.value
                if review.get('schema')==3:
                    try:eligibility(review,gpu,consumer,api)
                    except (ValueError,PermissionError) as error:need('technical_incompatibility','runtime_profile',False,str(error),'Choose a runtime profile for this exact GPU, driver, backend and API.')
                elif review.get('targets'):
                    targets=[r for r in review['targets'] if (r.get('generation'),r.get('driver'),r.get('backend'),r.get('api'))==(gpu.generation,gpu.driver_version,consumer,api)]
                    need('technical_incompatibility','runtime_profile',bool(targets and all(r.get('status')!='incompatible' for r in targets)),'The runtime review does not cover this exact GPU, driver, backend and API.','Import a matching runtime profile.')
                verdict=runtime_verdict(record.get('version'),gpu.generation,self.s.compat_db.nr_runtimes(),build_sha256=runtime)
                need('technical_incompatibility','runtime_incompatible',verdict.state!='fails',verdict.text,'Choose an eligible GPU-specific runtime.')
            need('file_conflict','running',not game.is_running,'Close the game before changing files.','Close the selected game.')
            from akeno.pipeline.proxy import resolve_proxy,observed_loader_paths,load_candidates
            managed=self.s.installer.managed_files(game.id,game.target_dir) if game.target_dir else set()
            if recipe['family']=='native_optiscaler':
                from akeno.pipeline.discovery import native_support
                native,_=native_support(game,managed)
                need('missing_input','native',native is True,'No existing engine upscaler contract is established.','Use a complete Feeder route or a matching reviewed game profile.')
                need('file_conflict','loader',bool(resolve_proxy(game,managed,observed=observed_loader_paths(self.s,game))) if game.target_dir else False,'No free supported loader path is evidenced.','Analyze the selected executable or observe its actual module paths.')
            elif recipe['family']=='feeder':
                imports=load_candidates(game)
                need('missing_input','reshade_loader','dxgi.dll' in imports,'ReShade needs an evidenced DXGI load path.','Observe a supported loader before installing.')
                for role in ('reshade','motion_provider'):
                    present=self.s.pipelines.components.records(game.id).get(role) or self.bundled_components().get(role)
                    if role=='motion_provider' and present and (not present.get('technique') or 'provider_id' not in present):present=None
                    need('resolvable_dependency' if present else 'missing_component',role,bool(present),msg('A compatible {role} is required.',role=role),'Resolve the bundled dependency or import it once.')
                need('resolvable_dependency' if self.official_sr() else 'missing_component','official_sr',bool(self.official_sr()),'An official SR runtime is required by Feeder.','Find and import a permitted local NVIDIA SR runtime.')
            elif recipe['family'].startswith('renodx_'):
                from akeno.pipeline.discovery import native_support
                from akeno.fivem.community_stack import CONTRACT
                native,_=native_support(game,managed)
                if recipe['family']=='renodx_native':need('missing_input','native',native is True,'No existing engine DLSS contract is established.','Choose the complete Feeder recipe instead.')
                need('missing_input','reshade_loader','dxgi.dll' in load_candidates(game),'ReShade needs an evidenced DXGI load path.','Observe a supported loader before installing.')
                roles=['reshade','neural_consumer']+(['motion_provider'] if recipe['family']=='renodx_feeder' else [])
                for role in roles:
                    item=self.s.pipelines.components.records(game.id).get(role) or self.bundled_components().get(role)
                    need('resolvable_dependency' if item else 'missing_component',role,bool(item),msg('A compatible {role} is required.',role=role),'Resolve the bundle or import the licensed NR consumer once.')
                    if role=='neural_consumer' and item:need('technical_incompatibility','consumer_contract',item.get('contract')==CONTRACT,'The consumer needs the exact NR ConfigVersion 7 contract.','Import the matching permitted NR add-on.')
                if recipe['family']=='renodx_feeder':need('missing_component','official_sr',bool(self.official_sr()),'Feeder needs an authenticated NVIDIA SR runtime.','Find a permitted local SR runtime.')
            elif recipe['family']=='fivem':
                from akeno.fivem.community_stack import assess
                details=assess(self.s,game)
                conditions.extend(details['conditions'])
            else:need('missing_component','consumer',False,'An exact licensed NR consumer and its reviewed native contract are required.','Import an individually reviewed compatible consumer; HDR add-ons are different.')
            for pid in recipe.get('packages',[]):
                package=self.catalog.packages[pid]
                need('missing_component','package:'+pid,package.get('installable'),msg('{name} {version} needs its complete dependency review.',name=package['name'],version=package['version']),'Choose an installable pinned version.')
                for changed in self.releases.asset_changes(self.catalog.projects[package['project']]['repository'],[package]):
                    need('technical_incompatibility','asset_changed',self.s.package_cached(self.s.catalog.package(pid)),'Upstream removed or altered this exact release asset.','Keep the cached known-good version; review the upstream change.')
            from akeno.pipeline.conflicts import inspect_conflicts
            if recipe['family']!='fivem':
                consumers,conflicts=inspect_conflicts(game,managed)
                if consumers and recipe.get('consumers') and any(c not in recipe['consumers'] for c in consumers):conflicts.append(msg('Another NR consumer is installed. Restore it before changing consumer families.'))
                for text in conflicts:need('file_conflict','foreign_mod',False,text,'Restore the mod with its original manager or choose a reviewed coexistence recipe.')
            hard=[c for c in conditions if c['kind']!='resolvable_dependency']
            installed=self.s.installer.active_install(game.id)
            results.append({'id':recipe['id'],'name':recipe['name'],'family':recipe['family'],'status':'needs_action' if hard else 'experimental',
                'eligible':not hard,'conditions':conditions,'packages':[self.catalog.packages[p] for p in recipe.get('packages',[])],
                'runtime_sha256':runtime,'profile':profile,'installed':installed,'recommendation_rank':3 if not hard else 4,
                'locally_verified':False,'presets':recipe.get('presets',[])})
        # Exact process-bound execution is a ranking fact, never an installation prerequisite.
        session=self.s.community.sessions.get(game.id)
        for result in results:
            if session and not session.ended and session.verified and result['installed'] and result['installed']['params'].get('_mod_recipe')==result['id']:
                result.update(locally_verified=True,recommendation_rank=1)
        return sorted(results,key=lambda r:(not r['eligible'],r['recommendation_rank'],r['family']!='native_optiscaler',
            tuple(-x if isinstance(x,int) else x for x in version_key(r['packages'][0]['version'])[:5]) if r['packages'] else ()))

    def official_sr(self):
        row=self.s.runtime_lookup(runtimes.DLSS_RUNTIME)
        if row:return row
        rows=self.s.db.get_setting('official_dll_inventory',{})
        return next((r for r in sorted(rows.values(),key=lambda r:version_key(r.get('version')),reverse=True) if r.get('family')=='super_resolution' and r.get('trust')=='nvidia_authenticated'),None)

    def bundled_components(self):
        path=resource_dir()/'community/dependencies/manifest.json'
        if not path.is_file():return {}
        data=json.loads(path.read_text(encoding='utf-8'))
        return {r['role']:r for r in data.get('components',[])}

    def resolve_dependencies(self,token=None,progress=None):
        path=resource_dir()/'community/dependencies/manifest.json'
        if path.is_file():
            data=json.loads(path.read_text(encoding='utf-8'))
            roles={item['role'] for item in data.get('components',[]) if not self.s.pipelines.components.verified(item['role'])}
            if roles:self.s.pipelines.components.import_manifest(path,True,roles=roles)
            for item in data.get('packages',[]):
                package=self.s.catalog.package(item['id'])
                if not package or package.sha256!=item['sha256']:raise ValueError('Bundled release is outside the reviewed catalog.')
                source=path.parent/item['path'];regular(source)
                if not source.resolve().is_relative_to(path.parent.resolve()) or sha256_file(source)!=package.sha256:raise ValueError('Bundled package hash changed.')
                import shutil
                dest=self.s.downloads.cached_path(package.sha256,Path(package.url).name)
                if not dest.exists():shutil.copyfile(source,dest)
                if sha256_file(dest)!=package.sha256:raise ValueError('Changed release cache.')
        if token and token.cancelled:raise Cancelled('Dependency resolution cancelled.')
        return self.s.pipelines.components.records()

    def refresh_releases(self,token=None,progress=None):
        results=[]
        for index,project in enumerate(self.catalog.projects.values()):
            if token and token.cancelled:raise Cancelled('Release refresh cancelled.')
            try:
                releases=self.releases.refresh(project['repository'],token);results.append({'project':project['id'],'releases':len(releases),'ok':True})
            except Exception as error:
                if isinstance(error,Cancelled):raise
                results.append({'project':project['id'],'ok':False,'error':str(error),'cached_versions_preserved':True})
            if progress:progress(index+1,len(self.catalog.projects))
        self.s.db.set_setting('mod-last-release-refresh',results)
        return results

    def import_consumer(self,path,version,source,license,permitted=False):
        if permitted is not True:raise PermissionError('Confirm the component license and local-use permission.')
        if not version.strip() or not license.strip() or not source.startswith('https://'):raise ValueError('Version, source and license are required.')
        regular(Path(path))
        import shutil,uuid
        folder=cache_dir()/'consumer-imports'/uuid.uuid4().hex;folder.mkdir(parents=True)
        dest=folder/'renodx-dlss5.addon64';shutil.copyfile(path,dest)
        manifest={'schema':1,'distribution':'private-local-use','components':[{'role':'neural_consumer','path':dest.name,
            'sha256':sha256_file(dest),'version':version,'source':source,'license':license,'local_use_permitted':True,
            'architecture':'x64','apis':['d3d12'],'contract':'renodx-dlss5-config7'}]}
        file=folder/'manifest.json';file.write_text(json.dumps(manifest))
        return self.s.pipelines.components.import_manifest(file,True)

    def discover_runtimes(self,permitted=False,token=None):
        if permitted is not True:raise PermissionError('Confirm local runtime reuse once before automatic import.')
        imported=[]
        for name in (runtimes.NR_RUNTIME,runtimes.DLSS_RUNTIME):
            if token and token.cancelled:raise Cancelled('Runtime discovery cancelled.')
            report=runtimes.discover_report(name,[g.install_dir for g in self.s.library.games()],verifier=self.s.community.runtimes.verifier)
            best=report.best()
            if best:
                imported.append(runtimes.import_candidate(self.s.db,best))
        return imported

    def preset_params(self,game,preset,custom=None):
        adapter=self.s.registry.get('optiscaler_nr')
        selected=next((p for p in adapter.profiles(game) if p.id==preset),None)
        if preset=='custom':values=dict(custom or {})
        elif selected:values=selected.params
        else:raise ValueError('This preset is not supported by the selected API/version.')
        specs={p.key:p for p in adapter.param_specs(game)}
        if any(k not in specs for k in values):raise ValueError('Unsupported backend setting.')
        return {key:spec.coerce(values.get(key,spec.default)) for key,spec in specs.items()}

    def prepare(self,game_id,recipe_id,runtime_sha=None,preset='quality',offline=False,consent=False,token=None,progress=None,custom=None):
        if consent is not True:raise PermissionError('Confirm experimental community installation and local component reuse.')
        with self.lock:
            initial=self.s.library.get(game_id);recipe=self.catalog.recipes[recipe_id]
            selected=runtime_sha or self.selected_runtime(initial,recipe)
            if not selected or (recipe['family'] in ('feeder','fivem','renodx_feeder') and not self.official_sr()):self.discover_runtimes(True,token)
            selected=runtime_sha or self.selected_runtime(initial,recipe)
            self.select(game_id,recipe_id,selected,preset)
            self.resolve_dependencies(token,progress)
            game=self.s.library.reanalyze(game_id)
            decision=next(r for r in self.assessments(game_id,offline,selected) if r['id']==recipe_id)
            if not decision['eligible']:raise PermissionError('\n'.join(c['message']+' '+c['remedy'] for c in decision['conditions']))
            runtime=decision['runtime_sha256'];recipe=self.catalog.recipes[recipe_id]
            if recipe['family']=='fivem':
                from akeno.fivem.community_stack import prepare
                prepared=prepare(self.s,game,recipe,runtime,preset,token,progress,custom)
                from .validation import validate_sources
                validate_sources(prepared.plan);return prepared
            if recipe['family'].startswith('renodx_'):
                from .renodx import prepare
                prepared=prepare(self.s,game,recipe,runtime,preset,offline,consent,token,progress,custom)
                from .validation import validate_sources
                validate_sources(prepared.plan);return prepared
            if recipe['family'] not in ('native_optiscaler','feeder'):raise PermissionError('This consumer has no complete reviewed deployment contract.')
            config_game=replace(game,api=GraphicsApi.D3D12) if recipe['family']=='feeder' else game
            params=self.preset_params(config_game,preset,custom)
            if recipe['family']=='feeder':params.update({'DlssNr.PreSr':False})
            for pid in recipe['packages']:
                item=self.catalog.packages[pid]
                if item['backend']=='optiscaler_nr':
                    unsupported={k.split('.',1)[1] for k in params if k.startswith('DlssNr.')} - set(item.get('supported_nr_keys',[]))
                    if unsupported:raise PermissionError('Settings absent from this version contract: '+', '.join(sorted(unsupported)))
                package=self.s.catalog.package(pid)
                if token and token.cancelled:raise Cancelled('Installation cancelled before staging.')
                # Resolve complete pinned download queue up front; existing stage() reuses these bytes.
                self.s.downloads.fetch(package.url,package.sha256,Path(package.url).name,package.size,progress,token)
            prepared=self.s.pipelines.prepare(game_id,recipe['family'],runtime,offline,consent,params=params)
            from .validation import validate_sources
            validate_sources(prepared.plan)
            prepared.plan.params.update(_mod_recipe=recipe_id,_mod_packages=recipe['packages'],_mod_preset=preset)
            prepared=replace(prepared,plan_pin=plan_digest(prepared.plan))
            return PreparedMod(game_id,recipe_id,tuple(recipe['packages']),runtime,prepared.plan,prepared.plan_pin,
                prepared.environment_pin,self.selection_pin(game_id),pipeline=prepared)

    def install(self,prepared):
        with self.lock:
            if self.selection_pin(prepared.game_id)!=prepared.selection_pin or plan_digest(prepared.plan)!=prepared.plan_pin:raise PermissionError('Game selection or prepared files changed. Preview again.')
            from .validation import validate_sources
            validate_sources(prepared.plan)
            if prepared.fiveM is not None:
                from akeno.fivem.community_stack import install
                return install(self.s,prepared)
            if prepared.pipeline is None:
                from .renodx import install
                return install(self.s,prepared)
            return self.s.pipelines.install(prepared.pipeline)

    def configure(self,game_id,preset,custom=None):
        with self.lock:
            game=self.s.library.reanalyze(game_id);active=self.s.installer.active_install(game_id)
            if not active or not active['params'].get('_mod_recipe'):raise PermissionError('Install a managed community recipe first.')
            if self.s.installer.running_check(game.install_dir):raise PermissionError('Close the game before changing its preset.')
            if game.launcher=='fivem' or active['backend_id']=='community:renodx_nr':
                from akeno.fivem.community_stack import configure
                result=configure(self.s,game,active,preset,custom)
                if result.ok:
                    overrides=self.s.db.game_overrides(game_id);overrides.update(mod_preset=preset,mod_custom=custom);self.s.db.set_game_overrides(game_id,overrides)
                return result
            consumer_game=replace(game,api=GraphicsApi.D3D12) if self.catalog.recipes[active['params']['_mod_recipe']]['family']=='feeder' else game
            params=self.preset_params(consumer_game,preset,custom)
            if self.catalog.recipes[active['params']['_mod_recipe']]['family']=='feeder':params['DlssNr.PreSr']=False
            manifest=self.catalog.package(active['package_id'])
            if manifest and {k.split('.',1)[1] for k in params if k.startswith('DlssNr.')} - set(manifest.get('supported_nr_keys',[])):raise PermissionError('The installed version does not support the selected settings.')
            adapter=self.s.registry.get('optiscaler_nr');cfg=Path(active['target_dir'])/'OptiScaler.ini';regular(cfg)
            # Configuration is a separate journalled change in the same ownership chain.
            plan=adapter.plan_configure(consumer_game,self.s.catalog.package(active['package_id']),params,self.s.context(game),cfg.read_text(encoding='utf-8-sig'))
            plan.backend_id=active['backend_id'];plan.params=dict(active['params'],**params,_mod_preset=preset)
            result=self.s.installer.apply(game,plan)
            if result.ok:
                self.s.db.save_profile(game_id,preset,'optiscaler_nr',params,True)
                overrides=self.s.db.game_overrides(game_id);overrides.update(mod_preset=preset,mod_custom=custom);self.s.db.set_game_overrides(game_id,overrides)
            return result

    def previous(self,game_id):
        """Reverse the latest install plus later presets as a NEW backed-up transaction.

        Original restore points and superseded versions remain intact. Version-only
        files are removed through the same pre-journalled, recoverable engine.
        """
        from akeno.backends.base import InstallPlan
        from akeno.install.transaction import TxResult
        with self.lock:
            game=self.s.library.reanalyze(game_id);active=self.s.installer.active_install(game_id)
            if not active or not active['params'].get('_mod_recipe'):raise PermissionError('No managed community version to change.')
            chain=self.s.installer._chain(active)
            previous=next((t for t in chain if t['id']<active['id'] and t['kind'] in ('install','swap')),None)
            if not previous:raise PermissionError('No previous managed version is available. Use Remove mod to restore originals.')
            if game.launcher=='fivem':
                self.s.fivem._stopped()
                from akeno.fivem.discovery import inspect_installation
                from akeno.fivem.paths import guard_target
                installation=inspect_installation(game.install_dir)
                if not installation or str(installation.plugin_dir)!=active['target_dir']:raise PermissionError('FiveM target changed.')
                guard_target(installation.plugin_dir,installation.app_dir)
            conflicts=self.s.installer.check_restore(active['id'])
            if conflicts:return TxResult(False,active['id'],'installed',['Previous version blocked by changed files or backup integrity.'],conflicts)
            previous_params=next(t['params'] for t in chain if previous['id']<=t['id']<active['id'])
            undo=[t for t in chain if t['id']>=active['id']]
            originals,latest=self.s.installer._file_views(undo)
            plan=InstallPlan(active['backend_id'],self.s.catalog.package(previous['package_id']),active['target_dir'],
                params=dict(previous_params),summary=['Restore previous managed version '+str(previous['params'].get('_mod_consumer_version') or previous['package_version'])+' using verified transaction backups.'])
            for rel,record in originals.items():
                if record['action']=='replace':plan.ops.append(FileOp(rel,source=Path(record['backup_path']),expected_sha256=record['original_sha256']))
                elif record['action']=='create':
                    path=Path(active['target_dir'])/rel
                    if path.exists():plan.ops.append(FileOp(rel,kind='delete',expected_sha256=latest[rel]['installed_sha256']))
                else:raise PermissionError('This version contains a rename operation; use full restore instead.')
            result=self.s.installer.apply(game,plan)
            if result.ok:
                self.select(game_id,previous_params['_mod_recipe'],previous_params.get('_community_runtime'),previous_params.get('_mod_preset','quality'))
                for role,item in previous_params.get('_mod_components',{}).items():
                    self.s.pipelines.components.select(game_id,role,item['sha256'])
            return result

    def restore(self,game_id):
        active=self.s.installer.active_install(game_id)
        if not active:raise ValueError('No managed installation to restore.')
        if active['backend_id'].startswith('fivem_community:'):
            from akeno.fivem.community_stack import restore
            return restore(self.s,self.s.library.get(game_id),active)
        return self.s.restore(active['id'])

    def rendering_status(self,game_id):
        game=self.s.library.get(game_id);active=self.s.installer.active_install(game_id)
        if game.launcher!='fivem':return self.s.community.report(game_id)
        from dataclasses import asdict
        from akeno.diagnostics import neural
        from akeno.fivem.discovery import inspect_installation
        installation=inspect_installation(game.install_dir)
        if not installation:raise PermissionError('Inspect the FiveM client before reading its logs.')
        return {'installation':'installed' if active else 'not_installed',
            'game_renderer':game.api.value,'consumer_transport':active['params'].get('_transport') if active else None,
            'historical_logs':asdict(neural.inspect(installation.plugin_dir)),
            'current_feature18_execution':'not_verified',
            'note':'Historical logs are not attributed to a current GTAProcess. Use the existing FiveM live diagnostics to inspect its actual modules and renderer; verify Feature 18 separately.'}

    def launch(self,game_id,offline=False,consent=False):
        game=self.s.library.reanalyze(game_id)
        if game.launcher=='fivem':
            from akeno.fivem.community_stack import launch
            return launch(self.s,game,consent)
        active=self.s.installer.active_install(game_id)
        if not active or not active['params'].get('_mod_recipe'):raise PermissionError('Install a managed community recipe first.')
        recipe=self.catalog.recipes[active['params']['_mod_recipe']]
        if recipe['family']=='native_optiscaler':return self.s.community.launch_test(game_id,offline,consent)
        if not offline or consent is not True:raise PermissionError('Confirm an eligible offline community session.')
        from akeno.paths import is_windows
        if not is_windows():raise PermissionError('Game launch requires Windows.')
        decision=next(r for r in self.assessments(game_id,True,active['params']['_community_runtime']) if r['id']==recipe['id'])
        if not decision['eligible'] or self.s.installer.check_restore(active['id']):raise PermissionError('The complete stack changed or is no longer eligible.')
        if recipe['family'].startswith('renodx_'):
            from akeno.fivem.community_stack import _consumer
            _consumer(self.s,active['params']['_community_runtime'])
        else:
            virtual=replace(game,upscaler_inputs=['dlss'],api=GraphicsApi.D3D12)
            self.s.community._check(virtual,self.s.community._candidate(active['params']['_community_runtime']),'optiscaler_nr',True,True)
        import os,subprocess,re
        self.s.community.observe(game_id)
        if game.launcher=='steam' and re.fullmatch(r'[0-9]+',str(game.launcher_id or '')):os.startfile('steam://rungameid/'+str(game.launcher_id))
        else:subprocess.Popen([str(Path(game.exe_path).resolve())],cwd=str(Path(game.exe_path).parent))
        return {'launch_requested':True,'execution':'not_tested'}
