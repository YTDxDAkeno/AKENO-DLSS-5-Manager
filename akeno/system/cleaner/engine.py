"""Smart Cleaner's scan, preview and clean-up.

1. **Scan** (read-only): every category's roots are walked without following links; a file is listed when it is
   old enough, matches the category and passes :func:`rules.path_allowed`. Each file keeps its identity (size,
   modification time, file id).
2. **Preview**: what would be removed per category, how (Recycle Bin or permanently) and what cannot be undone.
3. **Clean** (only what the user confirmed): each file is checked again right before it is removed: still inside
   the confirmed root, unchanged since the scan, not a link, not open in another program. Anything that fails a
   check is skipped and reported, never forced. Files go to the Recycle Bin unless the user chose permanent
   deletion. The journal records each category before it runs.
"""

from __future__ import annotations

import heapq
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from akeno.i18n import msg
from akeno.system import fs
from akeno.system.cleaner.rules import DAY, Category, path_allowed
from akeno.system.tasks import Cancelled, LimitReached, TaskContext, TimedOut, context

SAMPLE = 40               # paths shown per category in the preview
LARGE_KEEP = 200          # largest files kept for the review list


@dataclass
class FileItem:
    path: str
    size: int
    mtime: float
    identity: tuple | None


@dataclass
class CategoryResult:
    category: Category
    files: list[FileItem] = field(default_factory=list)
    bytes: int = 0
    young: int = 0                    # files skipped because they are too recent
    errors: list[str] = field(default_factory=list)
    status: str = "ok"                # ok | empty | not_found | app_running | limited
    running: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.files)

    def summary(self) -> dict[str, Any]:
        c = self.category
        return {"id": c.id, "title": c.title, "count": self.count, "bytes": self.bytes, "status": self.status,
                "running": self.running, "roots": list(c.roots), "errors": self.errors[:20], "young": self.young}


@dataclass
class ScanResult:
    categories: list[CategoryResult]
    created_at: float = field(default_factory=time.time)
    duration_s: float = 0.0
    free_before: dict[str, int] = field(default_factory=dict)

    def get(self, cat_id: str) -> CategoryResult | None:
        return next((c for c in self.categories if c.category.id == cat_id), None)

    @property
    def cleanable_bytes(self) -> int:
        return sum(c.bytes for c in self.categories if not c.category.review_only)


def running_names() -> set[str]:
    import psutil
    out = set()
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").lower()
        if n:
            out.add(n)
    return out


def scan(cats: Iterable[Category], ctx: TaskContext | None = None, now: float | None = None,
         running: set[str] | None = None, env: Mapping[str, str] | None = None) -> ScanResult:
    ctx = context(ctx)
    t0 = time.monotonic()
    now = time.time() if now is None else now
    running = running_names() if running is None else {r.lower() for r in running}
    cats = list(cats)
    seen: set[str] = set()
    results = []
    for i, cat in enumerate(cats):
        ctx.check()
        ctx.report(msg("Scanning {what}", what=msg(cat.title)), i, len(cats))
        res = CategoryResult(cat)
        res.running = [p for p in cat.processes if p.lower() in running]
        existing = [r for r in cat.roots if os.path.isdir(r)]
        if not existing:
            res.status = "not_found"
            results.append(res)
            continue
        large: list[tuple[int, str, FileItem]] = []
        try:
            for root in existing:
                skip = (lambda name, path, keep=cat.keep_dirs: name in keep) if cat.keep_dirs else None
                for e in fs.walk(root, ctx, max_files=cat.max_files, max_depth=cat.max_depth, skip_dir=skip,
                                 errors=res.errors):
                    if not cat.matches(e.name) or e.size < cat.min_size:
                        continue
                    if cat.min_age_days and now - e.mtime < cat.min_age_days * DAY:
                        res.young += 1
                        continue
                    key = os.path.normcase(e.path)
                    if key in seen or not path_allowed(e.path, cat.roots, env):
                        continue
                    seen.add(key)
                    item = FileItem(e.path, e.size, e.mtime, fs.identity(e.path))
                    if cat.review_only:
                        heapq.heappush(large, (e.size, e.path, item))
                        if len(large) > LARGE_KEEP:
                            heapq.heappop(large)
                        continue
                    res.files.append(item)
                    res.bytes += e.size
        except LimitReached as exc:
            res.status = "limited"
            res.errors.append(str(exc))
        if cat.review_only:
            res.files = [x for _, _, x in sorted(large, key=lambda t: -t[0])]
            res.bytes = sum(f.size for f in res.files)
        if res.status == "ok":
            res.status = "app_running" if res.running and res.files else ("empty" if not res.files else "ok")
        results.append(res)
    out = ScanResult(results, duration_s=round(time.monotonic() - t0, 1))
    out.free_before = free_space({r for c in results for r in c.category.roots if os.path.isdir(r)})
    ctx.report(msg("Scan finished"), len(cats), len(cats))
    return out


def free_space(paths: Iterable[str]) -> dict[str, int]:
    out = {}
    for p in paths:
        root = fs.drive_root(p)
        if root in out:
            continue
        try:
            import shutil
            out[root] = shutil.disk_usage(root).free
        except OSError:
            continue
    return out


# -- preview ----------------------------------------------------------------------------------------------------
@dataclass
class Preview:
    rows: list[dict[str, Any]]
    count: int
    bytes: int
    permanent: bool
    needs_shader_confirmation: bool
    blocked: list[dict[str, Any]]


