"""SQLite persistence for settings, games, profiles, install transactions, backups and benchmarks.

One connection guarded by a lock: AKENO's workloads are small and serialising writes keeps the
transaction log consistent when background workers record state.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    manual INTEGER NOT NULL DEFAULT 0,
    user_overrides TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
    game_id TEXT NOT NULL,
    name TEXT NOT NULL,
    backend_id TEXT NOT NULL,
    params TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (game_id, name, backend_id)
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    game_name TEXT NOT NULL,
    backend_id TEXT NOT NULL,
    package_id TEXT,
    package_version TEXT,
    kind TEXT NOT NULL,            -- install | configure | uninstall | restore
    state TEXT NOT NULL,           -- planned | backing_up | applying | installed | failed | rolled_back | restored | superseded
    target_dir TEXT NOT NULL,
    backup_dir TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    log TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS transaction_files (
    tx_id INTEGER NOT NULL,
    rel_path TEXT NOT NULL,
    action TEXT NOT NULL,          -- create | replace | rename_away
    original_sha256 TEXT,
    original_size INTEGER,
    original_mtime REAL,
    backup_path TEXT,
    installed_sha256 TEXT,
    PRIMARY KEY (tx_id, rel_path)
);
CREATE TABLE IF NOT EXISTS benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    label TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runtimes (
    sha256 TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    original_path TEXT,
    version TEXT,
    signer TEXT,
    trust TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),)
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- low level ---------------------------------------------------------------------------
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def transaction(self) -> "_Tx":
        return _Tx(self)

    # -- settings ------------------------------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self.query("SELECT value FROM settings WHERE key=?", (key,))
        if not rows:
            return default
        return json.loads(rows[0]["value"])

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def all_settings(self) -> dict[str, Any]:
        return {r["key"]: json.loads(r["value"]) for r in self.query("SELECT key, value FROM settings")}

    def clear_settings(self) -> None:
        self.execute("DELETE FROM settings")

    # -- games -------------------------------------------------------------------------------
    def upsert_game(self, game_dict: dict[str, Any], manual: bool = False) -> None:
        self.execute(
            """INSERT INTO games(id, data, manual, updated_at) VALUES(?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at,
               manual=MAX(games.manual, excluded.manual)""",
            (game_dict["id"], json.dumps(game_dict), int(manual), now_iso()),
        )

    def games(self) -> list[tuple[dict[str, Any], bool, dict[str, Any]]]:
        rows = self.query("SELECT data, manual, user_overrides FROM games ORDER BY id")
        return [(json.loads(r["data"]), bool(r["manual"]), json.loads(r["user_overrides"])) for r in rows]

    def delete_game(self, game_id: str) -> None:
        self.execute("DELETE FROM games WHERE id=?", (game_id,))

    def set_game_overrides(self, game_id: str, overrides: dict[str, Any]) -> None:
        self.execute("UPDATE games SET user_overrides=? WHERE id=?", (json.dumps(overrides), game_id))

    def game_overrides(self, game_id: str) -> dict[str, Any]:
        rows = self.query("SELECT user_overrides FROM games WHERE id=?", (game_id,))
        return json.loads(rows[0]["user_overrides"]) if rows else {}

    # -- profiles ------------------------------------------------------------------------------
    def save_profile(self, game_id: str, name: str, backend_id: str, params: dict[str, Any],
                     active: bool = False) -> None:
        with self.transaction():
            if active:
                self.execute("UPDATE profiles SET is_active=0 WHERE game_id=? AND backend_id=?",
                             (game_id, backend_id))
            self.execute(
                """INSERT INTO profiles(game_id, name, backend_id, params, is_active, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?)
                   ON CONFLICT(game_id, name, backend_id) DO UPDATE SET params=excluded.params,
                   is_active=excluded.is_active, updated_at=excluded.updated_at""",
                (game_id, name, backend_id, json.dumps(params), int(active), now_iso()),
            )

    def profiles(self, game_id: str, backend_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM profiles WHERE game_id=?"
        args: tuple = (game_id,)
        if backend_id:
            sql += " AND backend_id=?"
            args += (backend_id,)
        return [
            {"game_id": r["game_id"], "name": r["name"], "backend_id": r["backend_id"],
             "params": json.loads(r["params"]), "is_active": bool(r["is_active"]), "updated_at": r["updated_at"]}
            for r in self.query(sql, args)
        ]

    def active_profile(self, game_id: str, backend_id: str) -> dict[str, Any] | None:
        for p in self.profiles(game_id, backend_id):
            if p["is_active"]:
                return p
        return None

    def delete_profile(self, game_id: str, name: str, backend_id: str) -> None:
        self.execute("DELETE FROM profiles WHERE game_id=? AND name=? AND backend_id=?", (game_id, name, backend_id))

    # -- transactions --------------------------------------------------------------------------
    def create_transaction(self, **kw: Any) -> int:
        kw.setdefault("params", {})
        cur = self.execute(
            """INSERT INTO transactions(game_id, game_name, backend_id, package_id, package_version, kind, state,
               target_dir, backup_dir, params, log, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (kw["game_id"], kw["game_name"], kw["backend_id"], kw.get("package_id"), kw.get("package_version"),
             kw["kind"], kw.get("state", "planned"), kw["target_dir"], kw.get("backup_dir"),
             json.dumps(kw["params"]), "[]", now_iso()),
        )
        return int(cur.lastrowid)

    def update_transaction(self, tx_id: int, **fields: Any) -> None:
        if not fields:
            return
        if "params" in fields:
            fields["params"] = json.dumps(fields["params"])
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE transactions SET {cols} WHERE id=?", (*fields.values(), tx_id))

    def append_tx_log(self, tx_id: int, message: str) -> None:
        with self._lock:
            rows = self.query("SELECT log FROM transactions WHERE id=?", (tx_id,))
            if not rows:
                return
            log = json.loads(rows[0]["log"])
            log.append({"t": now_iso(), "msg": message})
            self.execute("UPDATE transactions SET log=? WHERE id=?", (json.dumps(log), tx_id))

    def get_transaction(self, tx_id: int) -> dict[str, Any] | None:
        rows = self.query("SELECT * FROM transactions WHERE id=?", (tx_id,))
        return _tx_row(rows[0]) if rows else None

    def transactions(self, game_id: str | None = None) -> list[dict[str, Any]]:
        if game_id:
            rows = self.query("SELECT * FROM transactions WHERE game_id=? ORDER BY id DESC", (game_id,))
        else:
            rows = self.query("SELECT * FROM transactions ORDER BY id DESC")
        return [_tx_row(r) for r in rows]

    def record_tx_file(self, tx_id: int, rel_path: str, action: str, **kw: Any) -> None:
        self.execute(
            """INSERT INTO transaction_files(tx_id, rel_path, action, original_sha256, original_size, original_mtime,
               backup_path, installed_sha256) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(tx_id, rel_path) DO UPDATE SET action=excluded.action,
               original_sha256=excluded.original_sha256, original_size=excluded.original_size,
               original_mtime=excluded.original_mtime, backup_path=excluded.backup_path,
               installed_sha256=COALESCE(excluded.installed_sha256, transaction_files.installed_sha256)""",
            (tx_id, rel_path, action, kw.get("original_sha256"), kw.get("original_size"), kw.get("original_mtime"),
             kw.get("backup_path"), kw.get("installed_sha256")),
        )

    def set_tx_file_installed(self, tx_id: int, rel_path: str, sha: str) -> None:
        self.execute("UPDATE transaction_files SET installed_sha256=? WHERE tx_id=? AND rel_path=?",
                     (sha, tx_id, rel_path))

    def tx_files(self, tx_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.query("SELECT * FROM transaction_files WHERE tx_id=? ORDER BY rel_path", (tx_id,))]

    def delete_transaction(self, tx_id: int) -> None:
        with self.transaction():
            self.execute("DELETE FROM transaction_files WHERE tx_id=?", (tx_id,))
            self.execute("DELETE FROM transactions WHERE id=?", (tx_id,))

    # -- benchmarks ----------------------------------------------------------------------------
    def add_benchmark(self, game_id: str, label: str, data: dict[str, Any]) -> int:
        cur = self.execute("INSERT INTO benchmarks(game_id, label, data, created_at) VALUES(?,?,?,?)",
                           (game_id, label, json.dumps(data), now_iso()))
        return int(cur.lastrowid)

    def benchmarks(self, game_id: str | None = None) -> list[dict[str, Any]]:
        if game_id:
            rows = self.query("SELECT * FROM benchmarks WHERE game_id=? ORDER BY id DESC", (game_id,))
        else:
            rows = self.query("SELECT * FROM benchmarks ORDER BY id DESC")
        return [{"id": r["id"], "game_id": r["game_id"], "label": r["label"], "created_at": r["created_at"],
                 **json.loads(r["data"])} for r in rows]

    def delete_benchmark(self, bench_id: int) -> None:
        self.execute("DELETE FROM benchmarks WHERE id=?", (bench_id,))

    # -- runtimes ------------------------------------------------------------------------------
    def add_runtime(self, **kw: Any) -> None:
        self.execute(
            """INSERT OR REPLACE INTO runtimes(sha256, file_name, stored_path, original_path, version, signer, trust,
               imported_at) VALUES(?,?,?,?,?,?,?,?)""",
            (kw["sha256"], kw["file_name"], kw["stored_path"], kw.get("original_path"), kw.get("version"),
             kw.get("signer"), kw["trust"], now_iso()),
        )

    def runtimes(self, file_name: str | None = None) -> list[dict[str, Any]]:
        if file_name:
            rows = self.query("SELECT * FROM runtimes WHERE lower(file_name)=lower(?) ORDER BY imported_at DESC",
                              (file_name,))
        else:
            rows = self.query("SELECT * FROM runtimes ORDER BY imported_at DESC")
        return [dict(r) for r in rows]

    def delete_runtime(self, sha: str) -> None:
        self.execute("DELETE FROM runtimes WHERE sha256=?", (sha,))


def _tx_row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["params"] = json.loads(d["params"] or "{}")
    d["log"] = json.loads(d["log"] or "[]")
    return d


class _Tx:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self) -> "_Tx":
        self.db._lock.acquire()
        self.db._conn.execute("BEGIN")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.db._conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db._lock.release()


def iter_chunks(path: str | Path, size: int = 1 << 20) -> Iterator[bytes]:
    with open(path, "rb") as f:
        while True:
            b = f.read(size)
            if not b:
                return
            yield b
