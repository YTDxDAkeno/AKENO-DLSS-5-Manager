"""Read-only discovery CLI: python -m akeno.fivem [portable installation roots]."""
from __future__ import annotations

import argparse
import json
from .discovery import discover


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*", help="Optional FiveM roots, FiveM.app folders or FiveM.exe paths")
    args = parser.parse_args()
    installations = discover(args.roots)
    print(json.dumps({"schema": 1, "experimental": True, "approved_backends": [],
        "installations": [{"game": i.game.to_dict(), "app_dir": str(i.app_dir),
            "plugins": str(i.plugin_dir), "gta_dir": str(i.gta_dir) if i.gta_dir else None,
            "edition": i.edition.value, "saved_build_preference": i.saved_build,
            "channel": i.update_channel, "fingerprint": i.fingerprint, "errors": list(i.errors),
            "inventory": [{"path": str(f.path), "kind": f.kind, "sha256": f.sha256} for f in i.inventory]}
            for i in installations]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
