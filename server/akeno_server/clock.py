"""The server's clock. Every license time comes from here (UTC), so tests can move time deterministically."""

from __future__ import annotations

from datetime import datetime, timezone

_offset_seconds = 0.0


def utcnow() -> datetime:
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    return now + timedelta(seconds=_offset_seconds) if _offset_seconds else now


def advance(seconds: float) -> None:
    """Tests only: move the server clock forward (or back)."""
    global _offset_seconds
    _offset_seconds += seconds


def reset() -> None:
    global _offset_seconds
    _offset_seconds = 0.0
