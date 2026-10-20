"""Comment-preserving INI editing for OptiScaler-style config files."""

from __future__ import annotations

import re
from typing import Any

_SECTION = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*$")
_KEY = re.compile(r"^(?P<key>[A-Za-z0-9_.]+)\s*=(?P<value>.*)$")


def format_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        s = f"{v:.4f}".rstrip("0")
        return s + "0" if s.endswith(".") else s
    return str(v)


def set_values(text: str, values: dict[str, dict[str, Any]]) -> str:
    """Set ``values[section][key]`` in ``text``; keys missing from a section are appended to it, and
    missing sections are appended at the end. Comments, order and other keys are preserved."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    out: list[str] = []
    written: set[tuple[str, str]] = set()
    seen_sections: set[str] = set()
    section: str | None = None

    def flush(sec: str | None) -> None:
        if sec is None:
            return
        for k, v in values.get(sec, {}).items():
            if (sec, k) not in written:
                # keep blank lines that close the section after the inserted key
                insert_at = len(out)
                while insert_at > 0 and out[insert_at - 1].strip() == "":
                    insert_at -= 1
                out.insert(insert_at, f"{k}={format_value(v)}")
                written.add((sec, k))

    for line in lines:
        m = _SECTION.match(line)
        if m:
            flush(section)
            section = m.group("name").strip()
            seen_sections.add(section)
            out.append(line)
            continue
        km = _KEY.match(line)
        if km and section in values and km.group("key") in values[section] and (section, km.group("key")) not in written:
            k = km.group("key")
            out.append(f"{k}={format_value(values[section][k])}")
            written.add((section, k))
            continue
        out.append(line)
    flush(section)
    for sec, kv in values.items():
        if sec not in seen_sections:
            out.append("")
            out.append(f"[{sec}]")
            for k, v in kv.items():
                out.append(f"{k}={format_value(v)}")
    return newline.join(out) + newline


def get_values(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    section = None
    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            section = m.group("name").strip()
            result.setdefault(section, {})
            continue
        km = _KEY.match(line)
        if km and section:
            result[section][km.group("key")] = km.group("value").strip()
    return result


def split_params(params: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for full, v in params.items():
        sec, _, key = full.partition(".")
        if key:
            out.setdefault(sec, {})[key] = v
    return out
