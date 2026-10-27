"""The updater process: ``AKENO.exe --apply-update <plan.json>`` and ``AKENO.exe --recover-update <journal.json>``.

It runs from the new version's staged folder (``%LOCALAPPDATA%\\AKENO\\updates\\staging``), never from the installed
program folder, so every AKENO process can end and the program folder can be replaced. Windows does not allow a
folder to be renamed while a program inside it runs, and virus scanners briefly hold new files open, so:

1. wait until the AKENO that started the update has ended; then end AKENO's other processes that run from the
   program folder (Desktop Studio's runtime and wallpaper renderers, a second window): first politely, then firmly;
2. check the staged files against the content list made while unpacking the verified package (nothing changed or
   added since), and that the program folder's drive has room for one more copy;
3. copy exactly the listed files next to the program folder (``.<name>.new``) and check the copy again;
4. switch with two renames on the same drive: program folder → ``.<name>.old``, ``.<name>.new`` → program folder.
   A portable data folder inside the program folder (``AKENO_HOME`` there) moves over;
5. health check: start the new version and wait for its report. It writes the report only once its window is up
   and its own checks pass (right version, program data, release and license keys, the settings database, the
   license state readable), and it must still be running a few seconds later. A process that merely starts is
   not enough;
6. only then is the previous version moved to ``updates\\previous`` (kept for "Roll back"). On any failure the
   previous version is put back and started.

Every step is first written to a journal (``updates\\journal.json``, atomically). If the PC loses power or restarts
in the middle, the next sign-in (a one-time ``RunOnce`` entry) or the next start of AKENO finishes the job from the
journal: before the switch nothing has changed; after it, the previous version comes back; a confirmed update is
completed. Each of these steps can run again without harm. User data in ``%LOCALAPPDATA%\\AKENO`` (licenses,
settings, games, backups, wallpapers, themes) is never inside the program folder and never touched.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from akeno.updater import staging

log = logging.getLogger(__name__)
KEEP_ALWAYS = ("portable.ini", "AKENO.ini", "akeno-components.ini")
# the installer's uninstaller lives in the program folder; the portable package does not carry it
KEEP_PATTERNS = ("unins*.exe", "unins*.dat", "unins*.msg")
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
UNFINISHED = ("prepared", "stopping", "copying", "swapping", "swapped", "health", "committing", "rolling_back")
FINAL = ("committed", "rolled_back", "failed")
RUNONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
RUNONCE_VALUE = "AKENO Update Recovery"
SETTLE_SECONDS = 5.0                         # the new version must still run this long after its report


# -- small helpers ---------------------------------------------------------------------------------------------------
def retry(action, what: str, timeout: float = 60.0):
    """Run ``action``; a file Windows or a virus scanner still holds open is tried again for up to ``timeout``."""
    end = time.monotonic() + timeout
    delay = 0.2
    while True:
        try:
            return action()
        except FileNotFoundError:
            raise
        except PermissionError as exc:
            last = exc
        except OSError as exc:
            if getattr(exc, "winerror", None) not in (5, 32, 33, 145, 1224):
                raise
            last = exc
        if time.monotonic() >= end:
            raise OSError(f"{what}: {last}") from last
        log.info("%s: %s; trying again", what, last)
        time.sleep(delay)
        delay = min(2.0, delay * 1.6)


def remove(path: Path) -> None:
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        retry(lambda: shutil.rmtree(path), f"remove {path}")
    else:
        retry(lambda: path.unlink(), f"remove {path}")


def move(src: Path, dst: Path) -> None:
    """A rename where possible; across drives a copy that is complete before the original goes."""
    try:
        retry(lambda: os.replace(src, dst), f"move {src}")
    except OSError as exc:
        if getattr(exc, "winerror", None) != 17 and getattr(exc, "errno", None) != 18:   # not another drive
            raise
        tmp = dst.with_name(dst.name + ".partial")
        remove(tmp)
        shutil.copytree(src, tmp) if src.is_dir() else shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        remove(src)


def names(target: Path) -> dict[str, Path]:
    target = Path(target)
    return {k: target.with_name(f".{target.name}.{k}") for k in ("new", "old", "failed")}


class Journal:
    """The update's progress on disk, written before each step (atomic, survives a power cut)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def write(self, **values) -> dict:
        data = self.load()
        data.update(values)
        if "phase" in values:
            data.setdefault("history", []).append([values["phase"], round(time.time(), 3)])
        staging.write_atomic(self.path, json.dumps(data, indent=1).encode("utf-8"))
        return data


