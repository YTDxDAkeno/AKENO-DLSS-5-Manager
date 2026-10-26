"""Upgrading from AKENO DLSS 5 Manager 2.1 to the AKENO Gaming Suite 3.0: the same user folder, database, settings,
install records and backups keep working; files AKENO put into game folders stay recognised as its own."""

from __future__ import annotations

import json
import re
import sqlite3

from akeno.core import db as dbmod


def test_a_21_database_opens_with_everything_kept(akeno_home):
    path = akeno_home / "akeno.sqlite3"
    schema_21 = re.sub(r"CREATE TABLE IF NOT EXISTS game_meta \(.*?\);\n", "", dbmod.SCHEMA, flags=re.S)
    assert "game_meta" not in schema_21
    conn = sqlite3.connect(path)
    conn.executescript(schema_21)
    conn.execute("INSERT INTO games(id, data, manual, user_overrides, updated_at) VALUES(?,?,?,?,?)",
                 ("steam:1091500", json.dumps({"id": "steam:1091500", "name": "Cyberpunk 2077", "install_dir": "C:/G",
                                               "launcher": "steam"}), 0,
                  json.dumps({"stack": {"recipe": "r", "preset": "quality"}}), "2026-09-01T10:00:00Z"))
    conn.execute("INSERT INTO settings(key, value) VALUES(?, ?)", ("language", json.dumps("de")))
    conn.commit()
    conn.close()
    (akeno_home / "backups" / "steam_1091500").mkdir(parents=True)
    (akeno_home / "backups" / "steam_1091500" / "nvngx_dlss.dll.bak").write_bytes(b"original game file")
    db = dbmod.Database(path)
    try:
        assert db.game_meta("steam:1091500")["playtime_s"] == 0, "3.0's table was added"
        assert db.game_overrides("steam:1091500")["stack"]["preset"] == "quality", "2.1 choices are kept"
        assert db.get_setting("language") == "de"
        db.set_game_meta("steam:1091500", favorite=1)
        assert db.all_game_meta()["steam:1091500"]["favorite"] == 1
    finally:
        db.close()
    assert (akeno_home / "backups" / "steam_1091500" / "nvngx_dlss.dll.bak").read_bytes() == b"original game file"


def test_files_installed_by_21_stay_recognised():
    """The marker written next to installed mods is part of their identity: 3.0 keeps it unchanged."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "akeno"
    for rel in ("stack/planner.py", "backends/optiscaler_nr.py", "backends/dlss5_feeder.py"):
        assert '"managed_by": "AKENO DLSS 5 Manager"' in (root / rel).read_text(encoding="utf-8"), rel


def test_version_and_name():
    import akeno
    assert akeno.__version__ == "3.1.0" and akeno.APP_NAME == "AKENO Gaming Suite"
