"""File-system safety shared by Smart Cleaner, Disk Space Analyzer and the Universal Mod Scanner.

* :func:`walk` never follows a symbolic link, junction or other reparse point, stays below its root, honours
  cancellation and stops at its file and depth limits.
* :func:`within` decides containment on the resolved path, case-insensitively on Windows, so ``..``, a link or a
  junction cannot move a path out of the folder the user confirmed.
* :func:`identity` is what a scan remembers about a file (size, modification time, file id); a file that changed
  between the scan and the clean-up is skipped.
* :func:`is_locked` asks Windows whether another program holds the file open; such a file is skipped, never forced.
* :class:`RecycleBin` moves files to the Windows Recycle Bin (``SHFileOperationW`` with ``FOF_ALLOWUNDO``), one at
  a time, so each outcome is known.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from akeno.paths import is_windows
from akeno.system.tasks import LimitReached, TaskContext, context

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _norm(path: str) -> str:
    p = os.path.normpath(os.path.abspath(path))
    return os.path.normcase(p)


def resolved(path: str | os.PathLike) -> str:
    """The real path (links and junctions resolved), normalised for comparison."""
    try:
        return _norm(os.path.realpath(path))
    except (OSError, ValueError):
        return _norm(str(path))


def within(root: str | os.PathLike, path: str | os.PathLike) -> bool:
    """True when ``path`` really is inside ``root`` (or is ``root``) after resolving links and ``..``."""
    r, p = resolved(root), resolved(path)
    if p == r:
        return True
    return p.startswith(r.rstrip("\\/") + os.sep)


def is_reparse(path: str | os.PathLike, st: os.stat_result | None = None) -> bool:
    """A symbolic link, junction, mount point or other reparse point (never followed or deleted through)."""
    try:
        st = st or os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0) or 0
    if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    if not is_windows():
        return False
    try:                                                # a junction reports as a directory to lstat on old Pythons
        return bool(os.path.isjunction(path)) if hasattr(os.path, "isjunction") else False
    except OSError:
        return False


@dataclass
class Entry:
    path: str
    size: int
    mtime: float
    is_dir: bool
    file_id: int = 0

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


def walk(root: str | os.PathLike, ctx: TaskContext | None = None, max_files: int = 500_000, max_depth: int = 64,
         skip_dir: Callable[[str, str], bool] | None = None, include_dirs: bool = False,
         errors: list[str] | None = None) -> Iterator[Entry]:
    """Every regular file below ``root`` (no links, junctions or reparse points, which are reported in ``errors``
    and skipped). ``skip_dir(name, path)`` prunes folders. Raises :class:`LimitReached` after ``max_files``."""
    ctx = context(ctx)
    root = str(root)
    try:
        st = os.lstat(root)
    except OSError as exc:
        if errors is not None:
            errors.append(f"{root}: {exc.strerror or exc}")
        return
    if is_reparse(root, st) or not stat.S_ISDIR(st.st_mode):
        if errors is not None and is_reparse(root, st):
            errors.append(f"{root}: link or junction, not followed")
        return
    stack: list[tuple[str, int]] = [(root, 0)]
    count = 0
    while stack:
        ctx.check()
        folder, depth = stack.pop()
        try:
            it = os.scandir(folder)
        except OSError as exc:
            if errors is not None:
                errors.append(f"{folder}: {exc.strerror or exc}")
            continue
        with it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError as exc:
                    if errors is not None:
                        errors.append(f"{e.path}: {exc.strerror or exc}")
                    continue
                if is_reparse(e.path, st):
                    if errors is not None:
                        errors.append(f"{e.path}: link or junction, not followed")
                    continue
                if stat.S_ISDIR(st.st_mode):
                    if skip_dir is not None and skip_dir(e.name, e.path):
                        continue
                    if depth + 1 <= max_depth:
                        stack.append((e.path, depth + 1))
                    if include_dirs:
                        yield Entry(e.path, 0, st.st_mtime, True, getattr(st, "st_ino", 0))
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                count += 1
                if count > max_files:
                    raise LimitReached(f"More than {max_files} files; the scan stopped there.")
                if count % 500 == 0:
                    ctx.check()
                yield Entry(e.path, st.st_size, st.st_mtime, False, getattr(st, "st_ino", 0))


def identity(path: str | os.PathLike) -> tuple[int, int, int] | None:
    """(size, mtime in ns, file id) of a regular file that is not a link; None otherwise."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if is_reparse(path, st) or not stat.S_ISREG(st.st_mode):
        return None
    return st.st_size, st.st_mtime_ns, getattr(st, "st_ino", 0)