@contextlib.contextmanager
def lock(path: Path):
    """One updater at a time. A lock left by a process that no longer runs is taken over."""
    import psutil
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if holder_alive(path):
                raise OSError("another AKENO update is running") from None
            path.unlink(missing_ok=True)
    else:
        raise OSError("another AKENO update is running")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"pid": os.getpid(), "created": psutil.Process().create_time()}))
        yield
    finally:
        path.unlink(missing_ok=True)


def holder_alive(path: Path) -> bool:
    import psutil
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        proc = psutil.Process(int(data["pid"]))
        return abs(proc.create_time() - float(data["created"])) < 2
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        return False


def wait_for_exit(pid: int | None, timeout: float = 120) -> bool:
    if not pid:
        return True
    import psutil
    try:
        psutil.Process(pid).wait(timeout)
    except psutil.NoSuchProcess:
        return True
    except psutil.TimeoutExpired:
        return False
    return True


def processes_in(folder: Path, exclude: set[int] = frozenset()) -> list:
    """AKENO's processes (any program, really) whose executable lies in ``folder``."""
    import psutil
    folder = Path(folder).resolve()
    out = []
    for proc in psutil.process_iter(["pid", "exe"]):
        try:
            exe = proc.info.get("exe")
            if proc.pid in exclude or proc.pid == os.getpid() or not exe:
                continue
            if Path(exe).resolve().is_relative_to(folder):
                out.append(proc)
        except (psutil.Error, OSError, ValueError):
            continue
    return out


def stop_processes(folder: Path, grace: float = 15.0, exclude: set[int] = frozenset()) -> list[str]:
    """End every process running from ``folder``: they get ``grace`` seconds to end by themselves (the suite asked
    the desktop runtime to quit), then they are terminated. Returns what could not be ended."""
    import psutil
    end = time.monotonic() + grace
    procs = processes_in(folder, exclude)
    while procs and time.monotonic() < end:
        time.sleep(0.5)
        procs = [p for p in procs if p.is_running()]
    for p in procs:
        log.info("ending %s (pid %s) that still runs from %s", p.info.get("exe"), p.pid, folder)
        with contextlib.suppress(psutil.Error):
            p.terminate()
    _, alive = psutil.wait_procs(procs, timeout=10)
    for p in alive:
        with contextlib.suppress(psutil.Error):
            p.kill()
    _, alive = psutil.wait_procs(alive, timeout=5)
    return [f"{p.info.get('exe')} (pid {p.pid})" for p in alive]


def free_bytes(path: Path) -> int:
    path = Path(path)
    while not path.exists() and path.parent != path:
        path = path.parent
    return shutil.disk_usage(path).free


def update_uninstall_entry(target: Path, version: str) -> None:
    """The installed version Windows shows in "Installed apps" (the installer's entry for this folder)."""
    if sys.platform != "win32":
        return
    import winreg
    want = os.path.normcase(os.path.normpath(str(target)))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                name = winreg.EnumKey(root, i)
                with winreg.OpenKey(root, name, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
                    try:
                        location, _ = winreg.QueryValueEx(key, "InstallLocation")
                    except OSError:
                        continue
                    if os.path.normcase(os.path.normpath(str(location))) == want:
                        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
    except OSError as exc:
        log.info("uninstall entry not updated: %s", exc)


def set_runonce(command: str | None) -> None:
    """The one-time recovery at the next sign-in (Windows); ``None`` removes it."""
    if sys.platform != "win32":
        return
    import winreg
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUNONCE_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if command:
                winreg.SetValueEx(key, RUNONCE_VALUE, 0, winreg.REG_SZ, command)
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, RUNONCE_VALUE)
    except OSError as exc:
        log.warning("RunOnce recovery entry: %s", exc)


