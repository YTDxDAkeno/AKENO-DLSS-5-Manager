"""Guided System Repair: Windows' own repair tools, offered when the analyses give a reason, each explained first.

Every step says what it does, whether it changes anything, whether it needs administrator rights and how long it
takes. AKENO starts DISM and SFC in their own visible window through the UAC prompt (the user sees their output),
waits for them and records the exit code in the journal. Network and disk-space problems hand over to Network Lab
and Smart Cleaner.

Broken shortcuts (Desktop, Start menu) are found read-only; moving them to the Recycle Bin is offered for the
user's own folders only and can be undone from the Recycle Bin.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from akeno.i18n import msg
from akeno.paths import is_windows
from akeno.system import fs, lnk
from akeno.system.commands import run_elevated, system32
from akeno.system.tasks import TaskContext, context


@dataclass(frozen=True)
class RepairStep:
    id: str
    title: str
    what: str                 # what it does
    effect: str               # what it changes
    admin: bool
    duration: str
    changes_system: bool
    module: str = ""          # a hand-over to another page instead of a command


STEPS = (
    RepairStep("dism_scan", "Check the Windows image (DISM ScanHealth)",
               "DISM compares Windows' component store with its manifests and reports damage.",
               "Reads only; nothing is changed.", True, "5–15 minutes", False),
    RepairStep("dism_restore", "Repair the Windows image (DISM RestoreHealth)",
               "DISM replaces damaged components with intact copies from Windows Update.",
               "Changes Windows system components. Needs an internet connection. Your files and programs stay.",
               True, "10–30 minutes", True),
    RepairStep("sfc", "Check and repair system files (SFC)",
               "The System File Checker verifies every protected Windows file and restores damaged ones from the "
               "component store. Run DISM first when the image itself is damaged.",
               "Can replace damaged Windows system files. Your files and programs stay.", True, "10–20 minutes", True),
    RepairStep("startup", "Review startup programs",
               "Startup Analyzer lists what starts with Windows and finds entries whose program is missing.",
               "Nothing changes until you disable an entry.", False, "1 minute", False, "doctor:startup"),
    RepairStep("shortcuts", "Find broken shortcuts",
               "Looks for shortcuts on the desktop and in the Start menu whose program no longer exists.",
               "Reads only; you decide what goes to the Recycle Bin.", False, "under a minute", False),
    RepairStep("network", "Network problems", "Network Lab measures the connection and offers safe repairs.",
               "Nothing changes without your confirmation.", False, "1–2 minutes", False, "network"),
    RepairStep("space", "Low disk space", "Smart Cleaner finds files that can be removed safely.",
               "Nothing is removed without a preview and your confirmation.", False, "1–5 minutes", False, "cleaner"),
)
STEP_TITLES = {s.id: s.title for s in STEPS}
COMMANDS = {
    "dism_scan": ("Dism.exe", "/Online /Cleanup-Image /ScanHealth"),
    "dism_restore": ("Dism.exe", "/Online /Cleanup-Image /RestoreHealth"),
    "sfc": ("sfc.exe", "/scannow"),
}
DISM_CODES = {0: "Completed successfully.", 87: "DISM did not accept the command.",
              740: "Administrator rights are needed.", 1726: "The repair service stopped unexpectedly.",
              0x800F081F: "The repair files were not found (Windows Update unreachable)."}


def recommendations(crash_report: dict | None, slow_report: dict | None) -> dict[str, str]:
    """step id -> why it is recommended now, from the last Crash Detective and Slow PC Detective results."""
    out: dict[str, str] = {}
    crash_ids = [f.get("id", "") for f in (crash_report or {}).get("findings", [])]
    slow_ids = [f.get("id", "") for f in (slow_report or {}).get("findings", [])]
    if any(i in ("crash.bugcheck.ef", "crash.bugcheck.50") for i in crash_ids):
        out["dism_scan"] = str(msg("Crash Detective found blue screens that damaged system files can cause."))
        out["sfc"] = out["dism_scan"]
    if any(i.startswith("crash.app.explorer.exe") or i.startswith("crash.app.svchost.exe") for i in crash_ids):
        out.setdefault("sfc", str(msg("Windows components crashed repeatedly.")))
    if any(i.startswith("startup.") for i in slow_ids):
        out["startup"] = str(msg("Slow PC Detective flagged startup entries."))
    if any(i.startswith("disk.low") for i in slow_ids + crash_ids):
        out["space"] = str(msg("A drive is almost full."))
    return out


def run_step(step_id: str, store=None, ctx: TaskContext | None = None) -> dict[str, Any]:
    """Run DISM or SFC elevated in a visible window. Returns {ok, code, text}; None code = the user declined."""
    ctx = context(ctx)
    if step_id not in COMMANDS:
        raise ValueError(step_id)
    if not is_windows():
        raise OSError(msg("This repair needs Windows."))
    exe, args = COMMANDS[step_id]
    # cmd keeps the tool's window open until the user has read it, and passes the tool's own exit code on
    params = f'/v:on /c "{system32(exe)} {args} & set RC=!errorlevel! & echo. & pause & exit !RC!"'
    aid = store.begin("doctor", "repair." + step_id, exe, f"{exe} {args}") if store else None
    try:
        code = run_elevated(system32("cmd.exe"), params, ctx)
    except Exception as exc:
        if aid:
            store.finish(aid, False, str(exc))
        raise
    if code is None:
        if aid:
            store.finish(aid, False, "UAC declined")
        return {"ok": False, "code": None, "text": str(msg("Administrator rights were not granted; nothing ran."))}
    if exe.lower().startswith("dism"):
        text = DISM_CODES.get(code, f"DISM exit code {code}.")
        ok = code == 0
    else:
        ok = code == 0
        text = "SFC finished. Its window showed the result." if ok else f"SFC exit code {code}; see its window."
    if aid:
        store.finish(aid, ok, text)
    return {"ok": ok, "code": code, "text": text}


# -- broken shortcuts ----------------------------------------------------------------------------------------------
def shortcut_folders() -> dict[str, tuple[str, bool]]:
    """name -> (folder, the user's own folder: may offer Recycle Bin)."""
    out = {}
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    appdata = os.environ.get("APPDATA")
    public = os.environ.get("PUBLIC")
    programdata = os.environ.get("ProgramData")
    if home:
        out["Desktop"] = (os.path.join(home, "Desktop"), True)
    if appdata:
        out["Start menu"] = (os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs"), True)
    if public:
        out["Desktop (all users)"] = (os.path.join(public, "Desktop"), False)
    if programdata:
        out["Start menu (all users)"] = (os.path.join(programdata, r"Microsoft\Windows\Start Menu\Programs"), False)
    return out


def broken_shortcuts(folders: dict[str, tuple[str, bool]] | None = None, ctx: TaskContext | None = None,
                     exists=os.path.exists) -> list[dict[str, Any]]:
    """Shortcuts whose local target does not exist. Targets on network shares, removable drives or shell items
    (no file path) are not judged."""
    ctx = context(ctx)
    out = []
    for label, (folder, own) in (folders or shortcut_folders()).items():
        if not os.path.isdir(folder):
            continue
        for e in fs.walk(folder, ctx, max_files=20_000, max_depth=6):
            if not e.path.lower().endswith(".lnk"):
                continue
            sc = lnk.read(e.path)
            if sc is None or not sc.target or sc.network:
                continue
            target = sc.target
            if target.startswith("\\\\") or not os.path.isabs(target):
                continue
            if is_windows() and not fs.is_fixed_local_drive(target):
                continue
            if not exists(target):
                out.append({"path": e.path, "target": target, "folder": label, "own": own,
                            "identity": fs.identity(e.path)})
    return out


def recycle_shortcuts(items: list[dict[str, Any]], store=None, bin_=None) -> dict[str, list[str]]:
    """Move confirmed broken shortcuts of the user's own folders to the Recycle Bin (re-checked first)."""
    bin_ = bin_ or fs.RecycleBin()
    done, skipped = [], []
    for it in items:
        path = it["path"]
        if not it.get("own") or not path.lower().endswith(".lnk"):
            skipped.append(path)
            continue
        if fs.identity(path) != (tuple(it["identity"]) if it.get("identity") else None):
            skipped.append(path)
            continue
        sc = lnk.read(path)
        if sc is None or not sc.target or os.path.exists(sc.target):
            skipped.append(path)
            continue
        aid = store.begin("doctor", "repair.shortcut", path, sc.target) if store else None
        try:
            bin_.remove(path)
        except OSError as exc:
            if aid:
                store.finish(aid, False, str(exc))
            skipped.append(path)
            continue
        if aid:
            store.finish(aid, True, "Recycle Bin")
        done.append(path)
    return {"done": done, "skipped": skipped}
