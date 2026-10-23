"""Compile the actual pinned motion and Feeder effects with ReShade FXC (no GPU)."""
import argparse
from pathlib import Path
import sys
import tempfile
import subprocess
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akeno.mods.catalog import CommunityCatalog
from akeno.security.archive import extract


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--compiler',type=Path,required=True);parser.add_argument('--bundle',type=Path,required=True);args=parser.parse_args()
    import json
    manifest=json.loads((args.bundle/'manifest.json').read_text());p=CommunityCatalog.bundled().packages['dlss5-feeder-1.19.0'];package=next(r for r in manifest['packages'] if r['id']==p['id'])
    with tempfile.TemporaryDirectory() as temp:
        temp=Path(temp);extract(args.bundle/package['path'],temp,{'reshade-shaders/Shaders/DLSS5_Feed.fx':p['files']['reshade-shaders/Shaders/DLSS5_Feed.fx']})
        for shader in [args.bundle/'dh-motion/dh_uber_motion.fx',temp/'reshade-shaders/Shaders/DLSS5_Feed.fx']:
            for renderer in ('0xB000','0xC000'):
                subprocess.run([str(args.compiler.resolve()),'-I',str((args.bundle/'reshade-framework').resolve()),'-D','__RENDERER__='+renderer,'-D','DLSS5_MV_PROVIDER=0','--hlsl','--width','1920','--height','1080','-Fo',str(temp/(shader.stem+renderer+'.hlsl')),str(shader.resolve())],check=True)
    print('Reviewed motion and Feeder effects compiled to HLSL for D3D11/D3D12. No GPU execution is claimed.')
