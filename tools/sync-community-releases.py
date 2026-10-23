"""Find upstream releases newer than the catalog and prepare reviewed pins for them (maintainers).

    python tools/sync-community-releases.py --out proposal.json            # list new releases (GitHub API)
    python tools/sync-community-releases.py --out proposal.json --pin dl/  # also download + hash them

The proposal is input for a human review; this tool never edits akeno/data/stack/components.json. With --pin
every new release asset is downloaded once and each file the component installs is hashed with the production
extractor, so the reviewer can paste a complete, exact version entry. Nothing downloaded is executed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akeno.core.db import Database  # noqa: E402
from akeno.security.archive import extract  # noqa: E402
from akeno.security.downloads import sha256_file  # noqa: E402
from akeno.stack.catalog import StackCatalog  # noqa: E402
from akeno.stack.releases import GitHubReleases  # noqa: E402


def pin(v, folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / v.asset
    if not dest.is_file():
        req = urllib.request.Request(v.url, headers={"User-Agent": "AKENO-release-sync"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
    digest = sha256_file(dest)
    if v.sha256 and digest != v.sha256:
        raise SystemExit(f"{v.asset}: GitHub's published digest does not match the downloaded bytes")
    with tempfile.TemporaryDirectory() as tmp:
        files = extract(dest, tmp, {name: None for name in v.files}, v.root)
        hashes = {rel: sha256_file(p) for rel, p in files.items()}
    return {"version": v.version, "tag": v.tag, "asset": v.asset, "url": v.url, "sha256": digest,
            "size": dest.stat().st_size, "root": v.root, "files": hashes, "channel": "stable",
            "published": v.published, "review_required": ["channel", "gpus/recommended_for", "settings", "notes"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pin", type=Path, help="download new assets here and hash the installed files")
    args = ap.parse_args()
    cat = StackCatalog.load()
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "sync.sqlite3")
        repos = {c.repository for c in cat.components.values() if c.repository and c.release}
        client = GitHubReleases(db, repos, token_getter=lambda: os.environ.get("GITHUB_TOKEN", ""))
        results = client.refresh_all()
        rows = []
        for c in cat.components.values():
            new = client.discovered_versions(c)
            entry = {"component": c.id, "repository": c.repository, "new_versions": []}
            for v in new:
                item = {"version": v.version, "tag": v.tag, "asset": v.asset, "url": v.url,
                        "published_digest": v.sha256, "published": v.published}
                if args.pin:
                    item["pinned_entry"] = pin(v, args.pin)
                entry["new_versions"].append(item)
            rows.append(entry)
        db.close()
    proposal = {"schema": 3, "proposal_only": True, "refresh": [r.__dict__ for r in results], "components": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(proposal, indent=1) + "\n", encoding="utf-8")
    print(f"{sum(len(r['new_versions']) for r in rows)} new upstream version(s); proposal written to {args.out}")
    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
