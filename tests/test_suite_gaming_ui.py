"""Suite 3.0 gaming pages: FPS Booster, Recently Played, Game Hub on a game's page, Benchmark modes, and the
intelligent game mode controller."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from helpers import make_pe


@pytest.fixture
def suite(akeno_home, tmp_path, qtbot, monkeypatch):
    from akeno.core.db import Database
    from akeno.performance import system
    from akeno.services import AppServices
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    monkeypatch.setattr(system, "REGISTRY", system.MemoryRegistry())
    monkeypatch.setattr(system, "POWER", system.MemoryPower())
    from akeno.performance import tweaks
    monkeypatch.setattr(tweaks.system, "REGISTRY", system.REGISTRY)
    folder = tmp_path / "games" / "Speedway"
    folder.mkdir(parents=True)
    make_pe(folder / "speedway.exe", imports=["d3d11.dll"])
    svc = AppServices(db=Database(akeno_home / "suite.sqlite3"))
    svc.boost.registry, svc.boost.power = system.REGISTRY, system.POWER
    game = svc.library.add_manual(str(folder))
    win = MainWindow(Controller(svc))
    qtbot.addWidget(win)
    win.resize(1500, 950)
    win.show()
    yield svc, win, game
    win.sessions.stop()
    from akeno.ui import workers
    workers.drain(10000)


def test_booster_preview_apply_restore(suite, qtbot, monkeypatch):
    svc, win, game = suite
    monkeypatch.setattr("akeno.ui.pages.booster.confirm", lambda *a, **k: True)
    win.go("booster")
    page = win.pages["booster"]
    assert page.game_combo.currentData() == game.id
    page._set_profile("max_fps")
    tweaks_shown = {page.table.item(r, 0).text() for r in range(page.table.rowCount())}
    assert any("Graphics" in x or "Grafik" in x for x in tweaks_shown) and page.table.rowCount() >= 3
    page._apply()
    qtbot.waitUntil(lambda: svc.boost.summary(game.id)["profile"] == "max_fps", timeout=5000)
    from akeno.performance import system, tweaks
    assert system.REGISTRY.get(tweaks.GPU_PREF_KEY, game.exe_path).data == "GpuPreference=2;"
    qtbot.waitUntil(lambda: page.btn_restore.isEnabled(), timeout=5000)
    page._restore()
    qtbot.waitUntil(lambda: svc.boost.summary(game.id)["profile"] is None, timeout=5000)
    assert system.REGISTRY.get(tweaks.GPU_PREF_KEY, game.exe_path) is None


def test_custom_profile_options(suite):
    svc, win, game = suite
    page = win.pages["booster"]
    page._set_profile("custom")
    assert page.custom_box.isVisibleTo(page)
    page.custom["priority"].setCurrentIndex(page.custom["priority"].findData("high"))
    page.custom_fps.setValue(90)
    assert page._custom_options() == {"priority": "high", "frame_limit": 90}
    page._set_profile("balanced")
    assert not page.custom_box.isVisibleTo(page)


def test_game_hub_card_saves_and_list_shows_favourite(suite, qtbot):
    svc, win, game = suite
    win.go("library")
    lib = win.pages["library"]
    lib.open(game.id)
    qtbot.waitUntil(lambda: getattr(lib, "hub", None) is not None and lib.hub.game.id == game.id, timeout=5000)
    hub = lib.hub
    hub.fav.setChecked(True)
    hub.args.setText("-dx11 -windowed")
    hub.args.editingFinished.emit()
    hub.notes.setPlainText("Use the HDR preset")
    hub._save(notes=hub.notes.toPlainText())
    meta = svc.db.game_meta(game.id)
    assert meta["favorite"] == 1 and meta["launch_args"] == "-dx11 -windowed" and meta["notes"] == "Use the HDR preset"
    assert lib.model.data(lib.model.index(0)).startswith("★ ")
    lib.filter.setCurrentIndex(lib.filter.findData("favorites"))
    assert lib.model.rowCount() == 1


def test_recently_played_lists_played_games(suite):
    svc, win, game = suite
    svc.db.add_playtime(game.id, 4000)
    win.go("recent")
    page = win.pages["recent"]
    assert page.list.count() == 1
    from PySide6.QtWidgets import QLabel
    texts = " ".join(lb.text() for lb in page.list.itemAt(0).widget().findChildren(QLabel))
    assert "Speedway" in texts and "1 h 6 min" in texts


def test_benchmark_offers_the_four_modes(suite):
    svc, win, game = suite
    win.go("benchmark")
    page = win.pages["benchmark"]
    ids = [page.label_combo.itemData(i) for i in range(page.label_combo.count())]
    assert {"baseline", "boost", "dlss5", "dlss5_boost"} <= set(ids)
    assert page.compare_table.columnCount() == 5 and page.compare_table.rowCount() >= 10
    page.preset(game.id, "dlss5_boost")
    assert page.label_combo.currentData() == "dlss5_boost"


def test_mode_comparison_deltas_only_from_measurements():
    from akeno.performance.compare import compare

    def session(label, fps, sid):
        stats = {"avg_fps": fps, "low_1pct_fps": fps * 0.8, "low_01pct_fps": None, "median_frametime_ms": 1000 / fps,
                 "p95_frametime_ms": 1100 / fps, "p99_frametime_ms": 1200 / fps, "duration_s": 60,
                 "displayed_fps": None, "generated_frame_share": None}
        return {"id": sid, "game_id": "g", "label": label, "created_at": f"2026-10-24T00:00:0{sid}", "stats": stats,
                "gpu": "RTX 4080", "driver": "581", "resolution": "2560x1440",
                "neural_config": {"measurement": {"reliable": True}, "sensor_samples": [{"gpu_usage_percent": 97}]}}
    rows = {r.mode: r for r in compare([session("baseline", 100, 1), session("boost", 110, 2)])}
    assert rows["boost"].delta_pct["avg_fps"] == 10.0 and rows["boost"].load["gpu_usage_percent"] == 97
    assert rows["dlss5"].session is None and rows["dlss5"].delta_pct == {}
    assert rows["boost"].delta_pct["median_frametime_ms"] == -9.1      # frame time 10.0 -> 9.09 ms, measured
    assert rows["boost"].warnings                      # capture conditions unknown: flagged, not hidden


def test_game_sessions_record_playtime(suite, qtbot, tmp_path, monkeypatch):
    svc, win, game = suite
    exe = tmp_path / "copied" / ("speedway" + (".exe" if os.name == "nt" else ""))
    exe.parent.mkdir()
    shutil.copy2(sys.executable, exe)
    env = dict(os.environ)
    if os.name == "nt":
        import pathlib
        for folder in {os.path.dirname(sys.executable), sys.base_prefix}:
            for dll in pathlib.Path(folder).glob("*.dll"):
                shutil.copy2(dll, exe.parent / dll.name)
        env["PYTHONHOME"] = sys.base_prefix
    game.exe_path = str(exe)
    monkeypatch.setattr(win.sessions.watcher, "_games", lambda: [game])
    monkeypatch.setattr(svc.library, "get", lambda gid: game if gid == game.id else None)
    started, stopped = [], []
    win.sessions.started.connect(lambda g, pid: started.append(g))
    win.sessions.stopped.connect(lambda g, s: stopped.append((g, s)))
    proc = subprocess.Popen([str(exe), "-c", "import time; time.sleep(30)"], env=env)
    try:
        qtbot.waitUntil(lambda: (win.sessions.poll(), bool(started))[1], timeout=10000)
    finally:
        proc.kill()
        proc.wait()
    qtbot.waitUntil(lambda: (win.sessions.poll(), bool(stopped))[1], timeout=10000)
    assert started == [game.id] and stopped[0][0] == game.id
    assert svc.db.game_meta(game.id)["launches"] == 1
