"""Pinned Feeder host --test in a fresh private directory, never inside a game."""
from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from akeno.paths import app_home, is_windows
from akeno.backends import runtimes
from akeno.core.db import now_iso
from akeno.core import processes
from akeno.core.models import Game, GraphicsApi
from akeno.security import archive
from akeno.security.downloads import sha256_file
from akeno.games import pe
from akeno.community.runtime import regular
from akeno.diagnostics.privacy import redact

HOST='host64/dlss5-feed-host64.exe'


@dataclass
class SelfTestResult:
    id: str
    status: str = 'inconclusive'
    scope: str = 'Standalone runtime/consumer self-test only; no game compatibility is established.'
    started_at: str = field(default_factory=now_iso)
    duration_s: float = 0
    completion: str = 'not_started'
    gpu: dict | None = None
    host_adapter: dict = field(default_factory=dict)
    runtime: dict = field(default_factory=dict)
    consumer: dict = field(default_factory=dict)
    process: dict = field(default_factory=dict)
    ngx_initialization: str = 'not_observed'
    feature_18_creation: str = 'not_observed'
    dlss_evaluations: dict = field(default_factory=dict)
    neural_evaluations: dict = field(default_factory=dict)
    error_codes: list = field(default_factory=list)
    limitations: list = field(default_factory=list)


def parse_host_result(text,neural_text='',exit_code=None):
    """Only the exact pinned host's summary counts SR. It must not become an NR count."""
    result={'ngx_initialization':'not_observed','feature_18_creation':'not_observed',
            'dlss_evaluations':{},'neural_evaluations':{},'error_codes':[], 'status':'inconclusive'}
    init=re.findall(r'\[host\] (?:NVSDK_NGX_D3D12_Init|Init_with_ProjectID) -> (0x[0-9a-fA-F]+)',text)
    if init:result['ngx_initialization']='success' if int(init[-1],16)==1 else 'failed'
    match=re.search(r'\[host\] --test finished: (\d+)/(\d+) evaluates succeeded',text)
    if match:
        good,total=map(int,match.groups())
        if total==300 and 0<=good<=total:result['dlss_evaluations']={'attempt_limit':total,'successful':good,'failed':1 if good<total else 0,'attempted':good+(good<total)}
    from akeno.diagnostics.neural import classify
    stages=[classify(line) for line in neural_text.splitlines() if len(line)<=4096]
    if 'initialized' in stages:result['feature_18_creation']='success'
    if 'failed' in stages or result['ngx_initialization']=='failed':result['status']='fail'
    elif 'initialized' in stages and 'running' in stages and exit_code==0:
        result['status']='pass'
        # Log events are not a frame counter; exact success count remains unknown.
        result['neural_evaluations']={'successful':None,'failed':None,'evidence':'Explicit fresh NR create/evaluate success events; no exact counter available.'}
    result['error_codes']=sorted(set(re.findall(r'0x(?:BAD[0-9a-fA-F]{5}|C000[0-9a-fA-F]{4})\b',text+'\n'+neural_text,re.I)))
    if exit_code not in (None,0):result['status']='fail'
    return result


