"""Build the redistributable dependency bundle from exact reviewed source inputs.

Run after MSBuild on Windows. No NVIDIA runtime or private consumer is accepted.
Source trees and notices travel with the binary; all hashes are generated at build time.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from akeno.games import pe
from akeno.security.downloads import DownloadManager,sha256_file
from akeno.security.archive import extract
from akeno.pipeline.manifests import ComponentStore
from tools.check_public_payload import check


def build(reshade_source,reshade_dll,out):
    pins=json.loads((ROOT/'community/vendor/pins.json').read_text())
    for name,pin in pins['files'].items():
        if sha256_file(ROOT/name)!=pin:raise ValueError('Changed vendored source: '+name)
    identity=subprocess.check_output(['git','-C',str(reshade_source),'rev-parse','HEAD'],text=True).strip()
    if identity!=pins['reshade']['commit']:raise ValueError('ReShade source identity differs from reviewed pin.')
    if subprocess.check_output(['git','-C',str(reshade_source),'diff','--name-only','HEAD'],text=True).strip():raise ValueError('ReShade source has local changes.')
    info=pe.parse(reshade_dll)
    if not info.valid or info.machine!='x64' or info.strings.get('ProductName')!='ReShade' or not {'ReShadeRegisterAddon','ReShadeUnregisterAddon'}<=set(info.exports):raise ValueError('A genuine x64 full add-on ReShade build is required.')
    if not str(info.file_version).startswith('6.8.0'):raise ValueError('Expected the reviewed ReShade 6.8.0 source version.')
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(reshade_dll,out/'ReShade64.dll')
    for folder in ('dh-motion','reshade-framework'):
        shutil.copytree(ROOT/'community/vendor'/folder,out/folder,dirs_exist_ok=True)
    shutil.copytree(ROOT/'community/licenses',out/'licenses',dirs_exist_ok=True)
    source_zip=out/'corresponding-source.zip'
    with zipfile.ZipFile(source_zip,'w',compression=zipfile.ZIP_DEFLATED) as z:
        tracked=subprocess.check_output(['git','-C',str(reshade_source),'ls-files','--recurse-submodules'],text=True).splitlines()
        for relative in [*tracked,'res/version.h']:
            path=reshade_source/relative
            if not path.is_file() or path.is_symlink() or path.suffix.lower() in ('.dll','.exe','.pdb','.obj','.lib','.exp'):continue
            z.write(path,'ReShade/'+relative)
        for path in (ROOT/'community/vendor').rglob('*'):
            if path.is_file():z.write(path,'vendor/'+path.relative_to(ROOT/'community/vendor').as_posix())
    base={'local_use_permitted':True,'architecture':'x64','apis':['d3d11','d3d12']}
    reshade=dict(base,role='reshade',version='6.8.0',path='ReShade64.dll',sha256=sha256_file(out/'ReShade64.dll'),
        source='https://github.com/crosire/reshade/tree/'+identity,license='BSD-3-Clause',source_commit=identity)
    assets=[]
    for name in ('ReShade.fxh',):
        # dh_uber_motion uses Reshade.fxh, Feeder uses ReShade.fxh. Windows paths are case-insensitive.
        assets.append(dict(path='reshade-framework/'+name,destination='reshade-shaders/Shaders/'+name,sha256=sha256_file(out/'reshade-framework'/name)))
    motion=dict(base,role='motion_provider',version='0.2.0',path='dh-motion/dh_uber_motion.fx',sha256=sha256_file(out/'dh-motion/dh_uber_motion.fx'),
        source='https://github.com/AlucardDH/dh-reshade-shaders/tree/'+pins['motion']['commit'],license='GPL-2.0',source_commit=pins['motion']['commit'],framework_license='CC0-1.0',
        destination='reshade-shaders/Shaders/dh_uber_motion.fx',provider_id=0,technique='DH_UBER_MOTION_020',assets=assets,requires={'reshade':'6.8.0'})
    stack=json.loads((ROOT/'akeno/data/stack/components.json').read_text())
    feeder=next(c for c in stack['components'] if c['id']=='dlss5-feeder')
    package=dict(next(v for v in feeder['versions'] if v['version']=='1.19.0'),id='dlss5-feeder-1.19.0')
    downloader=DownloadManager(['github.com','objects.githubusercontent.com','release-assets.githubusercontent.com'],cache=out/'packages')
    fetched=downloader.fetch(package['url'],package['sha256'],package['asset'],package['size'])
    source='https://codeload.github.com/jlrouzies-fr/DLSS5-Feeder/zip/'+package['source_commit']
    # Source download is original, immutable by commit identity, and never executed.
    import urllib.request
    source_path=out/'DLSS5-Feeder-source.zip'
    with urllib.request.urlopen(source,timeout=60) as response,source_path.open('wb') as destination:
        remaining=128*1024*1024
        while chunk:=response.read(min(1024*1024,remaining+1)):
            remaining-=len(chunk)
            if remaining<0:raise ValueError('Feeder source archive is too large.')
            destination.write(chunk)
    if sha256_file(source_path)!=package['source_archive_sha256']:raise ValueError('Feeder source archive digest changed.')
    with zipfile.ZipFile(source_path) as z:
        for suffix,name in [('/LICENSE','Feeder-MIT.txt'),('/LICENSE-NIGos-dlss5-bridge.md','Feeder-NIGos-bridge-MIT.txt')]:
            member=next(n for n in z.namelist() if n.endswith(suffix));(out/'licenses'/name).write_bytes(z.read(member))
    manifest={'schema':1,'distribution':'private-local-use','bundle_id':'akeno-community-2.0.0','public_redistribution':True,
        'components':[reshade,motion],'corresponding_source':{'path':source_zip.name,'sha256':sha256_file(source_zip)},
        'packages':[{'id':package['id'],'path':str(fetched.path.relative_to(out)).replace('\\','/'),'sha256':package['sha256']}],
        'additional_sources':[{'path':source_path.name,'sha256':sha256_file(source_path),'commit':package['source_commit']}]}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    # Importing validates the exact same file/API/include contract the application uses.
    from tempfile import TemporaryDirectory
    from akeno.core.db import Database
    import os
    with TemporaryDirectory() as local:
        previous=os.environ.get('AKENO_HOME');os.environ['AKENO_HOME']=local
        try:
            db=Database(Path(local)/'validate.sqlite');ComponentStore(db).import_manifest(out/'manifest.json',True);db.close()
        finally:
            if previous is None:os.environ.pop('AKENO_HOME',None)
            else:os.environ['AKENO_HOME']=previous
    check(out)
    (out/'SHA256SUMS.txt').write_text(''.join(sha256_file(p)+'  '+p.relative_to(out).as_posix()+'\n' for p in sorted(out.rglob('*')) if p.is_file() and p.name!='SHA256SUMS.txt'))
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--reshade-source',type=Path,required=True);parser.add_argument('--reshade-dll',type=Path,required=True);parser.add_argument('--out',type=Path,default=ROOT/'akeno/data/community/dependencies');args=parser.parse_args()
    build(args.reshade_source,args.reshade_dll,args.out)