# -- copying, switching, restoring -----------------------------------------------------------------------------------
def copy_listed(source: Path, dest: Path, content: dict) -> None:
    """Copy exactly the files of the content list from ``source`` to the new folder ``dest``."""
    remove(dest)
    dest.mkdir(parents=True)
    for rel in sorted(content["files"]):
        src = source.joinpath(*rel.split("/"))
        dst = dest.joinpath(*rel.split("/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        retry(lambda s=src, d=dst: shutil.copy2(s, d), f"copy {rel}")


def carry_over(keep: list[str], src: Path, dst: Path) -> None:
    """Move the kept items (portable data folder, ``portable.ini``, the installer's uninstaller) from ``src`` to
    ``dst`` where ``dst`` lacks them."""
    names_ = {*keep, *KEEP_ALWAYS} | {p.name for pattern in KEEP_PATTERNS for p in Path(src).glob(pattern)}
    for name in names_:
        old = src / name
        if (old.exists() or old.is_symlink()) and not (dst / name).exists():
            move(old, dst / name)


def switch(target: Path, keep: list[str]) -> None:
    """``.new`` becomes the program folder; the program folder becomes ``.old``. Two renames on one drive."""
    n = names(target)
    retry(lambda: os.replace(target, n["old"]), "move the installed version aside")
    try:
        retry(lambda: os.replace(n["new"], target), "put the new version in place")
    except OSError:
        retry(lambda: os.replace(n["old"], target), "put the installed version back")
        raise
    carry_over(keep, n["old"], target)


def restore(target: Path, keep: list[str]) -> bool:
    """Put the previous version (``.old``) back; idempotent. Returns whether anything had to be restored."""
    n = names(target)
    restored = False
    if n["old"].exists():
        if target.exists():
            carry_over(keep, target, n["old"])
            remove(n["failed"])
            retry(lambda: os.replace(target, n["failed"]), "move the failed version aside")
        retry(lambda: os.replace(n["old"], target), "put the previous version back")
        restored = True
    for leftover in (n["new"], n["failed"]):
        with contextlib.suppress(OSError):
            remove(leftover)
    return restored


def keep_previous(target: Path, backup: Path) -> None:
    """The confirmed update: ``.old`` becomes the kept previous version (``updates\\previous``)."""
    old = names(target)["old"]
    if not old.exists():
        return
    try:
        remove(backup)
        backup.parent.mkdir(parents=True, exist_ok=True)
        move(old, backup)
    except OSError as exc:                # without a kept copy only "Roll back" is lost, never the new version
        log.warning("the previous version could not be kept (%s); it is removed", exc)
        with contextlib.suppress(OSError):
            remove(old)


# -- the health check ------------------------------------------------------------------------------------------------
def health(command: list[str], marker: Path, version: str, timeout: float = 90,
           settle: float = SETTLE_SECONDS) -> tuple[bool, str, subprocess.Popen | None]:
    """Start the program and wait for its health report (``akeno.updater.service.write_health_report``)."""
    marker = Path(marker)
    marker.unlink(missing_ok=True)
    env = dict(os.environ, AKENO_UPDATE_MARKER=str(marker), AKENO_UPDATE_EXPECT=version)
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0      # detached, new process group
    try:
        proc = subprocess.Popen(command, env=env, creationflags=flags, close_fds=True,
                                cwd=str(Path(command[0]).parent) if Path(command[0]).is_absolute() else None)
    except OSError as exc:
        return False, f"could not start ({exc})", None
    end = time.monotonic() + timeout
    report = None
    while time.monotonic() < end:
        if marker.exists():
            try:
                report = json.loads(marker.read_text(encoding="utf-8"))
                break
            except (OSError, ValueError):
                pass                                  # still being written
        if proc.poll() is not None:
            return False, f"ended before it was ready (exit code {proc.returncode})", proc
        time.sleep(0.25)
    if not isinstance(report, dict):
        _end(proc)
        return False, "no health report within the time limit", proc
    failed = [k for k, v in (report.get("checks") or {}).items() if v is not True]
    if report.get("ok") is not True or failed:
        _end(proc)
        return False, f"its own checks failed: {', '.join(failed) or 'not ok'}", proc
    if report.get("version") != version:
        _end(proc)
        return False, f"reports version {report.get('version')}, expected {version}", proc
    settle_end = time.monotonic() + settle
    while time.monotonic() < settle_end:
        if proc.poll() is not None:
            return False, f"ended right after starting (exit code {proc.returncode})", proc
        time.sleep(0.25)
    return True, "running, own checks passed", proc


def _end(proc: subprocess.Popen | None) -> None:
    if proc is not None and proc.poll() is None:
        with contextlib.suppress(OSError):
            proc.kill()
            proc.wait(10)


# -- the update ------------------------------------------------------------------------------------------------------
def _start_command(plan: dict) -> list[str]:
    target = Path(plan["target"])
    return [part.replace("{target}", str(target)) for part in plan.get("start") or [str(target / "AKENO.exe")]]


def run(plan: dict) -> dict:
    """Apply ``plan`` (written by ``UpdateService.plan``); returns the outcome that the next start reports."""
    target, source = Path(plan["target"]), Path(plan["source"])
    keep = list(plan.get("keep") or [])
    journal = Journal(plan.get("journal") or Path(plan["result"]).with_name("journal.json"))
    outcome = {"version": plan["version"], "previous": plan.get("previous_version"), "ok": False,
               "rolled_back": False, "error": "", "at": time.time(), "rollback": bool(plan.get("rollback"))}
    lock_path = plan.get("lock") or Path(plan["result"]).with_name("apply.lock")
    try:
        with lock(lock_path):
            return _run(plan, target, source, keep, journal, outcome)
    except OSError as exc:
        outcome["error"] = f"{exc}; nothing was changed"
        return outcome


def _run(plan, target, source, keep, journal, outcome) -> dict:
    n = names(target)
    if n["old"].exists():                  # only an unfinished update leaves it; recovery must look at it first
        outcome["error"] = f"{n['old']} is left from an earlier update; nothing was changed"
        return outcome
    with contextlib.suppress(OSError):
        remove(n["failed"])
    journal.write(phase="prepared", plan=plan, version=plan["version"], target=str(target), started=time.time())
    if not wait_for_exit(plan.get("pid")):
        journal.write(phase="failed", error="AKENO did not close")
        outcome["error"] = "AKENO did not close; nothing was changed"
        return outcome
    journal.write(phase="stopping")
    left = stop_processes(target, float(plan.get("grace", 15)))
    if left:
        journal.write(phase="failed", error="processes still running")
        outcome["error"] = f"AKENO programs are still running ({', '.join(left)}); nothing was changed"
        return outcome
    try:
        content = staging.load(plan["content"]) if plan.get("content") else None
        if content is not None:
            staging.verify(source, content)
            from akeno.components.service import ComponentError, selected_content
            try:
                action = plan.get("component_action")
                if action is not None and (action not in ("install", "repair", "remove") or
                                           plan["version"] != plan.get("previous_version")):
                    raise ComponentError("Invalid component operation or mismatched Core version.")
                content = selected_content(source, target, content, plan["version"],
                                           enabled=action != "remove" if action is not None else None)
            except ComponentError as exc:
                raise staging.StagingError(str(exc)) from exc
        else:                                  # a rollback to the kept previous version: copy it as it is
            content = {"files": {rel: {} for rel in staging.walk(source)}}
            # An old monolithic executable ignores component selection. Core-only installations must
            # not silently roll back to it and reactivate the excluded component.
            from akeno.components.service import ComponentError, inventory, selection
            try:
                if not selection(target):
                    optional = inventory(source)["security_center"]["files"]
                    content["files"] = {k: v for k, v in content["files"].items() if k not in optional}
            except ComponentError as exc:
                raise staging.StagingError(str(exc)) from exc
        need = sum(int(v.get("size") or 0) for v in content["files"].values()) + 64 * 2 ** 20
        if free_bytes(target.parent) < need:
            raise OSError(f"not enough free space on the drive of {target} (needs {need // 2 ** 20} MB)")
        journal.write(phase="copying")
        copy_listed(source, n["new"], content)
        if plan.get("content"):
            staging.verify(n["new"], content)
        if plan.get("component_action"):
            # The selected marker is local state, written only AFTER payload verification. Keep the old
            # marker in .old so rollback restores the previous physical selection, even for same-version changes.
            from akeno.components.service import write_selection
            write_selection(n["new"], plan["component_action"] != "remove")
    except (OSError, staging.StagingError) as exc:
        with contextlib.suppress(OSError):
            remove(n["new"])
        journal.write(phase="failed", error=str(exc))
        outcome["error"] = f"the new version could not be prepared ({exc}); nothing was changed"
        return outcome
    if plan.get("recovery_command"):
        set_runonce(plan["recovery_command"])
    journal.write(phase="swapping")
    try:
        switch(target, keep)
    except OSError as exc:
        restore(target, keep)
        set_runonce(None)
        journal.write(phase="failed", error=str(exc))
        outcome["error"] = f"the program folder could not be switched ({exc}); nothing was changed"
        return outcome
    journal.write(phase="swapped")
    if plan.get("test_crash_after") == "swapped":           # tests: a power cut right after the switch
        os._exit(9)
    journal.write(phase="health")
    ok, detail, proc = health(_start_command(plan), Path(plan["marker"]), plan["version"],
                              float(plan.get("timeout", 90)), float(plan.get("settle", SETTLE_SECONDS)))
    log.info("health check of %s: %s", plan["version"], detail)
    if ok:
        journal.write(phase="committing", health=detail)
        keep_previous(target, Path(plan["backup"]))
        set_runonce(None)
        update_uninstall_entry(target, plan["version"])
        journal.write(phase="committed")
        outcome["ok"] = True
        return outcome
    log.warning("version %s failed its health check (%s): rolling back to %s", plan["version"], detail,
                plan.get("previous_version"))
    outcome["error"] = f"the new version did not pass its start check ({detail}); "
    return _roll_back(plan, target, keep, journal, outcome, proc, detail)


def _roll_back(plan, target, keep, journal, outcome, proc, detail, start: bool = True) -> dict:
    journal.write(phase="rolling_back", health=detail)
    _end(proc)
    stop_processes(target, grace=5)
    try:
        restore(target, keep)
    except OSError as exc:
        journal.write(phase="failed", error=f"rollback failed: {exc}")
        outcome["error"] += f"the rollback failed ({exc}); the previous version is in {names(target)['old']}"
        return outcome
    set_runonce(None)
    journal.write(phase="rolled_back")
    outcome.update(rolled_back=True)
    outcome["error"] += "the previous version is back"
    if start:
        command = [part.replace("{target}", str(target)) for part in
                   plan.get("start_previous") or plan.get("start") or [str(target / "AKENO.exe")]]
        health(command, Path(plan["marker"]), str(plan.get("previous_version") or ""), float(plan.get("timeout", 90)),
               0)
    return outcome


def recover(journal_path: Path, start: bool = True) -> dict | None:
    """Finish an interrupted update from its journal (after a power cut, a crash or a restart). Idempotent."""
    journal = Journal(journal_path)
    data = journal.load()
    phase = data.get("phase")
    plan = data.get("plan") or {}
    if phase not in UNFINISHED or not plan:
        return None
    target, keep = Path(plan["target"]), list(plan.get("keep") or [])
    outcome = {"version": plan["version"], "previous": plan.get("previous_version"), "ok": False,
               "rolled_back": False, "error": "", "at": time.time(), "recovered": phase}
    lock_path = plan.get("lock") or Path(plan["result"]).with_name("apply.lock")
    try:
        with lock(lock_path):
            stop_processes(target, grace=10)
            if phase == "committing":                     # the new version had passed its health check
                keep_previous(target, Path(plan["backup"]))
                set_runonce(None)
                update_uninstall_entry(target, plan["version"])
                journal.write(phase="committed")
                outcome["ok"] = True
            elif phase in ("prepared", "stopping", "copying"):
                with contextlib.suppress(OSError):
                    remove(names(target)["new"])
                set_runonce(None)
                journal.write(phase="rolled_back")
                outcome["error"] = "the update was interrupted before anything changed"
            else:
                outcome["error"] = "the update was interrupted; "
                outcome = _roll_back(plan, target, keep, journal, outcome, None, f"interrupted in {phase}",
                                     start=False)
    except OSError as exc:
        outcome["error"] = f"the interrupted update could not be finished yet ({exc})"
        return outcome
    Path(plan["result"]).write_text(json.dumps(outcome), encoding="utf-8")
    if start and Path(target / "AKENO.exe").exists():
        flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0
        with contextlib.suppress(OSError):
            subprocess.Popen([str(target / "AKENO.exe")], creationflags=flags, close_fds=True, cwd=str(target))
    return outcome


def handoff_recovery(install: Path, folder: Path) -> bool:
    """At the start of the installed AKENO: an update of this folder was interrupted. Hand over to the staged
    updater (it waits until this process has ended) and return True, so this process exits at once."""
    if os.environ.get("AKENO_UPDATE_MARKER"):
        return False                                   # this is the new version's health check
    journal = Journal(Path(folder) / "journal.json")
    data = journal.load()
    plan = data.get("plan") or {}
    if data.get("phase") not in UNFINISHED or not plan:
        return False
    with contextlib.suppress(OSError, ValueError):
        if Path(plan["target"]).resolve() != Path(install).resolve():
            return False
    if holder_alive(Path(plan.get("lock") or Path(folder) / "apply.lock")):
        return True                                    # the updater is at work; it starts AKENO when it is done
    exe = Path(plan["source"]) / "AKENO.exe"
    if not exe.is_file():
        log.warning("an interrupted update cannot be finished: %s is gone", exe)
        return False
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0
    try:
        subprocess.Popen([str(exe), "--recover-update", str(journal.path), "--wait-pid", str(os.getpid())],
                         creationflags=flags, close_fds=True, cwd=str(exe.parent))
    except OSError as exc:
        log.warning("could not start the update recovery: %s", exc)
        return False
    return True


def _logging(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(folder / "update.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")


def main(argv: list[str]) -> int:
    if "--recover-update" in argv[:-1]:
        journal_path = Path(argv[argv.index("--recover-update") + 1])
        _logging(journal_path.parent)
        if "--wait-pid" in argv[:-1]:
            wait_for_exit(int(argv[argv.index("--wait-pid") + 1]), 60)
        outcome = recover(journal_path, start="--no-start" not in argv)
        log.info("recovery outcome: %s", outcome)
        return 0 if outcome is None or outcome.get("ok") or outcome.get("rolled_back") else 1
    plan_file = Path(argv[argv.index("--apply-update") + 1])
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    result_file = Path(plan["result"])
    _logging(result_file.parent)
    log.info("updating %s from %s to %s", plan["target"], plan.get("previous_version"), plan["version"])
    outcome = run(plan)
    log.info("outcome: %s", outcome)
    result_file.write_text(json.dumps(outcome), encoding="utf-8")
    return 0 if outcome["ok"] else 1
