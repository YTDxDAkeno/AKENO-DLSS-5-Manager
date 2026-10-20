"""English/German UI strings.

English source strings are the keys; ``akeno/data/i18n_de.json`` maps them to German. Missing
translations fall back to English, and ``tests/test_i18n_coverage.py`` fails the build when a UI string
has no German entry.

Messages built from values in non-UI code (services, backends, installer) use :func:`msg`: the result is an
ordinary ``str`` (English, so logs and tests keep working) that also remembers its template and parameters,
so ``t()`` translates the template first and fills the values in afterwards — no mixed English/German text.
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


class Msg(str):
    """English text that can be re-rendered in the UI language (see module docstring)."""

    template: str
    params: dict

    def __new__(cls, template: str, **params):
        obj = super().__new__(cls, template.format(**params) if params else template)
        obj.template = template
        obj.params = params
        return obj

    def __reduce__(self):
        return (_rebuild_msg, (self.template, self.params))


def _rebuild_msg(template: str, params: dict) -> "Msg":
    return Msg(template, **params)


def msg(template: str, **params) -> Msg:
    return Msg(template, **params)


def t(text: str, **fmt) -> str:
    if isinstance(text, Msg) and not fmt:
        params = {k: (t(v) if isinstance(v, Msg) else v) for k, v in text.params.items()}
        out = _german().get(text.template, text.template) if _lang == "de" else text.template
        return out.format(**params) if params else out
    out = _german().get(text, text) if _lang == "de" else text
    return out.format(**fmt) if fmt else out
