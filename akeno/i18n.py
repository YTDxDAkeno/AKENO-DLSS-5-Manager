"""English/German UI strings.

English source strings are the keys; ``akeno/data/i18n_de.json`` maps them to German. Missing
translations fall back to English, and ``tests/test_i18n_coverage.py`` fails the build when a UI string
has no German entry.
"""

from __future__ import annotations

import json
import locale
from pathlib import Path

from akeno.paths import resource_dir

_lang = "en"
_de: dict[str, str] | None = None


def _german() -> dict[str, str]:
    global _de
    if _de is None:
        try:
            _de = json.loads(Path(resource_dir(), "i18n_de.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _de = {}
    return _de


def resolve(setting: str | None) -> str:
    if setting in ("en", "de"):
        return setting
    try:
        loc = (locale.getlocale()[0] or "") or (locale.getdefaultlocale()[0] or "")  # noqa: deprecated fallback
    except (ValueError, AttributeError):
        loc = ""
    return "de" if loc.lower().startswith(("de", "german")) else "en"


def set_language(setting: str | None) -> str:
    global _lang
    _lang = resolve(setting)
    return _lang


def language() -> str:
    return _lang


def t(text: str, **fmt) -> str:
    out = _german().get(text, text) if _lang == "de" else text
    return out.format(**fmt) if fmt else out
