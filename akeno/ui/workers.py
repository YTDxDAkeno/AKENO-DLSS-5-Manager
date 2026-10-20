"""Background task helper: run a callable on the thread pool, deliver results on the UI thread."""

from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

log = logging.getLogger(__name__)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object, str)
    progress = Signal(object)


class Task(QRunnable):
    def __init__(self, fn: Callable[..., Any], with_progress: bool = False):
        super().__init__()
        self.fn = fn
        self.with_progress = with_progress
        self.signals = _Signals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            res = self.fn(self.signals.progress.emit) if self.with_progress else self.fn()
        except Exception as exc:  # delivered to the UI as a readable error
            log.exception("background task failed")
            self.signals.failed.emit(exc, traceback.format_exc())
            return
        self.signals.done.emit(res)


_live: set[Task] = set()


def run(fn: Callable[..., Any], on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        on_progress: Callable[[Any], None] | None = None) -> Task:
    """Run ``fn`` in the background. If ``on_progress`` is given, ``fn`` receives an ``emit(value)`` callback."""
    task = Task(fn, with_progress=on_progress is not None)
    _live.add(task)

    def finish(res):
        _live.discard(task)
        if on_done:
            on_done(res)

    def fail(exc, tb):
        _live.discard(task)
        if on_error:
            on_error(exc)

    task.signals.done.connect(finish)
    task.signals.failed.connect(fail)
    if on_progress:
        task.signals.progress.connect(on_progress)
    QThreadPool.globalInstance().start(task)
    return task


def wait_all(ms: int = 30000) -> bool:
    return QThreadPool.globalInstance().waitForDone(ms)
