"""Local storage of the system tools: measurement history, the action journal and scan results.

A separate file, ``system.sqlite3`` beside ``akeno.sqlite3``: the mod engine's database, its schema and its
transaction journal stay exactly as they are, and Cloud Sync never sees this file. Nothing in it leaves the PC.

* ``history``: one row per measurement or report (kind, JSON, time). Each kind keeps its newest
  ``HISTORY_KEEP`` rows.
* ``actions``: every confirmed action, written *before* it runs (``planned``), then ``done``/``failed``, and
  ``undone`` when restored. ``undo`` holds what restoring needs (the previous registry value, the original DNS
  servers...).
* ``kv``: small per-module state (consent given, last scan, schedule).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from akeno.core.db import now_iso

HISTORY_KEEP = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS history_kind ON history(kind, id);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module TEXT NOT NULL,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    state TEXT NOT NULL,            -- planned | done | failed | undone
    detail TEXT NOT NULL DEFAULT '',
    undo TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
SCHEMA_VERSION = 1


class SystemStore:
    def __init__(self, path: str | Path | None = None):
        if path is None:
            from akeno.paths import app_home
            path = app_home() / "system.sqlite3"
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                               (str(SCHEMA_VERSION),))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, args).fetchall())

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, args)

    # -- key / value ---------------------------------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        rows = self._q("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set(self, key: str, value: Any) -> None:
        self._x("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)))

    # -- history -------------------------------------------------------------------------------------------------
    def add_history(self, kind: str, data: dict[str, Any], keep: int = HISTORY_KEEP) -> int:
        with self._lock:
            cur = self._conn.execute("INSERT INTO history(kind, data, created_at) VALUES(?,?,?)",
                                     (kind, json.dumps(data, default=str), data.get("created_at") or now_iso()))
            self._conn.execute("DELETE FROM history WHERE kind=? AND id NOT IN "
                               "(SELECT id FROM history WHERE kind=? ORDER BY id DESC LIMIT ?)", (kind, kind, keep))
            return int(cur.lastrowid)

    def history(self, kind: str, limit: int = 50) -> list[dict[str, Any]]:
        """Newest first."""
        rows = self._q("SELECT id, data, created_at FROM history WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit))
        out = []
        for r in rows:
            d = json.loads(r["data"])
            d.setdefault("created_at", r["created_at"])
            d["_id"] = r["id"]
            out.append(d)
        return out

    def latest(self, kind: str) -> dict[str, Any] | None:
        rows = self.history(kind, 1)
        return rows[0] if rows else None

    def clear_history(self, kind: str | None = None) -> None:
        if kind:
            self._x("DELETE FROM history WHERE kind=?", (kind,))
        else:
            self._x("DELETE FROM history")

    # -- action journal ------------------------------------------------------------------------------------------
    def begin(self, module: str, kind: str, target: str, detail: str = "", undo: dict | None = None) -> int:
        cur = self._x("INSERT INTO actions(module, kind, target, state, detail, undo, created_at) "
                      "VALUES(?,?,?,?,?,?,?)", (module, kind, target, "planned", str(detail),
                                                json.dumps(undo or {}, default=str), now_iso()))
        return int(cur.lastrowid)

    def finish(self, action_id: int, ok: bool, detail: str | None = None, undo: dict | None = None) -> None:
        fields = ["state=?", "finished_at=?"]
        args: list[Any] = ["done" if ok else "failed", now_iso()]
        if detail is not None:
            fields.append("detail=?")
            args.append(str(detail))
        if undo is not None:
            fields.append("undo=?")
            args.append(json.dumps(undo, default=str))
        self._x(f"UPDATE actions SET {', '.join(fields)} WHERE id=?", (*args, action_id))

    def mark_undone(self, action_id: int, detail: str | None = None) -> None:
        if detail is None:
            self._x("UPDATE actions SET state='undone', finished_at=? WHERE id=?", (now_iso(), action_id))
        else:
            self._x("UPDATE actions SET state='undone', finished_at=?, detail=? WHERE id=?",
                    (now_iso(), str(detail), action_id))

    def action(self, action_id: int) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM actions WHERE id=?", (action_id,))
        return _action(rows[0]) if rows else None

    def actions(self, module: str | None = None, kind: str | None = None, state: str | None = None,
                limit: int = 200) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM actions WHERE 1=1", []
        for col, val in (("module", module), ("kind", kind), ("state", state)):
            if val is not None:
                sql += f" AND {col}=?"
                args.append(val)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [_action(r) for r in self._q(sql, tuple(args))]


def _action(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["undo"] = json.loads(d.get("undo") or "{}")
    return d
