"""German texts of the websites. English source strings are the keys; a missing entry falls back to English."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache
def _german() -> dict[str, str]:
    return json.loads((Path(__file__).parent / "de.json").read_text(encoding="utf-8"))


def translate(text: str, lang: str) -> str:
    return _german().get(text, text) if lang == "de" else text