class NeuralSelfTest:
    def __init__(self,services):self.s=services

    def run(self,runtime_sha,explicit_action=False,token=None,timeout=180):
        if explicit_action is not True:raise PermissionError('Start the runtime self-test explicitly.')
        if not is_windows():raise PermissionError('A physical Windows NVIDIA system is required for the runtime self-test.')
        candidate=self.s.community._candidate(runtime_sha)
        from akeno.community.runtime import Category
        if candidate.category not in (Category.OFFICIAL,Category.COMMUNITY,Category.ADVANCED):raise PermissionError(candidate.detail)
        gpu=self.s.selected_gpu()
        if not gpu or gpu.vendor.value!='nvidia' or gpu.is_software or not gpu.driver_version:raise PermissionError('Detected NVIDIA hardware and driver are required.')
        from akeno.community.profiles import eligibility
        if candidate.review and candidate.review.get('schema')==3:eligibility(candidate.review,gpu,'optiscaler_nr','d3d12')
        elif candidate.category!=Category.OFFICIAL:
            targets=(candidate.review or {}).get('targets',[])
            matches=[row for row in targets if (row.get('generation'),row.get('driver'),row.get('backend'),row.get('api'))==(gpu.generation,gpu.driver_version,'optiscaler_nr','d3d12')]
            if not matches or any(row.get('status')=='incompatible' for row in matches):raise PermissionError('Import an applicable local-use runtime profile for this exact hardware/driver/backend/API.')
        from akeno.hardware.support import runtime_verdict
        verdict=runtime_verdict(candidate.version,gpu.generation,self.s.compat_db.nr_runtimes(),build_sha256=candidate.sha256)
        if verdict.state=='fails':raise PermissionError(verdict.text)
        package=self.s.catalog.package('dlss5-feeder-1.19.0')
        opti=self.s.catalog.package('optiscaler-dlss5-skynizz-1.3.0')
        if not package or not opti or not package.installable or not opti.installable:raise PermissionError('Pinned compatible Feeder/OptiScaler packages are unavailable.')
        runner_pin=package.files.get(HOST)
        if not runner_pin:raise PermissionError('Known test-runner hash is missing.')
        root=app_home()/'self-tests'/uuid.uuid4().hex;root.mkdir(parents=True)
        result=SelfTestResult(root.name,gpu=gpu.to_dict(),runtime={'sha256':candidate.sha256,'version':candidate.version,'category':candidate.category.value},
                              consumer={'id':'optiscaler_nr','version':opti.version,'host_version':package.version})
        started=time.monotonic()
        try:
            source=self.s.offline_bundle.backend_archive(package)
            if source is None:
                if self.s.offline_bundle.distribution=='private-local-use':raise PermissionError('Pinned host archive missing from private offline bundle.')
                source=self.s.downloads.fetch(package.url,package.sha256,Path(package.url).name,package.size,token=token).path
            archive.extract(source,root,{HOST:runner_pin},package.root)
            # Keep all dynamically loaded components beside this isolated helper, not the game.
            work=root/'host64';host=work/'dlss5-feed-host64.exe'
            regular(host);info=pe.parse(host,with_version=False)
            if sha256_file(host)!=runner_pin or not info.valid or info.is_dll or info.machine!='x64':raise PermissionError('Test runner identity/architecture mismatch.')
            dummy=Game('selftest','Isolated runtime self-test',str(work),'manual',exe_path=str(host),api=GraphicsApi.D3D12,architecture='x64')
            adapter=self.s.registry.get('optiscaler_nr');staging=self.s.stage(dummy,adapter,opti)
            for member,destination in [('OptiScaler.dll','winmm.dll'),('nvngx.dll_dlssnr.dll','nvngx.dll_dlssnr.dll')]:
                src=staging/member
                if sha256_file(src)!=opti.files[member]:raise PermissionError('Consumer package member changed.')
                shutil.copyfile(src,work/destination)
            config=adapter.config_text((staging/'OptiScaler.ini').read_text(encoding='utf-8-sig'),dummy,adapter.default_params(dummy))
            (work/'OptiScaler.ini').write_text(config,encoding='utf-8')
            sr=self.s.runtime_lookup(runtimes.DLSS_RUNTIME)
            if not sr:raise PermissionError('Import an official DLSS Super Resolution runtime for the host contract.')
            checked=runtimes.evaluate(sr['stored_path'],runtimes.DLSS_RUNTIME,verifier=self.s.community.runtimes.verifier)
            if checked.trust.value!='verified':raise PermissionError('Self-test SR runtime is not NVIDIA-authenticated.')
            for src,destination,pin in [(candidate.path,runtimes.NR_RUNTIME,candidate.sha256),(sr['stored_path'],runtimes.DLSS_RUNTIME,sr['sha256'])]:
                regular(Path(src));shutil.copyfile(src,work/destination)
                if sha256_file(work/destination)!=pin:raise PermissionError('Runtime changed during isolated copy.')
            diagnostic=self.s.pipelines.components.diagnostic()
            if diagnostic:
                for name,destination in [('OptiScaler.dll','winmm.dll'),('nvngx.dll_dlssnr.dll','nvngx.dll_dlssnr.dll')]:
                    item=diagnostic['binary_files'][name];shutil.copyfile(item['stored_path'],work/destination)
                    if sha256_file(work/destination)!=item['sha256']:raise PermissionError('Diagnostic copy hash mismatch.')
                result.consumer['diagnostic_build']=diagnostic['build']
            result.consumer['file_pins']={p.name:sha256_file(p) for p in work.iterdir() if p.is_file()}
            if token and token.cancelled:raise InterruptedError('Self-test cancelled before launch.')
            with tempfile.TemporaryFile() as output:
                proc=subprocess.Popen([str(host),'--test'],cwd=work,stdout=output,stderr=subprocess.STDOUT,creationflags=0x08000000)
                result.process={'pid':proc.pid,'created_at':processes.creation_identity(proc.pid),'runner_sha256':runner_pin}
                result.completion='running';last_module_check=0;deadline=time.monotonic()+min(max(timeout,10),300)
                try:
                    while proc.poll() is None:
                        if token and token.cancelled:raise InterruptedError('Self-test cancelled.')
                        if time.monotonic()-last_module_check>=2:
                            result.process['module_inventory']=processes.module_inventory(proc.pid,result.process['created_at'])
                            last_module_check=time.monotonic()
                        if time.monotonic()>deadline:raise TimeoutError('Self-test exceeded its time limit.')
                        time.sleep(.2)
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        try:proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
            text=self._log(work/'dlss5-feed-host.log')
            neural='\n'.join(self._log(p) for p in work.glob('OptiScaler*.log'))
            parsed=parse_host_result(text,neural,proc.returncode)
            for key,value in parsed.items():setattr(result,key,value)
            if diagnostic:
                from akeno.diagnostics.counters import parse_counter,attributed
                records=[parse_counter(line) for line in neural.splitlines()]
                valid=[c for c in records if attributed(c,result.process,[diagnostic['build']])]
                if valid:
                    latest=max(valid,key=lambda c:c['seq'])
                    result.neural_evaluations={'attempted':latest['eval_attempts'],'successful':latest['eval_success'],'failed':latest['eval_fail'],'observed_lower_bound':True}
                    if latest['eval_fail'] and latest.get('last_error'):result.error_codes=sorted(set(result.error_codes+[latest['last_error']]))
                    result.feature_18_creation='success' if latest['create_success'] else 'not_observed'
                    result.status='fail' if latest['eval_fail'] or latest['create_fail'] else ('pass' if latest['eval_success'] and latest['create_success'] and proc.returncode==0 else 'inconclusive')
            adapter_match=re.search(r'\[host\] device adapter: (.*?)  LUID ([0-9a-fA-F]+:[0-9a-fA-F]+)  PCI ([0-9a-fA-F]+):([0-9a-fA-F]+)  driver ([0-9.]+)',text)
            if adapter_match:
                name,luid,vendor,device,driver=adapter_match.groups()
                result.host_adapter={'name':name,'luid':luid,'vendor_id':int(vendor,16),'device_id':int(device,16),'driver':driver}
            if not result.host_adapter or result.host_adapter['device_id']!=gpu.device_id or result.host_adapter['driver']!=gpu.driver_version:
                result.limitations.append('Host adapter/driver identity could not be matched to the selected GPU. Hardware result remains inconclusive.')
                if result.status=='pass':result.status='inconclusive'
            modules=result.process.get('module_inventory',{}).get('modules',[])
            model_loaded=any(Path(m['path']).resolve()==(work/runtimes.NR_RUNTIME).resolve() for m in modules)
            if not model_loaded:
                result.limitations.append('The exact isolated NR runtime was not confirmed in a read-only module snapshot.')
                if result.status=='pass':result.status='inconclusive'
            for name,pin in result.consumer['file_pins'].items():
                if name=='OptiScaler.ini':continue # legitimate overlay configuration changes are separate
                if not (work/name).is_file() or sha256_file(work/name)!=pin:raise ValueError('An isolated test binary changed during execution.')
            result.completion='completed';result.consumer['exit_code']=proc.returncode
            if not result.neural_evaluations:result.limitations.append('300 successful DLAA/SR calls do not prove Feature 18. Stock OptiScaler normal DX12 success may not be logged; use a source-identified diagnostic build for counts.')
        except Exception as error:
            result.completion='cancelled' if isinstance(error,InterruptedError) else ('timeout' if isinstance(error,TimeoutError) else 'setup_or_execution_error')
            result.status='inconclusive';result.limitations.append(str(error))
        finally:
            result.duration_s=round(time.monotonic()-started,3)
            payload=redact(asdict(result));(root/'result.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
            self.s.db.set_setting('last_neural_selftest',payload)
            import hashlib
            axes={'device_id':gpu.device_id,'driver':gpu.driver_version,'runtime_sha256':candidate.sha256,
                  'consumer':'optiscaler_nr','consumer_version':opti.version,'api':'d3d12','diagnostic_build':result.consumer.get('diagnostic_build')}
            identity=hashlib.sha256(json.dumps(axes,sort_keys=True).encode()).hexdigest()
            self.s.db.set_setting('runtime-selftest:'+identity,{'axes':axes,'result':payload,'scope':'standalone_only'})
        return payload

    @staticmethod
    def _log(path):
        if not path.is_file() or path.is_symlink():return ''
        with path.open('rb') as stream:return stream.read(4*1024*1024).decode('utf-8',errors='replace')
