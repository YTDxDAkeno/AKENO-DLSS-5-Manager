"""Transactional installer and backup/restore manager.

Lifecycle of an installation (each step is recorded in SQLite before it happens):

  planned -> backing_up -> applying -> installed            (success)
                                    -> rolled_back | failed  (any error; rollback restores the folder)

Backups live under ``%LOCALAPPDATA%\\AKENO\\backups\\<tx id>`` and are hash-verified right after they
are written, before any game file is touched. AKENO overwrites only files it installed itself or
files the user explicitly approved; foreign files are never deleted, only renamed when approved.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from akeno.backends.base import InstallPlan
from akeno.core import processes
from akeno.core.db import Database
from akeno.core.models import Game
from akeno.i18n import msg
from akeno.paths import backups_dir
from akeno.security.downloads import sha256_file

log = logging.getLogger(__name__)
DISABLED_SUFFIX = ".akeno-disabled"


class InstallError(Exception):
    pass


@dataclass
class RestoreConflict:
    rel_path: str
    reason: str


@dataclass
class TxResult:
    ok: bool
    tx_id: int | None
    state: str
    messages: list[str] = field(default_factory=list)
    conflicts: list[RestoreConflict] = field(default_factory=list)


def _safe_join(base: str, rel: str) -> Path:
    base_p = Path(base).resolve()
    target = (base_p / rel.replace("\\", "/")).resolve()
    if base_p != target and base_p not in target.parents:
        raise InstallError(f"Refusing to write outside the game folder: {rel}")
    return target


def _atomic_write(target: Path, source: Path | None, content: bytes | None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".akeno-tmp")
    if source is not None:
        shutil.copyfile(source, tmp)
    else:
        with open(tmp, "wb") as f:
            f.write(content or b"")
            f.flush()
            os.fsync(f.fileno())
    os.replace(tmp, target)


class Installer:
    def __init__(self, db: Database, backup_root: Path | None = None,
                 running_check: Callable[[str], list] | None = None):
        self.db = db
        self.backup_root = Path(backup_root or backups_dir())
        self.running_check = running_check or (lambda d: processes.processes_in_dir(d))

    # -- queries -------------------------------------------------------------------------------
    def active_install(self, game_id: str, backend_id: str | None = None) -> dict | None:
        for t in self.db.transactions(game_id):
            if t["kind"] == "install" and t["state"] == "installed" and (backend_id is None or t["backend_id"] == backend_id):
                return t
        return None

    def managed_files(self, game_id: str, target_dir: str) -> set[str]:
        out: set[str] = set()
        for t in self.db.transactions(game_id):
            if t["state"] == "installed" and os.path.normcase(t["target_dir"]) == os.path.normcase(target_dir):
                out |= {f["rel_path"].lower() for f in self.db.tx_files(t["id"])}
        return out

    # -- apply ---------------------------------------------------------------------------------
    def apply(self, game: Game, plan: InstallPlan, log_cb: Callable[[str], None] | None = None,
              fail_after: int | None = None) -> TxResult:
        """Apply a confirmed plan. ``fail_after`` injects a failure after N writes (tests only)."""
        def say(msg: str) -> None:
            log.info(msg)
            if log_cb:
                log_cb(msg)

        target_dir = plan.target_dir
        if not os.path.isdir(target_dir):
            return TxResult(False, None, "failed", [msg("Installation blocked: the game folder does not exist "
                                                        "({target_dir}).", target_dir=target_dir)])
        running = self.running_check(game.install_dir)
        if running:
            return TxResult(False, None, "failed",
                            [msg("Installation blocked: the game is currently running ({v}). Close it and try again.",
                                 v=', '.join(sorted({p.name for p in running})))])
        if not os.access(target_dir, os.W_OK):
            return TxResult(False, None, "failed", [
                "Installation blocked: AKENO cannot write to the game folder. AKENO does not request administrator "
                "rights for game folders; move the game to a user-writable library or adjust folder permissions."])

        prior = self.active_install(game.id, plan.backend_id) if plan.kind == "install" else None
        managed = self.managed_files(game.id, target_dir)
        # Refuse to overwrite anything AKENO did not install, unless it is renamed away by approval.
        for op in plan.ops:
            dst = _safe_join(target_dir, op.rel_path)
            if dst.exists() and op.rel_path.lower() not in managed:
                return TxResult(False, None, "failed", [
                    msg("Installation blocked: {rel_path} already exists and was not installed by AKENO. AKENO never "
                        "overwrites files from other tools.", rel_path=op.rel_path)])
        for op in plan.ops:
            if op.source is not None and op.expected_sha256:
                actual = sha256_file(op.source)
                if actual != op.expected_sha256.lower():
                    return TxResult(False, None, "failed", [
                        msg("Installation blocked: {rel_path} failed verification (hash mismatch).",
                            rel_path=op.rel_path)])

        tx = self.db.create_transaction(game_id=game.id, game_name=game.name, backend_id=plan.backend_id,
                                        package_id=plan.package.id if plan.package else None,
                                        package_version=plan.package.version if plan.package else None,
                                        kind=plan.kind, target_dir=target_dir, params=plan.params)
        bdir = self.backup_root / f"tx{tx:05d}"
        self.db.update_transaction(tx, backup_dir=str(bdir), state="backing_up")
        self.db.append_tx_log(tx, "Plan: " + "; ".join(plan.summary))
        say("Creating backup…")
        written: list[str] = []
        renamed: list[str] = []
        try:
            bdir.mkdir(parents=True, exist_ok=True)
            # 1. back up every file that will be replaced, verify the copy
            for op in plan.ops:
                dst = _safe_join(target_dir, op.rel_path)
                if dst.exists():
                    st = dst.stat()
                    orig_sha = sha256_file(dst)
                    bpath = bdir / "files" / op.rel_path.replace("\\", "/")
                    bpath.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dst, bpath)
                    if sha256_file(bpath) != orig_sha:
                        raise InstallError(f"Backup verification failed for {op.rel_path}.")
                    self.db.record_tx_file(tx, op.rel_path, "replace", original_sha256=orig_sha,
                                           original_size=st.st_size, original_mtime=st.st_mtime,
                                           backup_path=str(bpath))
                else:
                    self.db.record_tx_file(tx, op.rel_path, "create")
            for name in plan.rename_away:
                src = _safe_join(target_dir, name)
                if src.exists():
                    self.db.record_tx_file(tx, name, "rename_away", original_sha256=sha256_file(src),
                                           original_size=src.stat().st_size, original_mtime=src.stat().st_mtime)
            self.db.append_tx_log(tx, msg("Backup verified in {bdir}", bdir=bdir))
            say("Backup verified.")

            # 2. apply
            self.db.update_transaction(tx, state="applying")
            for name in plan.rename_away:
                src = _safe_join(target_dir, name)
                if src.exists():
                    dst = src.with_name(src.name + DISABLED_SUFFIX)
                    if dst.exists():
                        raise InstallError(f"Cannot disable {name}: {dst.name} already exists.")
                    os.replace(src, dst)
                    renamed.append(name)
                    self.db.append_tx_log(tx, msg("Disabled {name} (renamed to {name2})", name=name, name2=dst.name))
            for i, op in enumerate(plan.ops):
                if fail_after is not None and i >= fail_after:
                    raise InstallError("Injected failure (test)")
                dst = _safe_join(target_dir, op.rel_path)
                _atomic_write(dst, op.source, op.content)
                written.append(op.rel_path)
                sha = sha256_file(dst)
                self.db.set_tx_file_installed(tx, op.rel_path, sha)
                self.db.append_tx_log(tx, f"Wrote {op.rel_path} ({op.description})")
                say(f"Installed {op.rel_path}")
            # 3. supersede the previous install of the same backend (its backups stay the restore point)
            if prior:
                self.db.update_transaction(prior["id"], state="superseded")
                self.db.append_tx_log(tx, msg("Supersedes transaction {v}", v=prior['id']))
            self.db.update_transaction(tx, state="installed", finished_at=_now())
            self.db.append_tx_log(tx, "Installation recorded.")
            return TxResult(True, tx, "installed", [msg("Installed into {target_dir}.", target_dir=target_dir)])
        except Exception as exc:
            log.exception("install failed")
            self.db.append_tx_log(tx, f"FAILED: {exc}")
            rb = self._rollback(tx, target_dir, written, renamed)
            state = "rolled_back" if rb else "failed"
            self.db.update_transaction(tx, state=state, finished_at=_now())
            msgs = [msg("Installation failed: {exc}", exc=exc)]
            msgs.append("All changes were rolled back." if rb else
                        "Rollback was incomplete; see the transaction log. Your backup is kept.")
            return TxResult(False, tx, state, msgs)

    def _rollback(self, tx: int, target_dir: str, written: list[str], renamed: list[str]) -> bool:
        ok = True
        files = {f["rel_path"]: f for f in self.db.tx_files(tx)}
        for rel in reversed(written):
            rec = files.get(rel)
            dst = _safe_join(target_dir, rel)
            try:
                if rec and rec["action"] == "replace" and rec["backup_path"]:
                    _atomic_write(dst, Path(rec["backup_path"]), None)
                elif dst.exists():
                    dst.unlink()
                self.db.append_tx_log(tx, msg("Rolled back {rel}", rel=rel))
            except OSError as exc:
                ok = False
                self.db.append_tx_log(tx, msg("Rollback of {rel} failed: {exc}", rel=rel, exc=exc))
        for name in renamed:
            src = _safe_join(target_dir, name)
            disabled = src.with_name(src.name + DISABLED_SUFFIX)
            try:
                if disabled.exists() and not src.exists():
                    os.replace(disabled, src)
            except OSError as exc:
                ok = False
                self.db.append_tx_log(tx, msg("Re-enabling {name} failed: {exc}", name=name, exc=exc))
        for leftover in Path(target_dir).glob("*.akeno-tmp"):
            try:
                leftover.unlink()
            except OSError:
                pass
        return ok

    # -- restore -------------------------------------------------------------------------------
    def _chain(self, tx: dict) -> list[dict]:
        """All transactions undone together with ``tx``: same game, backend and folder (newest first)."""
        chain = [tx] + [t for t in self.db.transactions(tx["game_id"])
                        if t["id"] != tx["id"] and t["state"] in ("installed", "superseded")
                        and t["backend_id"] == tx["backend_id"]
                        and os.path.normcase(t["target_dir"]) == os.path.normcase(tx["target_dir"])]
        chain.sort(key=lambda t: t["id"], reverse=True)
        return chain

    def _file_views(self, chain: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
        """(original record per file from the oldest tx, latest record per file from the newest tx)."""
        originals: dict[str, dict] = {}
        latest: dict[str, dict] = {}
        for t in reversed(chain):
            for f in self.db.tx_files(t["id"]):
                originals.setdefault(f["rel_path"], f)
                latest[f["rel_path"]] = f
        return originals, latest

    def check_restore(self, tx_id: int) -> list[RestoreConflict]:
        """Files changed after AKENO installed them (the user or another tool touched them)."""
        tx = self.db.get_transaction(tx_id)
        if not tx:
            return [RestoreConflict("-", "transaction not found")]
        originals, latest = self._file_views(self._chain(tx))
        out = []
        for rel, f in sorted(originals.items()):
            p = _safe_join(tx["target_dir"], rel)
            last = latest[rel]
            if last["action"] in ("create", "replace") and last["installed_sha256"]:
                if not p.exists():
                    out.append(RestoreConflict(rel, "deleted after installation"))
                elif sha256_file(p) != last["installed_sha256"]:
                    out.append(RestoreConflict(rel, "modified after installation"))
            if f["action"] == "replace":
                if not f["backup_path"] or not Path(f["backup_path"]).is_file():
                    out.append(RestoreConflict(rel, "backup file is missing"))
                elif sha256_file(f["backup_path"]) != f["original_sha256"]:
                    out.append(RestoreConflict(rel, "backup file is damaged"))
        return out

    def restore(self, tx_id: int, force: bool = False, game_running_dir: str | None = None) -> TxResult:
        tx = self.db.get_transaction(tx_id)
        if not tx:
            return TxResult(False, tx_id, "failed", ["Transaction not found."])
        if tx["state"] not in ("installed", "superseded", "failed"):
            return TxResult(False, tx_id, tx["state"], [msg("Nothing to restore (state: {v}).", v=tx['state'])])
        running = self.running_check(game_running_dir or tx["target_dir"])
        if running:
            return TxResult(False, tx_id, tx["state"], ["Restore blocked: the game is currently running."])
        conflicts = self.check_restore(tx_id)
        hard = [c for c in conflicts if "backup" in c.reason]
        if hard:
            return TxResult(False, tx_id, tx["state"], ["Restore blocked: a backup file is missing or damaged."], conflicts)
        if conflicts and not force:
            return TxResult(False, tx_id, tx["state"],
                            ["Some files were changed after installation. Confirm to restore anyway."], conflicts)
        chain = self._chain(tx)
        messages = []
        ok = True
        originals, _latest = self._file_views(chain)
        target = tx["target_dir"]
        for rel, f in sorted(originals.items()):
            p = _safe_join(target, rel)
            try:
                if f["action"] == "replace":
                    _atomic_write(p, Path(f["backup_path"]), None)
                    if sha256_file(p) != f["original_sha256"]:
                        raise InstallError(f"restored {rel} does not match its original hash")
                    messages.append(msg("Restored original {rel}", rel=rel))
                elif f["action"] == "create":
                    if p.exists():
                        p.unlink()
                        messages.append(f"Removed {rel}")
                elif f["action"] == "rename_away":
                    disabled = p.with_name(p.name + DISABLED_SUFFIX)
                    if disabled.exists() and not p.exists():
                        os.replace(disabled, p)
                        messages.append(msg("Re-enabled {rel}", rel=rel))
            except (OSError, InstallError) as exc:
                ok = False
                messages.append(msg("Could not restore {rel}: {exc}", rel=rel, exc=exc))
        # Remove now-empty folders AKENO created (e.g. reshade-shaders/Shaders), never others.
        for rel, f in originals.items():
            if f["action"] == "create" and "/" in rel.replace("\\", "/"):
                parent = _safe_join(target, rel).parent
                while parent != Path(target).resolve() and parent.exists():
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
        for t in chain:
            self.db.update_transaction(t["id"], state="restored" if ok else "failed", finished_at=_now())
            self.db.append_tx_log(t["id"], "Restored original files." if ok else "Restore incomplete.")
        return TxResult(ok, tx_id, "restored" if ok else "failed", messages)

    # -- manual snapshots ------------------------------------------------------------------------
    def snapshot(self, game: Game, target_dir: str, rel_paths: list[str], note: str = "") -> TxResult:
        """Back up the given files (those that exist) without changing anything in the game folder."""
        existing = [r for r in rel_paths if _safe_join(target_dir, r).is_file()]
        if not existing:
            return TxResult(False, None, "failed", ["There are no graphics-mod related files to back up in this "
                                                    "folder yet. AKENO backs up automatically before installing."])
        tx = self.db.create_transaction(game_id=game.id, game_name=game.name, backend_id="snapshot",
                                        kind="snapshot", target_dir=target_dir, state="backing_up")
        bdir = self.backup_root / f"tx{tx:05d}"
        self.db.update_transaction(tx, backup_dir=str(bdir))
        try:
            for rel in existing:
                src = _safe_join(target_dir, rel)
                st = src.stat()
                orig = sha256_file(src)
                bpath = bdir / "files" / rel
                bpath.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, bpath)
                if sha256_file(bpath) != orig:
                    raise InstallError(f"Backup verification failed for {rel}.")
                self.db.record_tx_file(tx, rel, "snapshot", original_sha256=orig, original_size=st.st_size,
                                       original_mtime=st.st_mtime, backup_path=str(bpath), installed_sha256=orig)
            self.db.update_transaction(tx, state="snapshot", finished_at=_now())
            self.db.append_tx_log(tx, msg("Snapshot of {v} file(s). {note}", v=len(existing), note=note).strip())
            return TxResult(True, tx, "snapshot", [msg("Backed up and verified {v} file(s).", v=len(existing))])
        except (OSError, InstallError) as exc:
            shutil.rmtree(bdir, ignore_errors=True)
            self.db.update_transaction(tx, state="failed", finished_at=_now())
            return TxResult(False, tx, "failed", [msg("Backup failed: {exc}", exc=exc)])

    def restore_snapshot(self, tx_id: int, force: bool = False) -> TxResult:
        tx = self.db.get_transaction(tx_id)
        if not tx or tx["kind"] != "snapshot":
            return TxResult(False, tx_id, "failed", ["Snapshot not found."])
        if self.running_check(tx["target_dir"]):
            return TxResult(False, tx_id, tx["state"], ["Restore blocked: the game is currently running."])
        files = self.db.tx_files(tx_id)
        changed = []
        for f in files:
            p = _safe_join(tx["target_dir"], f["rel_path"])
            if not Path(f["backup_path"]).is_file() or sha256_file(f["backup_path"]) != f["original_sha256"]:
                return TxResult(False, tx_id, tx["state"], [msg("Backup of {v} is missing or damaged.",
                                                                v=f['rel_path'])])
            if not p.exists() or sha256_file(p) != f["original_sha256"]:
                changed.append(RestoreConflict(f["rel_path"], "differs from the snapshot" if p.exists() else "missing"))
        if not changed:
            return TxResult(True, tx_id, "snapshot", ["All files already match the snapshot."])
        if not force:
            return TxResult(False, tx_id, tx["state"], ["These files will be replaced by their snapshot copies."],
                            changed)
        msgs = []
        for c in changed:
            f = next(x for x in files if x["rel_path"] == c.rel_path)
            _atomic_write(_safe_join(tx["target_dir"], c.rel_path), Path(f["backup_path"]), None)
            msgs.append(f"Restored {c.rel_path}")
        self.db.append_tx_log(tx_id, "; ".join(msgs))
        return TxResult(True, tx_id, "snapshot", msgs)

    def delete_backup(self, tx_id: int) -> TxResult:
        tx = self.db.get_transaction(tx_id)
        if not tx:
            return TxResult(False, tx_id, "failed", ["Transaction not found."])
        if tx["state"] in ("installed", "superseded") and tx["kind"] != "snapshot":
            return TxResult(False, tx_id, tx["state"], [
                "This backup is the restore point of an active installation. Restore or uninstall first."])
        if tx.get("backup_dir"):
            shutil.rmtree(tx["backup_dir"], ignore_errors=True)
        self.db.delete_transaction(tx_id)
        return TxResult(True, tx_id, "deleted", ["Backup deleted."])

    def prune(self, game_id: str, keep: int) -> int:
        """Delete the oldest finished backups of a game beyond ``keep``. Restore points of active
        installations (installed/superseded) are never pruned."""
        finished = [t for t in self.db.transactions(game_id)
                    if t["state"] in ("restored", "rolled_back", "failed", "snapshot")]
        removed = 0
        for tx in finished[max(keep, 1):]:
            if self.delete_backup(tx["id"]).ok:
                removed += 1
        return removed

    def backup_size(self, tx: dict) -> int:
        d = tx.get("backup_dir")
        if not d or not os.path.isdir(d):
            return 0
        return sum(p.stat().st_size for p in Path(d).rglob("*") if p.is_file())


def _now() -> str:
    from akeno.core.db import now_iso
    return now_iso()
