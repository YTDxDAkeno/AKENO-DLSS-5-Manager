"""The updater process: ``AKENO.exe --apply-update <plan.json>``.

It runs from the new version's unpacked folder, never from the installed one, so the running AKENO can end and
its program folder can be replaced:

1. wait until the AKENO that started the update has ended;
2. move the installed program folder aside: it becomes the backup of the previous version (rollback);
3. copy the new version into the program folder. User data (settings, licenses, game records, backups of game
   files, wallpapers) lives in ``%LOCALAPPDATA%\\AKENO`` and is never part of the program folder; a portable data
   folder inside it (``AKENO_HOME`` pointing there) is carried over;
4. start the new version and wait until it reports a good start;
5. if it does not, put the previous version back and start that one.

Every step is logged to ``%LOCALAPPDATA%\\AKENO\\updates\\update.log`` and the outcome written for the next start.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)
KEEP_ALWAYS = ("portable.ini", "AKENO.ini")


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


def _move_or_copy(src: Path, dst: Path) -> None:
    try:
        os.replace(src, dst)
    except OSError:                       # another drive: copy, then remove
        shutil.copytree(src, dst)
        shutil.rmtree(src, ignore_errors=True)


def swap(target: Path, source: Path, backup: Path, keep: list[str]) -> None:
    """Program folder ``target`` becomes ``backup``; ``source`` (the new version) takes its place."""
    target, source, backup = Path(target), Path(source), Path(backup)
    aside = target.with_name(f".{target.name}.previous")
    if aside.exists():
        shutil.rmtree(aside)
    os.replace(target, aside)                     # same folder: an atomic rename
    try:
        shutil.copytree(source, target)
        for name in {*keep, *KEEP_ALWAYS}:
            old = aside / name
            if old.exists() and not (target / name).exists():
                _move_or_copy(old, target / name)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        os.replace(aside, target)                 # nothing changed for the user
        raise
    if backup.exists():
        shutil.rmtree(backup)
    backup.parent.mkdir(parents=True, exist_ok=True)
    _move_or_copy(aside, backup)


def restore(target: Path, backup: Path, keep: list[str]) -> None:
    """Put the previous version back (its kept data folder comes along from the failed one)."""
    target, backup = Path(target), Path(backup)
    failed = target.with_name(f".{target.name}.failed")
    if failed.exists():
        shutil.rmtree(failed)
    os.replace(target, failed)
    shutil.copytree(backup, target)
    for name in {*keep, *KEEP_ALWAYS}:
        if (failed / name).exists():
            if (target / name).exists():
                shutil.rmtree(target / name) if (target / name).is_dir() else (target / name).unlink()
            _move_or_copy(failed / name, target / name)
    shutil.rmtree(failed, ignore_errors=True)


def started_ok(command: list[str], marker: Path, timeout: float = 90) -> tuple[bool, subprocess.Popen | None]:
    """Start the program; it writes ``marker`` once its window is up. Exiting or hanging first is a failure."""
    marker = Path(marker)
    marker.unlink(missing_ok=True)
    env = dict(os.environ, AKENO_UPDATE_MARKER=str(marker))
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0      # detached, new process group
    proc = subprocess.Popen(command, env=env, creationflags=flags, close_fds=True)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if marker.exists():
            return True, proc
        if proc.poll() is not None:
            return marker.exists(), proc
        time.sleep(0.25)
    proc.kill()
    return False, proc


def run(plan: dict) -> dict:
    target, source, backup = Path(plan["target"]), Path(plan["source"]), Path(plan["backup"])
    keep = list(plan.get("keep") or [])
    marker = Path(plan["marker"])
    start = [part.replace("{target}", str(target)) for part in plan.get("start") or [str(target / "AKENO.exe")]]
    outcome = {"version": plan["version"], "previous": plan.get("previous_version"), "ok": False,
               "rolled_back": False, "error": "", "at": time.time()}
    if not wait_for_exit(plan.get("pid")):
        outcome["error"] = "AKENO did not close; nothing was changed"
        return outcome
    try:
        swap(target, source, backup, keep)
    except OSError as exc:
        outcome["error"] = f"the new version could not be copied ({exc}); nothing was changed"
        return outcome
    ok, _proc = started_ok(start, marker, float(plan.get("timeout", 90)))
    if ok:
        outcome["ok"] = True
        return outcome
    log.warning("version %s did not start: rolling back to %s", plan["version"], plan.get("previous_version"))
    try:
        restore(target, backup, keep)
        outcome.update(rolled_back=True, error="the new version did not start; the previous version is back")
        started_ok(start, marker, float(plan.get("timeout", 90)))
    except OSError as exc:
        outcome["error"] = f"the new version did not start and the rollback failed ({exc}); the previous " \
                           f"version is in {backup}"
    return outcome


def main(argv: list[str]) -> int:
    plan_file = Path(argv[argv.index("--apply-update") + 1])
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    result_file = Path(plan["result"])
    logging.basicConfig(filename=str(result_file.with_name("update.log")), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log.info("updating %s from %s to %s", plan["target"], plan.get("previous_version"), plan["version"])
    outcome = run(plan)
    log.info("outcome: %s", outcome)
    result_file.write_text(json.dumps(outcome), encoding="utf-8")
    return 0 if outcome["ok"] else 1
