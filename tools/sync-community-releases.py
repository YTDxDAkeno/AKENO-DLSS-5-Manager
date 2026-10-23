"""Discover original releases with the real API. Emits a proposal, never changes trusted pins."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akeno.mods.catalog import CommunityCatalog
from akeno.mods.releases import GitHubReleases
from akeno.core.db import Database
from akeno.paths import app_home


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--include-prereleases',action='store_true');parser.add_argument('--repository');parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    catalog=CommunityCatalog.bundled();repositories=[p['repository'] for p in catalog.projects.values()]
    if args.repository and args.repository not in repositories:parser.error('Repository is not in the reviewed discovery allowlist.')
    db=Database(app_home()/'community-sync.sqlite3');client=GitHubReleases(db,repositories);rows=[];failed=False
    try:
        for project in catalog.projects.values():
            if args.repository and args.repository!=project['repository']:continue
            repo=project['repository']
            try:client.refresh(repo);error=None
            except Exception as exc:error=str(exc);failed=True
            packages=[p for p in catalog.packages.values() if p['project']==project['id']]
            rows.append({'project':project,'error':error,'releases':client.available(repo,args.include_prereleases),
                'reviewed_versions':packages,'asset_changes':client.asset_changes(repo,packages),
                'approval_required':'Review exact API/architecture/dependencies/license plus archive and member hashes before editing the curated catalog.'})
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps({'schema':2,'proposal_only':True,'projects':rows},indent=2)+'\n')
    finally:db.close()
    raise SystemExit(1 if failed else 0)
