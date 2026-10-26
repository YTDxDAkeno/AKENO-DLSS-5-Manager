"""Long operations: cancellation, time limits, resource limits and honest progress.

A :class:`TaskContext` goes into every scan, hash run, network test and virus scan. The operation calls
``ctx.check()`` between steps (raises :class:`Cancelled` or :class:`TimedOut`) and ``ctx.report()`` when it has
something to say. ``done``/``total`` are only given when they are known: a scan of unknown size reports how far it
got, never an invented percentage.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


class Cancelled(Exception):
    """The user cancelled the operation."""


class TimedOut(Exception):
    """The operation ran into its time limit."""


class LimitReached(Exception):
    """A resource limit (files, bytes, entries) was reached; the partial result is still valid."""


@dataclass
class Progress:
    text: str
    done: int | None = None
    total: int | None = None

    @property
    def fraction(self) -> float | None:
        if self.done is None or not self.total:
            return None
        return max(0.0, min(1.0, self.done / self.total))


class TaskContext:
    """Cancellation token, deadline and progress sink for one operation.

    ``token`` may be any object with a ``cancelled`` attribute (``security.downloads.CancelToken`` works too), so
    one Cancel button can stop a download and the scan behind it."""

    def __init__(self, token: Any = None, progress: Callable[[Progress], None] | None = None,
                 timeout: float | None = None, now: Callable[[], float] = time.monotonic):
        self._event = threading.Event()
        self.token = token
        self._progress = progress
        self._now = now
        self.started = now()
        self.deadline = self.started + timeout if timeout else None
        self.last: Progress | None = None

    # -- cancellation --------------------------------------------------------------------------------------------
    def cancel(self) -> None:
        self._event.set()
        if self.token is not None and hasattr(self.token, "cancel"):
            self.token.cancel()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set() or bool(getattr(self.token, "cancelled", False))

    @property
    def timed_out(self) -> bool:
        return self.deadline is not None and self._now() >= self.deadline

    def check(self) -> None:
        if self.cancelled:
            raise Cancelled("Cancelled.")
        if self.timed_out:
            raise TimedOut("The time limit was reached.")

    def remaining(self, cap: float | None = None) -> float | None:
        """Seconds left before the deadline (at most ``cap``); None without a deadline and cap."""
        left = None if self.deadline is None else max(0.0, self.deadline - self._now())
        if cap is None:
            return left
        return cap if left is None else min(cap, left)

    def elapsed(self) -> float:
        return self._now() - self.started

    def sleep(self, seconds: float) -> None:
        """Wait, but wake up at once on cancel; raises like :meth:`check` afterwards."""
        end = self._now() + seconds
        while True:
            self.check()
            left = end - self._now()
            if left <= 0:
                return
            self._event.wait(min(left, 0.1))

    # -- progress ------------------------------------------------------------------------------------------------
    def report(self, text: str, done: int | None = None, total: int | None = None) -> None:
        self.last = Progress(text, done, total)
        if self._progress is not None:
            try:
                self._progress(self.last)
            except RuntimeError:                       # the receiving page was rebuilt meanwhile
                self._progress = None


def context(ctx: TaskContext | None) -> TaskContext:
    """The given context, or one that never cancels (calls from tests and other services)."""
    return ctx if ctx is not None else TaskContext()
