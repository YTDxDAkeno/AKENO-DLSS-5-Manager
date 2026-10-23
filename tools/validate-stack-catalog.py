"""Validate the schema 3 catalog against the original release assets (real-package contract check).

    python tools/validate-stack-catalog.py                       # catalog structure only
    python tools/validate-stack-catalog.py --download assets/    # fetch every pinned original asset (cached)
    python tools/validate-stack-catalog.py --archive-dir assets/ # verify them with the production extractor

For each downloadable version this checks the archive SHA-256, extracts every pinned member with AKENO's own
safe extractor (member SHA-256 enforced), checks that every binary is x64, that the consumer files are what
the recipes expect (OptiScaler.ini has [DlssNr] with every key a preset writes, the RenoDX add-on carries its
[RenoDX.DLSS5] section name, nvngx files identify themselves) and that no script from an archive is needed.
Nothing downloaded is executed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from akeno.backends import ini  # noqa: E402
from akeno.games import pe  # noqa: E402
from akeno.security.archive import extract  # noqa: E402
from akeno.security.downloads import sha256_file  # noqa: E402
from akeno.stack import configs  # noqa: E402
from akeno.stack.catalog import StackCatalog  # noqa: E402


def downloadable(cat: StackCatalog):
    for c in cat.components.values():
        for v in c.versions:
            if v.source == "download":
                yield c, v


def download(cat: StackCatalog, folder: Path) -> tuple[list[str], list[str]]:
    """Fetch every pinned asset from its catalog URL (the URL users download from). Every failure is reported."""
    folder.mkdir(parents=True, exist_ok=True)
    out, problems = [], []
    for c, v in downloadable(cat):
        dest = folder / v.asset
        if dest.is_file() and sha256_file(dest) == v.sha256:
            out.append(f"cached {v.asset}")
            continue
        error = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(v.url, headers={"User-Agent": "AKENO-catalog-validator"})
                with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
                    while chunk := resp.read(1 << 20):
                        fh.write(chunk)
                error = None
                break
            except urllib.error.HTTPError as exc:
                error = f"HTTP {exc.code}"
                if exc.code in (403, 404, 410):    # not transient: the pinned URL itself is wrong or gone
                    break
            except (urllib.error.URLError, OSError) as exc:
                error = str(exc)
            time.sleep(2 ** attempt)
        if error:
            dest.unlink(missing_ok=True)
            problems.append(f"{c.id} {v.version}: {v.url} -> {error}")
        elif sha256_file(dest) != v.sha256:
            problems.append(f"{c.id} {v.version}: downloaded bytes do not match the catalog pin")
        else:
            out.append(f"downloaded {v.asset}")
    return out, problems


def check_member(c, v, rel: str, path: Path, problems: list[str]) -> None:
    low = rel.lower()
    if low.endswith((".dll", ".addon64", ".exe", ".asi")):
        info = pe.parse(str(path))
        if not info.valid or info.machine != "x64":
            problems.append(f"{c.id} {v.version}: {rel} is not an x64 PE file")
    if low == "optiscaler.ini":
        sections = ini.get_values(path.read_text(encoding="utf-8-sig", errors="replace"))
        if "DlssNr" not in sections:
            problems.append(f"{c.id} {v.version}: OptiScaler.ini has no [DlssNr] section")
            return
        keys = set(sections["DlssNr"])
        missing = set(v.settings) - keys
        if missing:
            problems.append(f"{c.id} {v.version}: catalog lists settings absent from OptiScaler.ini: {sorted(missing)}")
        for preset in ("performance", "balanced", "quality", "ultra"):
            for api in ("d3d12", "d3d11", "vulkan"):
                written = configs.optiscaler_values(preset, None, c.features, api, v.settings)["DlssNr"]
                unknown = set(written) - keys - {"WhitePointSource"}
                if unknown:
                    problems.append(f"{c.id} {v.version}: {preset}/{api} writes unknown keys {sorted(unknown)}")
    if low == "renodx-dlss5.addon64":
        data = path.read_bytes()
        if b"RenoDX.DLSS5" not in data:
            problems.append(f"{c.id} {v.version}: add-on does not carry the [RenoDX.DLSS5] section")
        for key in ("NRResolutionScale", "NRPasses", "NRStyle", "NeuralUplift"):
            if key.encode() not in data:
                problems.append(f"{c.id} {v.version}: preset key {key} not found in the add-on")
    if low in ("nvngx_dlssnr.dll", "nvngx_dlss.dll"):
        info = pe.parse(str(path))
        desc = " ".join(info.strings.values()).lower()
        want = "dlssnr" if low == "nvngx_dlssnr.dll" else "dlss"
        if desc and want not in desc:
            problems.append(f"{c.id} {v.version}: {rel} does not identify itself as {want}")


def verify(cat: StackCatalog, folder: Path) -> dict:
    problems: list[str] = []
    checked = []
    for c, v in downloadable(cat):
        path = folder / v.asset
        if not path.is_file():
            problems.append(f"{v.asset}: not downloaded")
            continue
        if sha256_file(path) != v.sha256:
            problems.append(f"{v.asset}: archive SHA-256 differs from the catalog")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            try:
                files = extract(path, tmp, dict(v.files), v.root)
            except Exception as exc:          # noqa: BLE001 - reported as a contract failure
                problems.append(f"{v.asset}: {exc}")
                continue
            for rel, p in files.items():
                check_member(c, v, rel, p, problems)
            if any(re.search(r"\.(bat|ps1|py|cmd|exe)$", rel, re.I) and not rel.lower().endswith("host64.exe")
                   for rel in files):
                problems.append(f"{v.asset}: a script would be installed")
        checked.append(f"{c.id} {v.version}")
    return {"checked": checked, "problems": problems}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--download", type=Path, help="fetch every pinned original asset into this folder")
    ap.add_argument("--archive-dir", type=Path, help="verify the assets in this folder")
    args = ap.parse_args()
    cat = StackCatalog.load()
    report = {"components": len(cat.components), "recipes": len(cat.recipes), "games": len(cat.games),
              "versions": sum(1 for _ in downloadable(cat))}
    fetch_problems: list[str] = []
    if args.download:
        report["download"], fetch_problems = download(cat, args.download)
    if args.archive_dir or args.download:
        report.update(verify(cat, args.archive_dir or args.download))
        report["problems"] = fetch_problems + report["problems"]
    print(json.dumps(report, indent=1))
    return 1 if report.get("problems") else 0


if __name__ == "__main__":
    raise SystemExit(main())
