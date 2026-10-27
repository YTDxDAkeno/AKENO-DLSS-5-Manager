"""Write an optional-component inventory into the PyInstaller output, before signing the portable ZIP.

Usage: python tools/build_components.py dist/AKENO
Never changes source files. Source-only collection keeps Security Center out of PYZ/Core.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from akeno import __version__
from akeno.components.service import INVENTORY, SECURITY_FILES
from akeno.updater.staging import no_links_on_the_way, sha256_of, write_atomic


def build(root: Path) -> dict:
    files = {}
    for name in SECURITY_FILES:
        rel = "_internal/" + name
        path = root / rel
        no_links_on_the_way(path, root)
        if not path.is_file():
            raise ValueError(f"Optional component is not collected separately: {rel}")
        files[rel] = {"sha256": sha256_of(path), "size": path.stat().st_size}
    data = {"schema": 1, "version": __version__, "security_center": {"files": files}}
    write_atomic(root / INVENTORY, json.dumps(data, sort_keys=True, indent=2).encode("utf-8"))
    return data


if __name__ == "__main__":
    build(Path(sys.argv[1]))
