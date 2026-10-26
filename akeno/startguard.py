"""Start guard: a start that never got AKENO's window up is noticed by the next start.

Before the windows are built, ``startup.json`` in AKENO's data folder records this start (version, process id).
Once the window has been up for ``STABLE_SECONDS`` it is removed. If it is still there at the next start of the same
version and that process is gone, the start failed (a crash, or a hang the user ended). The next start then runs in
safe mode: the SYSTEM tools do not show results saved on this PC (they stay stored, nothing is deleted), and AKENO
offers the update check and the return to the kept previous version (KONTO → Updates).

A crash while starting is shown in a plain Windows message box instead of the bare "Unhandled exception" dialog,
with the traceback written to ``logs\\crash.log``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

STABLE_SECONDS = 20
log = logging.getLogger(__name__)


def marker() -> Path:
    from akeno.paths import app_home
    return app_home() / "startup.json"


def _alive(pid) -> bool:
    try:
        import psutil
        return isinstance(pid, int) and pid != os.getpid() and psutil.pid_exists(pid)
    except Exception:                                   # noqa: BLE001 (no psutil: assume it is gone)
        return False


def begin(version: str) -> int:
    """Record this start; the number of starts of ``version`` before it that never got their window up."""
    path = marker()
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = None
    failed = 0
    if isinstance(prev, dict) and prev.get("version") == version:
        if _alive(prev.get("pid")):                     # another AKENO of this version is starting right now
            failed = int(prev.get("failed") or 0)
        else:
            failed = int(prev.get("failed") or 0) + 1
    try:
        path.write_text(json.dumps({"version": version, "pid": os.getpid(), "started": time.time(),
                                    "failed": failed}), encoding="utf-8")
    except OSError as exc:
        log.warning("start guard: %s", exc)
    return failed


def done() -> None:
    """The window has been up long enough: this start succeeded."""
    try:
        data = json.loads(marker().read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("pid") not in (None, os.getpid()):
            return                                      # another AKENO wrote it since: not ours to clear
        marker().unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def crashed(exc: BaseException) -> int:
    """An exception ended the start: write it to the log and tell the user what happens next."""
    from akeno import __version__
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    where = ""
    try:
        from akeno.paths import logs_dir
        path = logs_dir() / "crash.log"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"\n=== AKENO {__version__} {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n{text}")
        where = str(path)
    except OSError:
        pass
    logging.getLogger("akeno").critical("start failed:\n%s", text)
    try:
        from akeno.i18n import t
        message = t("AKENO could not start: {error}\n\nYour games, mods, backups, settings and license are not "
                    "affected. The next start opens AKENO in safe mode (without the results saved by the System "
                    "tools); there you can look for a corrected version or go back to the previous one under "
                    "ACCOUNT → Updates.\n\nIf AKENO does not start at all, install the current version over it with "
                    "the installer from akeno.online; your data stays.\n\nDetails: {log}",
                    error=f"{type(exc).__name__}: {exc}"[:300], log=where or "—")
    except Exception:                                   # noqa: BLE001 (the message must not fail too)
        message = f"AKENO could not start: {type(exc).__name__}: {exc}"
    if os.environ.get("AKENO_UPDATE_MARKER"):
        return 1                                        # an update's start check: the updater rolls back, no dialog
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "AKENO Gaming Suite", 0x10)   # MB_ICONERROR
        except (AttributeError, OSError):
            pass
    elif sys.stderr is not None:
        print(message, file=sys.stderr)
    return 1
