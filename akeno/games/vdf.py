"""Parser for Valve's text KeyValues format (libraryfolders.vdf, appmanifest_*.acf)."""

from __future__ import annotations

from typing import Any


class VdfError(ValueError):
    pass


def _tokens(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
        elif c in "{}":
            yield c
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    nxt = text[j + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(nxt, "\\" + nxt))
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            if j >= n:
                raise VdfError("unterminated string")
            yield ("s", "".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n{}"':
                j += 1
            yield ("s", text[i:j])
            i = j


def loads(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack = [root]
    key: str | None = None
    for tok in _tokens(text):
        if tok == "{":
            if key is None:
                raise VdfError("block without key")
            child: dict[str, Any] = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif tok == "}":
            if len(stack) == 1:
                raise VdfError("unbalanced }")
            stack.pop()
            key = None
        else:
            value = tok[1]
            if key is None:
                key = value
            else:
                stack[-1][key] = value
                key = None
    if len(stack) != 1:
        raise VdfError("unbalanced {")
    return root


def get_ci(d: dict[str, Any], key: str, default: Any = None) -> Any:
    """Case-insensitive key lookup (Steam is inconsistent about key case)."""
    if key in d:
        return d[key]
    low = key.lower()
    for k, v in d.items():
        if k.lower() == low:
            return v
    return default
