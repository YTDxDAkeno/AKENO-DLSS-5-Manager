"""FPS Booster: previews, exact restore, shared system settings, sessions with crash recovery, background
programs, the game watcher and the bottleneck analysis. Registry and power plans use the in-memory stand-ins;
process priorities, pausing and the watcher use real processes."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

from akeno.performance import system, tweaks
from akeno.performance.bottleneck import analyse_csv
from akeno.performance.engine import BoostEngine
from akeno.performance.system import MemoryPower, MemoryRegistry, PowerScheme, RegValue
from akeno.performance.watcher import GameWatcher

GPU_KEY = tweaks.GPU_PREF_KEY


def game(tmp_path, name="Game", exe="game.exe"):
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    path = folder / exe
    path.write_bytes(b"MZ")
    return SimpleNamespace(id=f"manual:{name}", name=name, exe_path=str(path), install_dir=str(folder),
                           exe_candidates=[])


@pytest.fixture
def engine(tmp_path):
    return BoostEngine(tmp_path / "boost.json", MemoryRegistry(), MemoryPower())


def test_preview_lists_every_change_with_before_and_after(engine, tmp_path):
    g = game(tmp_path)
    preview = engine.preview(g, "max_fps")
    kinds = {c.tweak: c for c in preview.changes}
    assert {"gpu_preference", "capture", "power_plan", "priority"} <= set(kinds)
    assert "game_mode" not in kinds                       # already on (Windows' default): no change shown
    assert kinds["gpu_preference"].after == "High performance" and kinds["power_plan"].scope == "session"
    assert str(g.exe_path) in str(kinds["gpu_preference"].detail)
    assert any(u.tweak == "frame_limit" for u in engine.preview(g, "low_latency").unavailable)


def test_apply_and_restore_are_exact(engine, tmp_path):
    g = game(tmp_path)
    reg = engine.registry
    reg.set(GPU_KEY, g.exe_path, RegValue("sz", "SwapEffectUpgradeEnable=1;"))
    reg.set(tweaks.GAMEDVR_KEY, "AppCaptureEnabled", RegValue("dword", 1))
    engine.apply(g, "max_fps")
    assert reg.get(GPU_KEY, g.exe_path).data == "SwapEffectUpgradeEnable=1;GpuPreference=2;"   # other options kept
    assert reg.get(tweaks.GAMEDVR_KEY, "AppCaptureEnabled").data == 0
    engine.apply(g, "battery")                               # another profile: the original stays the backup
    assert reg.get(GPU_KEY, g.exe_path).data == "SwapEffectUpgradeEnable=1;GpuPreference=1;"
    assert reg.get(tweaks.GAMEDVR_KEY, "AppCaptureEnabled").data == 1                    # battery has no capture
    engine.restore(g)
    assert reg.get(GPU_KEY, g.exe_path).data == "SwapEffectUpgradeEnable=1;"
    assert reg.get(tweaks.GAMEDVR_KEY, "AppCaptureEnabled").data == 1
    assert reg.get(tweaks.GAMECONFIG_KEY, "GameDVR_Enabled") is None                    # was absent: absent again
    assert engine.configured(g.id) is None


def test_system_wide_settings_are_shared_between_games(engine, tmp_path):
    a, b = game(tmp_path, "A"), game(tmp_path, "B")
    reg = engine.registry
    engine.apply(a, "max_fps")
    engine.apply(b, "max_fps")
    engine.restore(a)
    assert reg.get(tweaks.GAMEDVR_KEY, "AppCaptureEnabled").data == 0          # B still needs it
    engine.restore(b)
    assert reg.get(tweaks.GAMEDVR_KEY, "AppCaptureEnabled") is None


def test_journal_survives_a_restart(tmp_path):
    reg, power = MemoryRegistry(), MemoryPower()
    g = game(tmp_path)
    BoostEngine(tmp_path / "boost.json", reg, power).apply(g, "standard")
    again = BoostEngine(tmp_path / "boost.json", reg, power)
    assert again.summary(g.id)["profile"] == "standard"
    again.restore(g)
    assert reg.get(GPU_KEY, g.exe_path) is None


def test_missing_power_plan_is_reported_not_created(tmp_path):
    eng = BoostEngine(tmp_path / "b.json", MemoryRegistry(), MemoryPower([PowerScheme(system.BALANCED, "Balanced")]))
    preview = eng.preview(game(tmp_path), "balanced")
    assert any(u.tweak == "power_plan" and "Modern Standby" in str(u.reason) for u in preview.unavailable)
    assert eng.power.list() == [PowerScheme(system.BALANCED, "Balanced")]


def test_dxvk_frame_limit_written_and_restored(engine, tmp_path, monkeypatch):
    g = game(tmp_path)
    conf = tmp_path / "Game" / "dxvk.conf"
    conf.write_text("dxgi.syncInterval = 0\n", encoding="utf-8")
    monkeypatch.setattr("akeno.games.analyzer.dxvk_in", lambda folder: ["d3d11.dll"])
    engine.apply(g, "battery")
    text = conf.read_text(encoding="utf-8")
    assert "dxgi.syncInterval = 0" in text and "dxgi.maxFrameRate = 60" in text and "d3d9.maxFrameRate = 60" in text
    engine.restore(g)
    assert conf.read_text(encoding="utf-8") == "dxgi.syncInterval = 0\n"


def test_rtss_profile_frame_limit(engine, tmp_path, monkeypatch):
    rtss = tmp_path / "RTSS"
    (rtss / "Profiles").mkdir(parents=True)
    monkeypatch.setattr(system, "rtss_dir", lambda: str(rtss))
    g = game(tmp_path)
    preview = engine.preview(g, "low_latency", refresh_hz=144)
    change = next(c for c in preview.changes if c.tweak == "frame_limit")
    assert change.data["fps"] == 141
    engine.apply(g, "low_latency", refresh_hz=144)
    assert "[Framerate]\nLimit=141" in (rtss / "Profiles" / "game.exe.cfg").read_text()
    engine.restore(g)
    assert not (rtss / "Profiles" / "game.exe.cfg").exists()


def _sleeper(tmp_path, name="sleeper"):
    """A real process with its own executable path (a copy of the Python interpreter)."""
    exe = tmp_path / name / (name + (".exe" if os.name == "nt" else ""))
    exe.parent.mkdir(exist_ok=True)
    shutil.copy2(sys.executable, exe)
    env = dict(os.environ)
    if os.name == "nt":                     # a copied python.exe needs its DLLs and its standard library
        for folder in {os.path.dirname(sys.executable), sys.base_prefix}:
            for dll in pathlib.Path(folder).glob("*.dll"):
                shutil.copy2(dll, exe.parent / dll.name)
        env["PYTHONHOME"] = sys.base_prefix
    proc = subprocess.Popen([str(exe), "-c", "import time; time.sleep(60)"], env=env)
    for _ in range(50):
        try:
            if psutil.Process(proc.pid).exe():
                break
        except psutil.Error:
            pass
        time.sleep(0.05)
    return exe, proc


def test_session_power_plan_and_crash_recovery(tmp_path):
    reg, power = MemoryRegistry(), MemoryPower()
    g = game(tmp_path)
    eng = BoostEngine(tmp_path / "boost.json", reg, power)
    eng.apply(g, "balanced")
    exe, proc = _sleeper(tmp_path)
    try:
        applied = eng.start_session(g, proc.pid)
        assert {c.tweak for c in applied} >= {"power_plan", "priority"}
        assert power.active() == system.HIGH_PERFORMANCE
        # AKENO crashes while the game runs; the game then exits; AKENO starts again
        restarted = BoostEngine(tmp_path / "boost.json", reg, power)
        assert restarted.recover({proc.pid}) == []                  # still running: keep the session
    finally:
        proc.kill()
        proc.wait()
    assert restarted.recover(set()) == [g.id]
    assert power.active() == system.BALANCED


def test_background_programs_lowered_and_paused_then_restored(engine, tmp_path, monkeypatch):
    from akeno.performance import background
    monkeypatch.setattr(background, "_own_tree", lambda: {os.getpid()})   # the test's children stand for other apps
    g = game(tmp_path)
    exe, proc = _sleeper(tmp_path, "busytool")
    _, game_proc = _sleeper(tmp_path, "thegame")
    name = os.path.basename(exe).lower()
    try:
        engine.set_background([name, "svchost.exe"], "lower")
        assert engine.background_names() == [name]                    # protected names are never kept
        engine.apply(g, "max_fps")
        engine.start_session(g, game_proc.pid)
        assert system.priority_of(proc.pid) == "below_normal"
        engine.end_session(g)
        assert system.priority_of(proc.pid) == "normal"
        engine.set_background([name], "pause")
        engine.apply(g, "max_fps")
        engine.start_session(g, game_proc.pid)
        assert psutil.Process(proc.pid).status() == psutil.STATUS_STOPPED
        engine.end_session(g)
        assert psutil.Process(proc.pid).status() != psutil.STATUS_STOPPED
    finally:
        for p in (proc, game_proc):
            p.kill()
            p.wait()


def test_watcher_sees_start_and_stop(tmp_path):
    exe, proc = _sleeper(tmp_path, "watched")
    g = SimpleNamespace(id="manual:watched", exe_path=str(exe), exe_candidates=[])
    watcher = GameWatcher(lambda: [g])
    try:
        started, stopped = watcher.poll()
        assert [r.game_id for r in started] == ["manual:watched"] and started[0].pid == proc.pid
        assert watcher.poll() == ([], [])
    finally:
        proc.kill()
        proc.wait()
    started, stopped = watcher.poll()
    assert not started and stopped[0][0].game_id == "manual:watched" and stopped[0][1] >= 0


def _csv(rows):
    head = "Application,ProcessID,SwapChainAddress,SyncInterval,FrameType,MsBetweenAppStart,MsCPUBusy,MsGPUBusy\n"
    return head + "".join(f"game.exe,1,0x1,{s},Application,{ft},{cpu},{gpu}\n" for s, ft, cpu, gpu in rows)


def test_bottleneck_gpu_cpu_limiter_and_unknown():
    gpu = analyse_csv(_csv([(0, 10.0, 4.0, 9.8)] * 100))
    assert gpu.verdict == "gpu" and gpu.gpu_bound_share == 1.0
    cpu = analyse_csv(_csv([(0, 10.0, 9.5, 4.0)] * 100))
    assert cpu.verdict == "cpu" and cpu.cpu_bound_share == 1.0
    capped = analyse_csv(_csv([(1, 16.7, 3.0, 5.0)] * 100))
    assert capped.verdict == "limiter"
    assert analyse_csv("Application,MsBetweenPresents\ngame.exe,10\n").verdict == "unknown"


def test_game_hub_meta_and_playtime(tmp_path):
    from akeno.core.db import Database
    db = Database(tmp_path / "db.sqlite3")
    assert db.game_meta("g1")["favorite"] == 0
    db.set_game_meta("g1", favorite=True, category="Shooter", notes="use the HDR preset", launch_args="-dx12")
    db.add_playtime("g1", 3600)
    db.add_playtime("g1", 1800)
    meta = db.game_meta("g1")
    assert meta["favorite"] == 1 and meta["playtime_s"] == 5400 and meta["launches"] == 2 and meta["last_played"]
    with pytest.raises(ValueError):
        db.set_game_meta("g1", data="x")
