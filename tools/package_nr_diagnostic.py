"""Create a PRIVATE local import directory from an explicitly compiled diagnostic source tree."""
from pathlib import Path
import hashlib,json,shutil,sys
source=Path(sys.argv[1]).resolve()
record=json.loads((source/'AKENO_DIAGNOSTIC_SOURCE.json').read_text())
out=source/'akeno-private-diagnostic';out.mkdir(exist_ok=True)
files={}
for name in ('OptiScaler.dll','nvngx.dll_dlssnr.dll'):
    matches=[p for p in (source/'x64'/'Release').rglob(name) if p.is_file()]
    if len(matches)!=1:raise ValueError('Expected exactly one compiled '+name)
    shutil.copyfile(matches[0],out/name);files[name]=hashlib.sha256((out/name).read_bytes()).hexdigest()
record.update(binary_files=files,corresponding_source='Private local source tree '+str(source),distribution='private-local-use')
(out/'diagnostic-manifest.json').write_text(json.dumps(record,indent=2))
print(out/'diagnostic-manifest.json')
