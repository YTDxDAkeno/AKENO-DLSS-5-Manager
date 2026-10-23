"""Validate curated manifests; optionally verify downloaded archives without running code."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akeno.mods.catalog import CommunityCatalog
from akeno.security.downloads import DownloadManager,sha256_file
from akeno.security.archive import inspect,extract


def validate(archive_dir=None):
    catalog=CommunityCatalog.bundled();checked=[]
    for p in catalog.packages.values():
        if not p.get('installable'):continue
        if archive_dir:
            path=Path(archive_dir)/p['asset_name']
            if sha256_file(path)!=p['sha256']:raise ValueError('Archive digest changed: '+p['id'])
            from tempfile import TemporaryDirectory
            with TemporaryDirectory() as staging:extract(path,staging,p['files'],p.get('root',''))
        checked.append(p['id'])
    return {'schema':2,'installable_versions':checked,'recipes':list(catalog.recipes),'profiles':len(catalog.profiles),'archives_verified':bool(archive_dir)}


if __name__=='__main__':
    import json
    parser=argparse.ArgumentParser();parser.add_argument('--archive-dir',type=Path);args=parser.parse_args();print(json.dumps(validate(args.archive_dir),indent=2))
