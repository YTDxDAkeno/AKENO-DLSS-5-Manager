"""Version comparison for mod metadata: Fabric/Quilt predicates and Forge/NeoForge Maven ranges.

A requirement that cannot be parsed is reported as *unknown*, never as a conflict.

Fabric predicates (``fabric.mod.json``): ``*``, ``1.20.1``, ``=1.20.1``, ``>=1.20``, ``<1.21``, ``~1.20.1`` (same
minor), ``^1.20`` (same major), ``1.20.x``, several separated by spaces (all must hold) or given as a list (any).
Maven ranges (``mods.toml``): ``[1.20,1.21)``, ``[1.20.1]``, ``[47,)``, ``(,1.19]``, unions separated by commas
between ranges, or a bare version meaning "at least".
"""

from __future__ import annotations

import re

_NUM = re.compile(r"\d+")


def parse(v: str) -> tuple | None:
    """"1.20.1" -> ((1, 20, 1), prerelease) for comparison; None when there is no number at all."""
    v = (v or "").strip().lstrip("vV=")
    if not v:
        return None
    main, _, pre = v.partition("-")
    main = main.split("+", 1)[0]
    parts = []
    for p in main.split("."):
        m = _NUM.match(p)
        if not m:
            break
        parts.append(int(m.group(0)))
    if not parts:
        return None
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()                             # 1.20.0 == 1.20
    # a pre-release sorts before the release ("1.21-pre1" < "1.21")
    return tuple(parts), (0, pre) if pre else (1, "")


def _nums(v: str) -> list[int]:
    out = []
    for p in (v or "").strip().lstrip("vV=").split("-", 1)[0].split("+", 1)[0].split("."):
        m = _NUM.match(p)
        if not m:
            break
        out.append(int(m.group(0)))
    return out


def compare(a: str, b: str) -> int | None:
    pa, pb = parse(a), parse(b)
    if pa is None or pb is None:
        return None
    return (pa > pb) - (pa < pb)


def _fabric_single(pred: str, version: str) -> bool | None:
    pred = pred.strip()
    if pred in ("*", ""):
        return True
    for op in (">=", "<=", ">", "<", "=", "~", "^"):
        if pred.startswith(op):
            target = pred[len(op):].strip()
            break
    else:
        op, target = "=", pred
    if target.endswith((".x", ".X", ".*")):
        want, have = _nums(target[:-2]), _nums(version)
        if not want or not have:
            return None
        have = have + [0] * max(0, len(want) - len(have))
        return have[:len(want)] == want
    c = compare(version, target)
    if c is None:
        return None
    if op == "=":
        return c == 0
    if op == ">=":
        return c >= 0
    if op == "<=":
        return c <= 0
    if op == ">":
        return c > 0
    if op == "<":
        return c < 0
    pt, pv = parse(target), parse(version)
    if op == "~":                     # same major.minor, at least target
        return c >= 0 and (pv[0] + (0, 0))[:2] == (pt[0] + (0, 0))[:2]
    if op == "^":                     # same major, at least target
        return c >= 0 and pv[0][0] == pt[0][0]
    return None


def fabric_matches(predicate, version: str) -> bool | None:
    """``predicate`` is a string or a list of strings (any of them). None: cannot tell."""
    if version is None:
        return None
    if isinstance(predicate, list):
        results = [fabric_matches(p, version) for p in predicate]
        if any(r is True for r in results):
            return True
        return None if any(r is None for r in results) else False
    if not isinstance(predicate, str):
        return None
    results = [_fabric_single(p, version) for p in predicate.split()] or [True]
    if any(r is False for r in results):
        return False
    return None if any(r is None for r in results) else True


_RANGE = re.compile(r"[\[(][^\])]*[\])]")


def maven_matches(spec: str, version: str) -> bool | None:
    spec = (spec or "").strip()
    if version is None:
        return None
    if not spec or spec == "*":
        return True
    if spec[0] not in "[(":
        c = compare(version, spec)       # a bare version: "at least" (Forge's soft requirement)
        return None if c is None else c >= 0
    tokens = _RANGE.findall(spec)
    if not tokens:
        return None
    unknown = False
    for tok in tokens:
        lo_b, inner, hi_b = tok[0], tok[1:-1], tok[-1]
        if "," not in inner:             # "[1.20.1]": exactly this version
            c = compare(version, inner)
            if c is None:
                unknown = True
            elif c == 0:
                return True
            continue
        lo, hi = (x.strip() for x in inner.split(",", 1))
        ok = True
        for bound, closed, sign in ((lo, lo_b == "[", 1), (hi, hi_b == "]", -1)):
            if not bound:
                continue
            c = compare(version, bound)
            if c is None:
                unknown, ok = True, False
                break
            ok = ok and (c * sign > 0 or (c == 0 and closed))
        if ok:
            return True
    return None if unknown else False


def matches(dep_versions, version: str, notation: str) -> bool | None:
    if notation == "maven":
        return maven_matches(dep_versions if isinstance(dep_versions, str) else "", version)
    return fabric_matches(dep_versions, version)
