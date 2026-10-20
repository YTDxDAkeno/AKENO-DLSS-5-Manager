"""Merge German translations into akeno/data/i18n_de.json (keys stay sorted, placeholders are checked).

    python tools/i18n_merge.py new_strings.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "akeno" / "data" / "i18n_de.json"


def merge(new: dict[str, str]) -> int:
    de = json.loads(TARGET.read_text(encoding="utf-8"))
    for en, g in new.items():
        if set(re.findall(r"\{\w+\}", en)) != set(re.findall(r"\{\w+\}", g)):
            raise SystemExit(f"placeholder mismatch: {en!r} -> {g!r}")
        de[en] = g
    TARGET.write_text(json.dumps(dict(sorted(de.items())), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(new)


if __name__ == "__main__":
    print(merge(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))), "translations merged")
