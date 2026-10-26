"""Disk Space Analyzer: what fills a drive or folder, the largest files, game installations, Downloads and
(optionally) duplicate files. Read-only; every result can be opened in Explorer.

Duplicates are found by size first, then by the SHA-256 of the first 64 KiB, then by the SHA-256 of the whole
file, so only byte-identical files are reported.
"""

from __future__ import annotations

import hashlib
import heapq
import os
from dataclasses import dataclass, field
from typing import Any

from akeno.i18n import msg
from akeno.system import fs
from akeno.system.tasks import LimitReached, TaskContext, context

TOP_FILES = 50
DUP_MIN = 1 << 20
DUP_HEAD = 64 * 1024


@dataclass
class Child:
    name: str
    path: str
    bytes: int = 0
    files: int = 0
    is_dir: bool = True


@dataclass
class FolderReport:
    root: str
    total: int = 0
    files: int = 0
    children: list[Child] = field(default_factory=list)
    largest: list[tuple[int, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    limited: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"root": self.root, "total": self.total, "files": self.files, "limited": self.limited,
                "children": [c.__dict__ for c in self.children], "largest": self.largest,
                "errors": self.errors[:50]}


def folder_sizes(root: str, ctx: TaskContext | None = None, max_files: int = 3_000_000) -> FolderReport:
    """Sizes of ``root``'s direct children (folders summed recursively) and its largest files, in one pass."""
    ctx = context(ctx)
    rep = FolderReport(root)
    children: dict[str, Child] = {}
    heap: list[tuple[int, str]] = []
    base = os.path.normcase(os.path.abspath(root)).rstrip("\\/")
    try:
        for e in fs.walk(root, ctx, max_files=max_files, errors=rep.errors):
            rep.total += e.size
            rep.files += 1
            rel = os.path.normcase(os.path.abspath(e.path))[len(base):].lstrip("\\/")
            first = rel.split(os.sep, 1)[0] if rel else ""
            is_dir = os.sep in rel
            key = first
            c = children.get(key)
            if c is None:
                name = os.path.abspath(e.path)[len(base):].lstrip("\\/").split(os.sep, 1)[0]
                c = children[key] = Child(name, os.path.join(root, name), 0, 0, is_dir)
            c.bytes += e.size
            c.files += 1
            heapq.heappush(heap, (e.size, e.path))
            if len(heap) > TOP_FILES:
                heapq.heappop(heap)
            if rep.files % 2000 == 0:
                ctx.report(msg("{n} files measured", n=rep.files))
    except LimitReached as exc:
        rep.limited = True
        rep.errors.append(str(exc))
    rep.children = sorted(children.values(), key=lambda c: -c.bytes)
    rep.largest = sorted(heap, reverse=True)
    return rep


def game_sizes(games: list, ctx: TaskContext | None = None) -> list[dict[str, Any]]:
    """Size of each library game's install folder (games sharing a folder are counted once)."""
    ctx = context(ctx)
    out, seen = [], set()
    for i, g in enumerate(games):
        folder = getattr(g, "install_dir", None)
        if not folder or not os.path.isdir(folder) or os.path.normcase(folder) in seen:
            continue
        seen.add(os.path.normcase(folder))
        ctx.report(msg("Measuring {name}", name=g.name), i, len(games))
        size, files = fs.dir_size(folder, ctx)
        out.append({"name": g.name, "path": folder, "bytes": size, "files": files,
                    "launcher": getattr(g, "launcher", "")})
    return sorted(out, key=lambda x: -x["bytes"])


def downloads_folder() -> str | None:
    """The user's Downloads folder (its real location when moved, from the shell folders registry key)."""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                value, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
                path = os.path.expandvars(value)
                if os.path.isdir(path):
                    return path
        except OSError:
            pass
    path = os.path.join(os.path.expanduser("~"), "Downloads")
    return path if os.path.isdir(path) else None


@dataclass
class DuplicateGroup:
    size: int
    sha256: str
    paths: list[str]

    @property
    def wasted(self) -> int:
        return self.size * (len(self.paths) - 1)


def duplicates(root: str, ctx: TaskContext | None = None, min_size: int = DUP_MIN,
               max_files: int = 500_000) -> tuple[list[DuplicateGroup], list[str]]:
    """Byte-identical files of at least ``min_size`` below ``root``."""
    ctx = context(ctx)
    errors: list[str] = []
    by_size: dict[int, list[str]] = {}
    try:
        for e in fs.walk(root, ctx, max_files=max_files, errors=errors):
            if e.size >= min_size:
                by_size.setdefault(e.size, []).append(e.path)
    except LimitReached as exc:
        errors.append(str(exc))
    candidates = [(s, ps) for s, ps in by_size.items() if len(ps) > 1]
    groups: list[DuplicateGroup] = []
    total = sum(len(ps) for _, ps in candidates)
    done = 0
    for size, paths in candidates:
        by_head: dict[str, list[str]] = {}
        for p in paths:
            ctx.check()
            done += 1
            ctx.report(msg("Comparing files"), done, total)
            h = _hash(p, DUP_HEAD, ctx)
            if h:
                by_head.setdefault(h, []).append(p)
        for same in by_head.values():
            if len(same) < 2:
                continue
            by_full: dict[str, list[str]] = {}
            for p in same:
                h = _hash(p, None, ctx)
                if h:
                    by_full.setdefault(h, []).append(p)
            for sha, ps in by_full.items():
                if len(ps) > 1:
                    groups.append(DuplicateGroup(size, sha, sorted(ps)))
    return sorted(groups, key=lambda g: -g.wasted), errors


def _hash(path: str, limit: int | None, ctx: TaskContext) -> str | None:
    h = hashlib.sha256()
    read = 0
    try:
        with open(path, "rb") as f:
            while True:
                ctx.check()
                chunk = f.read(1 << 20 if limit is None else min(1 << 20, limit - read))
                if not chunk:
                    break
                h.update(chunk)
                read += len(chunk)
                if limit is not None and read >= limit:
                    break
    except OSError:
        return None
    return h.hexdigest()
