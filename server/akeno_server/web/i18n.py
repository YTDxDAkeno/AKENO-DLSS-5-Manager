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


# -- coverage (used by tests/test_web_i18n.py) ------------------------------------------------------------------------
MESSAGE_CALLS = {"bad_request": 1, "unauthorized": 1, "forbidden": 1, "not_found": 1, "conflict": 1, "unavailable": 1,
                 "ApiError": 2, "Redirect": 1}


def needed() -> set[str]:
    """Every English text the websites can show: template strings, flash messages and server error messages."""
    import ast
    import re
    root = Path(__file__).resolve().parent.parent
    out: set[str] = set()
    pattern = re.compile(r"""\bt\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')""")
    dict_values = re.compile(r"""t\(\{([^}]*)\}""")
    for path in (Path(__file__).parent / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            out.add(m.group(1) if m.group(1) is not None else m.group(2))
        for m in dict_values.finditer(text):
            out |= set(re.findall(r""":\s*"([^"]+)\"""", m.group(1)))
        # (value, "label") pairs of option lists
        out |= set(re.findall(r"""\("[a-z_0-9]+", "([A-Z][^"]+)"(?:, [^)]*)?\)""", text))
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name in MESSAGE_CALLS and len(node.args) > MESSAGE_CALLS[name]:
                arg = node.args[MESSAGE_CALLS[name]]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.add(arg.value)
            if name == "redirect" and len(node.args) >= 3:
                arg = node.args[2]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.add(arg.value)
                elif isinstance(arg, ast.IfExp):
                    out |= {a.value for a in (arg.body, arg.orelse) if isinstance(a, ast.Constant)}
            if name == "render":
                for kw in node.keywords:
                    if kw.arg in ("title", "text", "error") and isinstance(kw.value, ast.Constant) \
                            and isinstance(kw.value.value, str):
                        out.add(kw.value.value)
                    if kw.arg == "link" and isinstance(kw.value, ast.Tuple):
                        out.add(kw.value.elts[1].value)
    from akeno_server.web.admin import SETTING_LABELS
    out |= set(SETTING_LABELS.values()) | EXTRA
    return {s for s in out if s.strip()}


# shown through helpers the scan above does not follow
EXTRA = {"Account created. We sent you an e-mail to confirm your address.", "Choose a license type.", "expired",
         "day", "days", "unlimited"}
