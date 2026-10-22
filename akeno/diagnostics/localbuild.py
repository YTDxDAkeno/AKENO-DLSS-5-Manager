"""Optional explicit local source build. No NVIDIA files or game writes; no elevation."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from akeno.paths import app_home,is_windows,resource_dir
from akeno.security.downloads import sha256_file
from akeno.diagnostics.source_build import prepare,BASE


def prerequisites():
    result={'windows':is_windows(),'git':shutil.which('git'),'msbuild':None,'sdk':None,'missing':[]}
    program=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))
    vswhere=program/'Microsoft Visual Studio/Installer/vswhere.exe'
    if result['windows'] and vswhere.is_file():
        try:
            found=subprocess.run([str(vswhere),'-latest','-products','*','-requires','Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
                '-find','MSBuild/**/Bin/MSBuild.exe'],capture_output=True,text=True,timeout=15,creationflags=0x08000000)
            paths=[p for p in found.stdout.splitlines() if Path(p).is_file()]
            if paths:result['msbuild']=paths[0]
        except (OSError,subprocess.SubprocessError):pass
    sdk=program/'Windows Kits/10/Include'
    if sdk.is_dir():
        choices=sorted(p for p in sdk.glob('10.*') if (p/'um/d3d12.h').is_file())
        if choices:result['sdk']=str(choices[-1])
    for key,title in [('windows','64-bit Windows'),('git','Git for Windows'),('msbuild','Visual Studio 2022 C++ Build Tools (v143)'),('sdk','Windows 10/11 SDK with D3D12 headers')]:
        if not result[key]:result['missing'].append(title)
    result['ready']=not result['missing']
    return result


def _run(args,cwd,log,token,timeout=1800):
    """Direct child with bounded time and output on disk. Never ShellExecute/runas."""
    with log.open('ab') as output:
        proc=subprocess.Popen(args,cwd=cwd,stdout=output,stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+timeout
        try:
            while proc.poll() is None:
                if token and token.cancelled:raise InterruptedError('Local diagnostic build cancelled.')
                if time.monotonic()>deadline:raise TimeoutError('Local source build exceeded its time limit. Inspect build.log.')
                if log.stat().st_size>64*1024*1024:raise ValueError('Local build log exceeded its 64-MiB limit.')
                time.sleep(.2)
            if proc.returncode:raise RuntimeError('Source build command failed; inspect the local build.log for compiler/dependency errors.')
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)


def package(source):
    source=Path(source);record=json.loads((source/'AKENO_DIAGNOSTIC_SOURCE.json').read_text())
    out=source/'akeno-private-diagnostic';out.mkdir()
    files={}
    for name in ('OptiScaler.dll','nvngx.dll_dlssnr.dll'):
        matches=list((source/'x64/Release').rglob(name))
        if len(matches)!=1:raise ValueError('Expected exactly one compiled '+name)
        shutil.copyfile(matches[0],out/name);files[name]=sha256_file(out/name)
    record.update(binary_files=files,corresponding_source='Private local complete source checkout '+str(source),distribution='private-local-use')
    target=out/'diagnostic-manifest.json';target.write_text(json.dumps(record,indent=2),encoding='utf-8')
    return target


def build(explicit_action=False,token=None):
    if explicit_action is not True:raise PermissionError('Start the optional local source build explicitly.')
    req=prerequisites()
    if not req['ready']:raise PermissionError('Missing build prerequisites: '+', '.join(req['missing']))
    root=app_home()/'diagnostic-builds'/uuid.uuid4().hex;root.mkdir(parents=True)
    source=root/'source';log=root/'build.log'
    result={'schema':1,'status':'failed','source_commit':BASE,'source':str(source),'log':str(log),'distribution':'private-local-use'}
    try:
        _run([req['git'],'clone','--branch','v1.3.0','--single-branch','--no-checkout',
            'https://github.com/Skynizz/optiscaler-dlss5',str(source)],root,log,token)
        _run([req['git'],'checkout','--detach',BASE],source,log,token)
        _run([req['git'],'submodule','update','--init','--recursive'],source,log,token)
        # prepare() verifies exact HEAD again; no downloaded installers/binaries executed.
        prepare(source)
        submodules=subprocess.check_output([req['git'],'submodule','status','--recursive'],cwd=source,text=True,timeout=30)
        if any(row[:1] in ('-','+','U') for row in submodules.splitlines()):raise ValueError('A pinned source dependency is unavailable or changed.')
        (root/'submodules.txt').write_text(submodules,encoding='utf-8')
        _run([req['msbuild'],str(source/'OptiScaler.sln'),'/m','/nodeReuse:false','/p:Configuration=Release','/p:Platform=x64','/verbosity:minimal'],source,log,token)
        manifest=package(source)
        result.update(status='built',manifest=str(manifest),manifest_sha256=sha256_file(manifest),
            note='Local source declaration; not an NVIDIA signature or proof of NR hardware execution. Import this optional consumer explicitly.')
    except Exception as error:
        result['status']='cancelled' if isinstance(error,InterruptedError) else 'failed';result['error']=str(error)
    (root/'build-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result
