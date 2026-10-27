"""Real Full/Core installs and signed same-version module swaps on a disposable Windows runner.

No release/private production key is used. A throwaway signing key lives only in this Python process;
local package delivery verifies signed hashes and sizes. Real packaged executables perform startup/health.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tools'))


def run(args):
    if sys.platform!='win32':
        raise RuntimeError('This test requires a disposable real Windows runner.')
    from akeno import __version__
    from akeno.components.service import ComponentService, SECURITY_FILES, selection
    from akeno.licensing import crypto
    from akeno.updater import apply, service
    from akeno.updater.service import UpdateService
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from update_e2e import package, signed_release
    import release_manifest as signing
    work=args.work.resolve();work.mkdir(parents=True,exist_ok=False)
    target=work/'program';home=work/'user';home.mkdir()
    os.environ.update(AKENO_HOME=str(home),QT_QPA_PLATFORM='offscreen')
    from akeno.core.db import Database
    db=Database(home/'akeno.sqlite3')
    db.set_setting('check_app_updates',False)
    db.set_setting('rescan_on_start',False)
    db.close()
    sentinel=home/'preserved.bin';sentinel.write_bytes(os.urandom(64));saved=sentinel.read_bytes()
    checks=[]
    def check(condition,name):
        checks.append({'name':name,'passed':bool(condition)})
        if not condition:raise AssertionError(name)
    def physical(chosen):
        check(selection(target)==chosen,'installer/component selection marker')
        check(all((target/'_internal'/p).is_file()==chosen for p in SECURITY_FILES),'actual optional file selection')
        state=ComponentService(target,home/'components.sqlite3',True).status()['state']
        check(state==('active' if chosen else 'not_installed'),'verified physical component state')
        check(sentinel.read_bytes()==saved,'user data retained')
    def install(chosen):
        apply.stop_processes(target,grace=0.1)
        cmd=[str(args.installer.resolve()),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER',
             '/TYPE=custom','/COMPONENTS=core'+(',security_center' if chosen else ''),
             '/DIR='+str(target),'/LOG='+str(work/('full.log' if chosen else 'core.log'))]
        result=subprocess.run(cmd,timeout=300)
        check(result.returncode==0,'Inno installer exit status')
        physical(chosen)
        result=subprocess.run([str(target/'AKENO.exe'),'--smoke-test'],capture_output=True,text=True,timeout=120)
        (work/('full-smoke.log' if chosen else 'core-smoke.log')).write_text(result.stdout+result.stderr,encoding='utf-8')
        check(result.returncode==0 and 'smoke test OK' in result.stdout,'real installed executable smoke test')
    try:
        install(False);install(True);install(False)
        served=work/'served';served.mkdir()
        archive=package(args.dist.resolve(),served,__version__)
        key=Ed25519PrivateKey.generate();public=signing.public_of(key)
        envelope=json.loads(signed_release(key,__version__,1,archive))
        class LocalDelivery:
            def fetch(self,url,sha256,name,size=None,progress=None,token=None):
                if token and token.cancelled:
                    from akeno.security.downloads import Cancelled
                    raise Cancelled('Cancelled.')
                data=archive.read_bytes()
                if hashlib.sha256(data).hexdigest()!=sha256 or len(data)!=size:
                    raise ValueError('Local test package differs from signed bytes.')
                if progress:progress(len(data),len(data))
                return type('Download',(),{'path':archive,'size':len(data)})()
        service.install_dir=lambda:target
        service.frozen=lambda:True
        # In the product the running program is the installed AKENO.exe; here it is the runner's signed python.exe.
        # Apply the unchanged Authenticode rule against the real installed executable instead.
        rule=service.signature_check
        service.signature_check=lambda new_exe,running_exe=None,pin=None:rule(new_exe,target/'AKENO.exe',pin)
        svc=UpdateService(keys={signing.kid_of(public):crypto.public_key(public)},version=__version__,
                          folder=work/'updates',fetch=lambda url:envelope,downloads=LocalDelivery())
        def change(action,success=True):
            plan=svc.prepare_component(action)
            plan.update(pid=None,grace=0.1,settle=1,timeout=90)
            out=apply.run(plan)
            check(bool(out['ok'])==success,'real module '+action+(' committed' if success else ' rejected'))
            if not success:check(out['rolled_back'],'real failed-health rollback')
        change('install');physical(True)
        change('remove');physical(False)
        change('install');physical(True)
        apply.stop_processes(target,grace=0.1)
        (target/'_internal'/SECURITY_FILES[0]).write_text('# damaged optional component',encoding='utf-8')
        check(ComponentService(target,home/'components.sqlite3',True).status()['state']=='error','damaged optional module detected')
        change('repair');physical(True)
        # Authentic but broken package: actual packaged health must reject it and restore the Full install.
        apply.stop_processes(target,grace=0.1)
        archive=package(args.dist.resolve(),served,__version__,drop=('akeno/data/i18n_de.json',))
        envelope=json.loads(signed_release(key,__version__,2,archive))
        change('remove',success=False);physical(True)
        apply.stop_processes(target,grace=0.1)
        result=subprocess.run([str(target/'unins000.exe'),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'],timeout=120)
        deadline=time.monotonic()+60
        while (target/'AKENO.exe').exists() and time.monotonic()<deadline:time.sleep(0.1)
        check(result.returncode==0 and not (target/'AKENO.exe').exists(),'real uninstall removes program')
        check(sentinel.read_bytes()==saved,'uninstall retains user data')
    finally:
        apply.stop_processes(target,grace=0.1)
        args.report.write_text(json.dumps({'platform':'real Windows','checks':checks},indent=2),encoding='utf-8')
    print(f'{len(checks)} real Windows component checks passed')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--installer',type=Path,required=True)
    p.add_argument('--dist',type=Path,required=True)
    p.add_argument('--work',type=Path,required=True)
    p.add_argument('--report',type=Path,default=Path('component-windows-report.json'))
    run(p.parse_args())