def preview(result: ScanResult, selected: Iterable[str], permanent: bool = False) -> Preview:
    rows, blocked = [], []
    count = size = 0
    shader = False
    for cid in selected:
        c = result.get(cid)
        if c is None or not c.files:
            continue
        cat = c.category
        if cat.review_only:
            blocked.append({"id": cid, "title": cat.title, "why": msg("Large files are only listed for your review.")})
            continue
        if c.running:
            blocked.append({"id": cid, "title": cat.title,
                            "why": msg("Close {apps} first.", apps=", ".join(c.running))})
            continue
        shader = shader or cat.shader
        rows.append({"id": cid, "title": cat.title, "count": c.count, "bytes": c.bytes, "risk": cat.risk,
                     "side_effect": cat.side_effect, "reason": cat.reason,
                     "sample": [f.path for f in c.files[:SAMPLE]], "more": max(0, c.count - SAMPLE)})
        count += c.count
        size += c.bytes
    return Preview(rows, count, size, permanent, shader, blocked)


# -- clean -------------------------------------------------------------------------------------------------------
@dataclass
class CleanReport:
    removed: int = 0
    removed_bytes: int = 0
    skipped: dict[str, int] = field(default_factory=dict)       # reason -> count
    per_category: dict[str, dict[str, int]] = field(default_factory=dict)
    examples: dict[str, list[str]] = field(default_factory=dict)  # reason -> a few paths
    free_before: dict[str, int] = field(default_factory=dict)
    free_after: dict[str, int] = field(default_factory=dict)
    permanent: bool = False
    cancelled: bool = False

    def skip(self, reason: str, path: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1
        ex = self.examples.setdefault(reason, [])
        if len(ex) < 5:
            ex.append(path)

    def to_dict(self) -> dict[str, Any]:
        return {"removed": self.removed, "removed_bytes": self.removed_bytes, "skipped": self.skipped,
                "per_category": self.per_category, "examples": self.examples, "free_before": self.free_before,
                "free_after": self.free_after, "permanent": self.permanent, "cancelled": self.cancelled}


SKIP_REASONS = {"changed": "changed since the scan", "in_use": "in use by another program",
                "outside": "outside the confirmed folders", "link": "a link or junction",
                "denied": "access denied", "gone": "already gone", "no_bin": "the drive has no Recycle Bin",
                "error": "could not be removed"}


def clean(result: ScanResult, selected: Iterable[str], ctx: TaskContext | None = None, store=None,
          remover=None, permanent: bool = False, confirm_shaders: bool = False,
          running: set[str] | None = None, env: Mapping[str, str] | None = None) -> CleanReport:
    """Remove the confirmed categories' files, each re-checked first. ``remover`` defaults to the Recycle Bin
    (``permanent`` uses plain deletion and is only passed when the user chose it)."""
    ctx = context(ctx)
    remover = remover or (fs.PermanentDelete() if permanent else fs.RecycleBin())
    running = running_names() if running is None else {r.lower() for r in running}
    report = CleanReport(permanent=permanent)
    chosen = [result.get(cid) for cid in selected]
    chosen = [c for c in chosen if c is not None and c.files and not c.category.review_only]
    roots = {r for c in chosen for r in c.category.roots if os.path.isdir(r)}
    report.free_before = free_space(roots)
    total = sum(c.count for c in chosen)
    done = 0
    try:
        for c in chosen:
            cat = c.category
            stats = report.per_category.setdefault(cat.id, {"removed": 0, "bytes": 0, "skipped": 0})
            if cat.shader and not confirm_shaders:
                stats["skipped"] += c.count
                report.skipped["shader_not_confirmed"] = report.skipped.get("shader_not_confirmed", 0) + c.count
                continue
            busy = [p for p in cat.processes if p.lower() in running]
            if busy:
                stats["skipped"] += c.count
                report.skipped["app_running"] = report.skipped.get("app_running", 0) + c.count
                continue
            aid = store.begin("cleaner", "clean." + cat.id, cat.id,
                              f"{c.count} files, {c.bytes} bytes", {"permanent": permanent}) if store else None
            for item in c.files:
                ctx.check()
                done += 1
                if done % 50 == 0:
                    ctx.report(msg("Cleaning {what}", what=msg(cat.title)), done, total)
                reason = _remove_one(item, cat, remover, env)
                if reason is None:
                    stats["removed"] += 1
                    stats["bytes"] += item.size
                    report.removed += 1
                    report.removed_bytes += item.size
                else:
                    stats["skipped"] += 1
                    report.skip(reason, item.path)
            if aid:
                store.finish(aid, True, f"removed {stats['removed']} ({stats['bytes']} bytes), "
                                        f"skipped {stats['skipped']}")
    except (Cancelled, TimedOut):
        report.cancelled = True
    report.free_after = free_space(roots)
    if store:
        store.add_history("cleaner.clean", report.to_dict() | {"categories": [c.category.id for c in chosen]})
    ctx.report(msg("Clean-up finished"), total, total)
    return report


def _remove_one(item: FileItem, cat: Category, remover, env) -> str | None:
    path = item.path
    if not path_allowed(path, cat.roots, env):
        return "outside"
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "gone"
    except OSError:
        return "denied"
    if fs.is_reparse(path, st):
        return "link"
    if fs.identity(path) != item.identity:
        return "changed"
    if fs.is_locked(path):
        return "in_use"
    if not remover.supports(path):
        return "no_bin"
    try:
        remover.remove(path)
    except FileNotFoundError:
        return "gone"
    except PermissionError:
        return "denied"
    except OSError:
        return "in_use" if fs.is_locked(path) else "error"
    return None
