"""Which playlist entry shows now: one wallpaper, a rotation every few minutes, or a schedule by time of day,
optionally a different entry per Windows virtual desktop."""

from __future__ import annotations

from datetime import datetime


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def current_index(cfg: dict, now: datetime | None = None, virtual_desktop: str | None = None,
                  started: datetime | None = None) -> int | None:
    items = cfg.get("playlist") or []
    if not items:
        return None
    if virtual_desktop and virtual_desktop in (cfg.get("per_virtual_desktop") or {}):
        index = int(cfg["per_virtual_desktop"][virtual_desktop])
        return index if 0 <= index < len(items) else 0
    now = now or datetime.now()
    mode = cfg.get("mode", "single")
    if mode == "schedule" and cfg.get("schedule"):
        today = now.hour * 60 + now.minute
        entries = sorted(cfg["schedule"], key=lambda e: _minutes(e["from"]))
        chosen = entries[-1]                       # before the first entry of the day: the last one of yesterday
        for entry in entries:
            if _minutes(entry["from"]) <= today:
                chosen = entry
        index = int(chosen.get("index", 0))
        return index if 0 <= index < len(items) else 0
    if mode == "rotate" and len(items) > 1:
        start = started or now.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed = max(0.0, (now - start).total_seconds() / 60)
        cycle = sum(max(1, int(i.get("minutes", 10))) for i in items)
        position = elapsed % cycle
        for index, item in enumerate(items):
            position -= max(1, int(item.get("minutes", 10)))
            if position < 0:
                return index
    return 0


def seconds_until_change(cfg: dict, now: datetime | None = None, started: datetime | None = None) -> float | None:
    """When the shown entry changes next (None: never, for a single wallpaper)."""
    now = now or datetime.now()
    mode = cfg.get("mode", "single")
    if mode == "single" or len(cfg.get("playlist") or []) < 2:
        return None
    if mode == "schedule":
        today = now.hour * 60 + now.minute
        later = [_minutes(e["from"]) for e in cfg.get("schedule", []) if _minutes(e["from"]) > today]
        nxt = min(later) if later else min(_minutes(e["from"]) for e in cfg["schedule"]) + 24 * 60
        return (nxt - today) * 60 - now.second
    return 60.0                                 # rotation: re-checked every minute