def is_locked(path: str | os.PathLike) -> bool:
    """Windows: another process has the file open without delete sharing (it is in use). Elsewhere: False."""
    if not is_windows():
        return False
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    DELETE, OPEN_EXISTING, FLAG_OPEN_REPARSE_POINT = 0x00010000, 3, 0x00200000
    share = 0x1 | 0x2 | 0x4                 # read | write | delete: fails only when someone else denies them
    h = k32.CreateFileW(str(path), DELETE, share, None, OPEN_EXISTING, FLAG_OPEN_REPARSE_POINT, None)
    if h == wintypes.HANDLE(-1).value or h is None:
        return ctypes.get_last_error() in (32, 33)   # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
    k32.CloseHandle(h)
    return False


def drive_root(path: str) -> str:
    drive, _ = os.path.splitdrive(os.path.abspath(path))
    return (drive + "\\") if drive else "/"


def is_fixed_local_drive(path: str) -> bool:
    """Windows: the path is on a fixed local disk (the drives that have a Recycle Bin)."""
    if not is_windows():
        return True
    import ctypes
    root = drive_root(path)
    if root.startswith("\\\\"):
        return False
    return ctypes.windll.kernel32.GetDriveTypeW(root) == 3   # DRIVE_FIXED


class RecycleBin:
    """Moves single files to the Recycle Bin. Elsewhere (development) to AKENO's own trash folder."""

    def __init__(self, fallback_dir: Path | None = None):
        self.fallback_dir = fallback_dir

    @property
    def undoable(self) -> bool:
        return True

    def supports(self, path: str) -> bool:
        return is_fixed_local_drive(path)

    def remove(self, path: str) -> None:
        if not is_windows():
            self._move_to_fallback(path)
            return
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

        FO_DELETE = 3
        # allow undo (Recycle Bin), no confirmation dialogs, no error UI, but warn before anything would be
        # destroyed instead of recycled (a file too large for the bin): that warning overrides "no confirmation"
        flags = 0x0040 | 0x0010 | 0x0400 | 0x0004 | 0x4000      # ALLOWUNDO|NOCONFIRMATION|NOERRORUI|SILENT|WANTNUKEWARNING
        op = SHFILEOPSTRUCTW(None, FO_DELETE, os.path.abspath(path) + "\0", None, flags, False, None, None)
        res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if res != 0 or op.fAnyOperationsAborted:
            raise OSError(f"Windows did not move the file to the Recycle Bin (code {res}).")

    def _move_to_fallback(self, path: str) -> None:
        from akeno.paths import sub
        import shutil
        import time
        dest_root = self.fallback_dir or sub("trash")
        dest_root.mkdir(parents=True, exist_ok=True)
        dest = dest_root / f"{time.time_ns()}-{os.path.basename(path)}"
        shutil.move(path, dest)


class PermanentDelete:
    """Used only for files the user explicitly chose to delete permanently (shown as not undoable)."""

    undoable = False

    def supports(self, path: str) -> bool:
        return True

    def remove(self, path: str) -> None:
        os.unlink(path)


def dir_size(path: str, ctx: TaskContext | None = None, max_files: int = 2_000_000) -> tuple[int, int]:
    """(bytes, files) below ``path``, links and junctions not followed."""
    total = n = 0
    try:
        for e in walk(path, ctx, max_files=max_files):
            total += e.size
            n += 1
    except LimitReached:
        pass
    return total, n
