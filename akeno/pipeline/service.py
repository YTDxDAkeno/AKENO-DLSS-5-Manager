"""User-sized rendering operations built on AKENO's existing package and backup engines."""
from dataclasses import dataclass, replace
from pathlib import Path
import hashlib
import json
from akeno.backends.base import FileOp
from akeno.backends import runtimes
from akeno.community.service import plan_digest
from akeno.pipeline.manifests import ComponentStore, safe_relative
from akeno.pipeline.discovery import facts_for, dll_inventory
from akeno.pipeline.resolver import resolve
from akeno.games import pe
from akeno.security.downloads import sha256_file


@dataclass(frozen=True)
class PreparedPipeline:
    game_id: str
    route_id: str
    runtime_sha256: str
    plan: object
    plan_pin: str
    environment_pin: str
    components_pin: str
    offline: bool
    consent: bool


class PipelineService:
    def __init__(self,services):
        self.s=services;self.components=ComponentStore(services.db)

    def component_selection_pin(self):
        selection={'components':self.components.records(),'diagnostic':self.s.db.get_setting('diagnostic_optiscaler')}
        return hashlib.sha256(json.dumps(selection,sort_keys=True).encode()).hexdigest()

    def _components(self,runtime):
        result=self.components.records().copy()
        # Inventory is cheap. Full byte validation is at prepare and immediately before deploy.
        opti=self.s.catalog.best_package('optiscaler_nr');feeder=self.s.catalog.best_package('dlss5_feeder')
        if opti and opti.installable and opti.id=='optiscaler-dlss5-skynizz-1.3.0':
            result.update(optiscaler={'version':opti.version,'package':opti.id},forwarder={'version':opti.version,'package':opti.id})
        if feeder and feeder.installable and feeder.id=='dlss5-feeder-1.19.0':
            result.update(feeder={'version':feeder.version,'package':feeder.id},shader={'version':feeder.version,'package':feeder.id},host64={'version':feeder.version,'package':feeder.id})
        sr=self.s.runtime_lookup(runtimes.DLSS_RUNTIME)
        if sr:result['dlss_runtime']=sr
        if runtime:result['nr_runtime']=runtime
        if 'optiscaler' in result:result['selected_consumer']='optiscaler_nr'
        return result

    def analyze(self,game_id,offline=False,runtime_sha=None):
        game=self.s.library.get(game_id)
        if not game:raise ValueError('Select a game first.')
        saved=self.s.db.get_setting('community-runtime:'+runtime_sha) if runtime_sha else None
        runtime=dict(saved or {})
        if saved:runtime.update(sha256=runtime_sha,category='community_experimental')
        facts=facts_for(self.s,game,offline,runtime,self._components(runtime))
        routes=resolve(facts)
        session=self.s.community.sessions.get(game.id)
        active=self.s.installer.active_install(game.id)
        if session and active and not session.ended and session.transaction['id']==active['id'] and session.verified:
            route=next((r for r in routes if r.route_id==active['params'].get('_pipeline_route')),None)
            if route:
                route.execution_state='execution_verified'
                route.execution={'session_id':session.id,'process':session.process,'counters':dict(session.last_evidence.counters),
                    'scope':'Selected game session, distinct from installation eligibility and standalone tests.'}
        # ReShade route components are not interchangeable with generic RenoDX HDR addons.
        return {'game_id':game.id,'game':game.name,'facts':facts,'routes':routes,'dll_inventory':dll_inventory(game),
                'execution':'Not tested. Installation planning does not prove Neural Rendering.'}

    def prepare(self,game_id,route_id,runtime_sha,offline=False,consent=False):
        if consent is not True:raise PermissionError('Explicit experimental consent is required.')
        game=self.s.library.reanalyze(game_id)
        if not game:raise ValueError('Selected game is unavailable.')
        analysis=self.analyze(game_id,offline,runtime_sha)
        decision=next((r for r in analysis['routes'] if r.route_id==route_id),None)
        if not decision or not decision.eligible:raise PermissionError('\n'.join(c.detail for c in decision.requirements if not c.satisfied) if decision else 'Unknown rendering route.')
        if route_id not in ('native_optiscaler','feeder'):raise PermissionError('Use the component swap or application-settings workflow for this route. A source-verified RenoDX NR consumer is not currently shipped.')
        candidate=self.s.community._candidate(runtime_sha)
        if route_id=='native_optiscaler':
            prepared=self.s.community.prepare(game_id,runtime_sha,'optiscaler_nr',{},offline,consent)
            plan=prepared.plan
        else:
            # Feeder's synthetic DLSS contract is the input, not a claim of native game DLSS.
            virtual=replace(game,upscaler_inputs=['dlss'])
            adapter,ctx=self.s.community._check(virtual,candidate,'optiscaler_nr',offline,consent)
            package=self.s.package_for(adapter);staging=self.s.stage(game,adapter,package)
            ctx.staging_dir=staging
            plan=adapter.plan(virtual,package,adapter.default_params(game),ctx)
            feeder=self.s.registry.get('dlss5_feeder');fp=self.s.package_for(feeder)
            staged=self.s.stage(game,feeder,fp)
            for rel in ('dlss5-feed.addon64','reshade-shaders/Shaders/DLSS5_Feed.fx'):
                plan.ops.append(FileOp(rel,source=staged/rel,expected_sha256=fp.files[rel],description='Pinned Feeder 1.19.0 component'))
            imports=set(pe.parse(game.exe_path,with_version=False).imports)
            if 'dxgi.dll' not in imports:raise PermissionError('Feeder/ReShade needs an evidenced dxgi loader path; do not introduce an unproven proxy.')
            reshade=self.components.verified('reshade');motion=self.components.verified('motion_provider')
            if not reshade or not motion:raise PermissionError('Reimport missing or changed ReShade/motion-provider components.')
            self._component_op(game,plan,'dxgi.dll',reshade)
            destination=safe_relative(motion.get('destination','reshade-shaders/Shaders/'+Path(motion['path']).name))
            if not destination.startswith('reshade-shaders/') or not destination.endswith(('.fx','.fxh')):raise ValueError('Motion provider must use a shader destination.')
            self._component_op(game,plan,destination,motion)
            for asset in motion.get('assets',[]):self._component_op(game,plan,safe_relative(asset['destination']),asset)
            sr=self.s.runtime_lookup(runtimes.DLSS_RUNTIME)
            verified=runtimes.evaluate(sr['stored_path'],runtimes.DLSS_RUNTIME,verifier=self.s.community.runtimes.verifier)
            if verified.trust.value!='verified':raise PermissionError('Feeder SR runtime is not NVIDIA-authenticated.')
            self._component_op(game,plan,runtimes.DLSS_RUNTIME,sr)
            plan.backend_id='community:optiscaler_nr'
            plan.params.update(_community_runtime=runtime_sha,_review=candidate.review,_offline_attested=True,
                               _trust_category=candidate.category.value)
            plan.summary.append('Feeder creates synthetic inputs; configure the actual depth/motion provider in ReShade before testing. No NR execution is claimed.')
        # Use the central loader selection even if the old adapter preferred another free filename.
        marker=next((op for op in plan.ops if op.rel_path=='akeno-install.json'),None)
        proxy=next(op for op in plan.ops if op.source and op.source.name=='OptiScaler.dll')
        proxy.rel_path=decision.proxy
        if route_id=='feeder' and decision.proxy.casefold()=='dxgi.dll':raise PermissionError('ReShade and OptiScaler cannot both own dxgi.dll; an additional evidenced OptiScaler loader is required.')
        diagnostic=self.components.diagnostic()
        if diagnostic:
            for op in plan.ops:
                if op.source and op.source.name in diagnostic['binary_files']:
                    item=diagnostic['binary_files'][op.source.name]
                    op.source=Path(item['stored_path']);op.expected_sha256=item['sha256']
            plan.params['_diagnostic_build']=diagnostic['build']
            plan.params['_diagnostic_source']=diagnostic['source_commit']
            plan.summary.append('Source-identified local diagnostic backend selected; not NVIDIA-authenticated or a security certification.')
        plan.params['_pipeline_route']=route_id
        plan.params['_pipeline_components']=self.components.records()
        if marker:
            payload=json.loads(marker.content);payload['proxy']=decision.proxy;payload['pipeline_route']=route_id;payload['files']=[o.rel_path for o in plan.ops];marker.content=json.dumps(payload,indent=2).encode()
        managed=self.s.installer.managed_files(game.id,game.target_dir)
        for op in plan.ops:
            path=Path(game.target_dir)/op.rel_path
            if not path.resolve().is_relative_to(Path(game.target_dir).resolve()):raise PermissionError('Pipeline path escapes the game.')
            if path.exists() and op.rel_path.casefold() not in managed:raise PermissionError('An unmanaged file would be overwritten: '+op.rel_path)
            if op.source and sha256_file(op.source)!=op.expected_sha256:raise PermissionError('Staged component hash mismatch.')
        plan.summary.insert(0,'Rendering pipeline: '+decision.name)
        return PreparedPipeline(game.id,route_id,runtime_sha,plan,plan_digest(plan),self.s.community._environment(game,plan),
            self.component_selection_pin(),offline,consent)

    def _component_op(self,game,plan,destination,item):
        existing=Path(game.target_dir)/destination
        if existing.exists() and sha256_file(existing)==item['sha256']:return  # preserve matching foreign file, no ownership claim
        plan.ops.append(FileOp(destination,source=Path(item['stored_path']),expected_sha256=item['sha256'],description='User-permitted local component'))

    def install(self,prepared):
        if not prepared.offline or not prepared.consent or plan_digest(prepared.plan)!=prepared.plan_pin:raise PermissionError('The prepared pipeline changed.')
        game=self.s.library.reanalyze(prepared.game_id)
        analysis=self.analyze(game.id,prepared.offline,prepared.runtime_sha256)
        decision=next(r for r in analysis['routes'] if r.route_id==prepared.route_id)
        if not decision.eligible:raise PermissionError('Rendering eligibility changed. Prepare again.')
        candidate=self.s.community._candidate(prepared.runtime_sha256)
        virtual=replace(game,upscaler_inputs=['dlss']) if prepared.route_id=='feeder' else game
        self.s.community._check(virtual,candidate,'optiscaler_nr',True,True)
        if self.s.community._environment(game,prepared.plan)!=prepared.environment_pin:raise PermissionError('Game/hardware state changed after preview.')
        pin=self.component_selection_pin()
        if pin!=prepared.components_pin:raise PermissionError('Component selection changed after preview.')
        for op in prepared.plan.ops:
            if op.source and sha256_file(op.source)!=op.expected_sha256:raise PermissionError('A pinned pipeline source changed.')
        return self.s.installer.apply(game,prepared.plan)

    def diff(self,prepared):
        return [{'path':o.rel_path,'operation':'replace with backup' if (Path(prepared.plan.target_dir)/o.rel_path).exists() else 'create',
                 'sha256':o.expected_sha256 or hashlib.sha256(o.content or b'').hexdigest()} for o in prepared.plan.ops]
