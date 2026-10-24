"""Desktop Studio: configuration, playlists, pause rules, media checks, themes (export, import and every refusal),
widgets, the dock, the local socket, the wallpaper renderer and the desktop host with real renderer processes."""

from __future__ import annotations

import json
import os
import struct
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from akeno.desktop import config as cfgmod
from akeno.desktop import media, pause, playlist, themes, winapi


def wait(qapp, condition, timeout: float = 15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def picture(path: Path, color: str = "#4E31D5", size=(320, 180)) -> Path:
    from PySide6.QtGui import QColor, QImage
    img = QImage(size[0], size[1], QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path))
    return path


def web_folder(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "index.html").write_text("<!doctype html><script src='app.js'></script>", encoding="utf-8")
    (path / "app.js").write_text("document.title='ok';", encoding="utf-8")
    (path / "shader.frag").write_text("void main(){}", encoding="utf-8")
    return path


# -- configuration, playlists, pause rules ----------------------------------------------------------------------------
def test_config_defaults_merge_and_profiles(akeno_home):
    cfg = cfgmod.load()
    assert cfg["dock"]["edge"] == "bottom" and cfg["pause"]["game"] is True and cfg["widgets"] == []
    cfg["wallpapers"]["DISPLAY1"] = {"enabled": True, "playlist": [{"path": "a.png"}]}
    cfg["widgets"] = [{"id": "w1", "type": "clock"}, {"id": "w2", "type": "unknown-kind"}]
    cfgmod.save(cfg)
    again = cfgmod.load()
    assert [w["type"] for w in again["widgets"]] == ["clock"], "unknown widget types are dropped"
    w = cfgmod.wallpaper(again, "DISPLAY1")
    assert w["enabled"] and w["fps_limit"] == 30 and w["fit"] == "fill", "missing keys come from the defaults"
    assert cfgmod.wallpaper(again, "OTHER")["enabled"] is False
    cfgmod.save_profile(again, "Gaming")
    again["widgets"] = []
    cfgmod.apply_profile(again, "Gaming")
    assert [w["type"] for w in again["widgets"]] == ["clock"]
    (akeno_home / "desktop" / "desktop.json").write_text("{broken", encoding="utf-8")
    assert cfgmod.load()["version"] == cfgmod.VERSION, "a damaged file falls back to the defaults"


def test_playlist_modes_schedule_and_virtual_desktops():
    items = [{"path": "a", "minutes": 10}, {"path": "b", "minutes": 20}, {"path": "c", "minutes": 30}]
    assert playlist.current_index({"playlist": []}) is None
    assert playlist.current_index({"playlist": items, "mode": "single"}) == 0
    start = datetime(2026, 1, 1, 12, 0)
    rotate = {"playlist": items, "mode": "rotate"}
    assert playlist.current_index(rotate, start.replace(minute=5), started=start) == 0
    assert playlist.current_index(rotate, start.replace(minute=15), started=start) == 1
    assert playlist.current_index(rotate, start.replace(minute=45), started=start) == 2
    assert playlist.current_index(rotate, start.replace(hour=13, minute=5), started=start) == 0, "the cycle repeats"
    sched = {"playlist": items, "mode": "schedule",
             "schedule": [{"from": "06:00", "index": 1}, {"from": "20:00", "index": 2}]}
    assert playlist.current_index(sched, datetime(2026, 1, 1, 7, 0)) == 1
    assert playlist.current_index(sched, datetime(2026, 1, 1, 21, 0)) == 2
    assert playlist.current_index(sched, datetime(2026, 1, 1, 3, 0)) == 2, "before 6:00 the evening entry stays"
    assert playlist.seconds_until_change(sched, datetime(2026, 1, 1, 19, 59, 0)) == 60
    vd = dict(sched, per_virtual_desktop={"desk-2": 0})
    assert playlist.current_index(vd, datetime(2026, 1, 1, 21, 0), virtual_desktop="desk-2") == 0
    assert playlist.current_index(vd, datetime(2026, 1, 1, 21, 0), virtual_desktop="desk-1") == 2
    assert playlist.seconds_until_change({"playlist": items, "mode": "single"}) is None


def test_pause_rules_each_reason_and_switches():
    rules = dict(cfgmod.PAUSE_DEFAULTS)
    assert not pause.decide(rules, pause.State()).paused
    for field in ("game_running", "fullscreen_app", "on_battery", "remote_session", "covered"):
        decision = pause.decide(rules, pause.State(**{field: True}))
        assert decision.paused and len(decision.reasons) == 1, field
    assert pause.decide(rules, pause.State(cpu_percent=90)).paused
    assert not pause.decide(rules, pause.State(cpu_percent=50)).paused
    off = dict(rules, game=False, cpu_percent=0)
    assert not pause.decide(off, pause.State(game_running=True, cpu_percent=99)).paused


def test_media_kinds_and_checks(tmp_path, qapp):
    img = picture(tmp_path / "a.png")
    assert media.check(img) == "image"
    assert media.kind_of(tmp_path / "b.webm") == "video" and media.kind_of(tmp_path / "c.gif") == "gif"
    web = web_folder(tmp_path / "web")
    assert media.check(web) == "web" and media.web_entry(web).name == "index.html"
    with pytest.raises(media.MediaError):
        media.check(tmp_path / "missing.mp4")
    (tmp_path / "x.exe").write_bytes(b"MZ")
    with pytest.raises(media.MediaError):
        media.check(tmp_path / "x.exe")


def test_covered_by_maximised_or_fullscreen_window():
    mon = winapi.Monitor("D1", 0, 0, 1920, 1080)
    right = winapi.Monitor("D2", 1920, 0, 2560, 1440)
    assert winapi.covers((0, 0, 1920, 1080), mon, False), "borderless fullscreen"
    assert winapi.covers((-8, -8, 1928, 1040), mon, True), "maximised (taskbar visible)"
    assert not winapi.covers((100, 100, 900, 700), mon, False)
    assert not winapi.covers((0, 0, 1920, 1080), right, False), "another monitor"


# -- themes -----------------------------------------------------------------------------------------------------------
def make_theme(tmp_path, qapp) -> tuple[dict, Path]:
    img = picture(tmp_path / "src" / "sunset.png")
    web = web_folder(tmp_path / "src" / "waves")
    cfg = cfgmod.load()
    cfg["wallpapers"]["M1"] = dict(cfgmod.WALLPAPER_DEFAULTS, enabled=True, mode="schedule", fit="fit",
                                   playlist=[{"path": str(img), "kind": "image", "minutes": 5},
                                             {"path": str(web), "kind": "web", "minutes": 15}],
                                   schedule=[{"from": "18:00", "index": 1}])
    cfg["dock"].update(edge="left", opacity=80, items=[{"type": "app", "path": "C:/secret/tool.exe"}])
    cfg["widgets"] = [{"id": "w1", "type": "clock", "x": 5, "y": 6, "w": 200, "h": 100, "options": {"h24": False}},
                      {"id": "w2", "type": "notes", "x": 5, "y": 120, "w": 200, "h": 100,
                       "options": {"text": "my private note"}}]
    out = themes.export(cfg, "M1", tmp_path / "Sunset", "Sunset Waves", "Akeno", "Evening colours", "CC BY 4.0",
                        preview_png=(tmp_path / "src" / "sunset.png").read_bytes())
    return cfg, out


def test_theme_export_import_apply_roundtrip(tmp_path, qapp):
    cfg, out = make_theme(tmp_path, qapp)
    assert out.suffix == ".akenotheme"
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read("theme.json"))
    assert "preview.png" in names and any(n.endswith("/sunset.png") for n in names)
    assert any(n.endswith("/index.html") for n in names) and any(n.endswith("/app.js") for n in names)
    assert "items" not in manifest["dock"], "dock pins are personal paths and never exported"
    assert manifest["widgets"][1]["options"] == {}, "notes stay on this PC"
    lib = tmp_path / "library"
    lib.mkdir()
    th = themes.install(out, root=lib)
    assert th.name == "Sunset Waves" and th.author == "Akeno" and th.preview is not None and th.files == 2
    with pytest.raises(FileExistsError):
        themes.install(out, root=lib)
    assert themes.install(out, replace=True, root=lib).id == th.id
    assert [x.id for x in themes.library(lib)] == [th.id]
    fresh = cfgmod.load()
    fresh["dock"]["items"] = [{"type": "app", "path": "mine.exe"}]
    themes.apply(th, fresh, ["A", "B"])
    for monitor in ("A", "B"):
        w = fresh["wallpapers"][monitor]
        assert w["enabled"] and w["mode"] == "schedule" and w["fit"] == "fit"
        assert [i["kind"] for i in w["playlist"]] == ["image", "web"]
        assert all(Path(i["path"]).exists() and lib in Path(i["path"]).parents for i in w["playlist"])
        assert w["schedule"] == [{"from": "18:00", "index": 1}]
    assert fresh["dock"]["edge"] == "left" and fresh["dock"]["items"] == [{"type": "app", "path": "mine.exe"}]
    assert [w["type"] for w in fresh["widgets"]] == ["clock", "notes"]
    assert media.check(fresh["wallpapers"]["A"]["playlist"][1]["path"]) == "web"
    themes.remove(th, root=lib)
    assert themes.library(lib) == []


def zipped(tmp_path, entries: dict, manifest: dict | None = None, name="t.akenotheme") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as z:
        if manifest is not False:
            z.writestr("theme.json", json.dumps(manifest or {"format": "akeno-theme", "version": 1, "name": "X",
                                                            "wallpaper": {"playlist": []}}))
        for arc, data in entries.items():
            z.writestr(arc, data)
    return path


@pytest.mark.parametrize("entry", ["../evil.png", "media/../../evil.png", "/abs/evil.png", "C:/evil.png",
                                   "media\\evil.png", "media/tool.exe", "media/run.bat", "media/x.ps1",
                                   "media/link.lnk", "media/lib.dll", "other/a.png", "setup.exe"])
def test_theme_import_refuses_unsafe_entries(tmp_path, entry):
    with pytest.raises(themes.ThemeError):
        themes.check_archive(zipped(tmp_path, {entry: b"data"}))


def test_theme_import_refuses_links_bombs_and_bad_manifests(tmp_path):
    link = tmp_path / "link.akenotheme"
    with zipfile.ZipFile(link, "w") as z:
        z.writestr("theme.json", json.dumps({"format": "akeno-theme", "version": 1, "name": "X"}))
        info = zipfile.ZipInfo("media/a.png")
        info.external_attr = (0o120777 << 16)
        z.writestr(info, "/etc/passwd")
    with pytest.raises(themes.ThemeError, match="link"):
        themes.check_archive(link)
    bomb = tmp_path / "bomb.akenotheme"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("theme.json", json.dumps({"format": "akeno-theme", "version": 1, "name": "X"}))
        z.writestr("media/a.png", b"\0" * (64 * 2 ** 20))
    with pytest.raises(themes.ThemeError, match="compression"):
        themes.check_archive(bomb)
    (tmp_path / "plain.txt").write_text("hello")
    with pytest.raises(themes.ThemeError):
        themes.check_archive(tmp_path / "plain.txt")
    with pytest.raises(themes.ThemeError, match="theme.json"):
        themes.check_archive(zipped(tmp_path, {"media/a.png": b"x"}, manifest=False, name="n.akenotheme"))
    with pytest.raises(themes.ThemeError, match="missing"):
        themes.check_archive(zipped(tmp_path, {}, {"format": "akeno-theme", "version": 1, "name": "X", "wallpaper": {
            "playlist": [{"file": "media/gone.png", "kind": "image"}]}}, name="m.akenotheme"))
    with pytest.raises(themes.ThemeError, match="not a supported"):
        themes.check_archive(zipped(tmp_path, {"media/a.png": b"x"}, {
            "format": "akeno-theme", "version": 1, "name": "X",
            "wallpaper": {"playlist": [{"file": "media/a.png", "kind": "video"}]}}, name="k.akenotheme"))
    with pytest.raises(themes.ThemeError, match="newer"):
        themes.check_archive(zipped(tmp_path, {}, {"format": "akeno-theme", "version": 99, "name": "X"},
                                    name="v.akenotheme"))


def test_theme_manifest_values_are_clamped(tmp_path):
    manifest = {"format": "akeno-theme", "version": 1, "name": "N" * 500, "author": "A",
                "wallpaper": {"mode": "evil", "fit": "x", "volume": 900, "fps_limit": 1, "playlist": [
                    {"file": "media/a.png", "kind": "image", "minutes": 10 ** 9}],
                    "schedule": [{"from": "25:00", "index": 0}, {"from": "07:30", "index": 0},
                                 {"from": "08:00", "index": 7}]},
                "dock": {"edge": "middle", "opacity": 5, "icon_size": 999, "autohide": "yes", "items": ["x"]},
                "widgets": [{"type": "clock", "options": {"cmd": {"run": "calc.exe"}, "h24": True}},
                            {"type": "not-a-widget"}]}
    clean = themes.check_archive(zipped(tmp_path, {"media/a.png": b"x"}, manifest))
    wp = clean["wallpaper"]
    assert len(clean["name"]) == 60 and wp["mode"] == "single" and wp["fit"] == "fill"
    assert wp["volume"] == 100 and wp["fps_limit"] == 5 and wp["playlist"][0]["minutes"] == 24 * 60
    assert wp["schedule"] == [{"from": "07:30", "index": 0}]
    assert clean["dock"] == {"opacity": 30, "icon_size": 96}
    assert [w["type"] for w in clean["widgets"]] == ["clock"] and clean["widgets"][0]["options"] == {"h24": True}


# -- widgets ----------------------------------------------------------------------------------------------------------
def test_sensor_parsing_and_formatting(tmp_path, db):
    from akeno.desktop import sensors
    rows = sensors.parse_nvidia("NVIDIA GeForce RTX 4080, 37, 4012, 16376, 52, 120.5\n"
                                "NVIDIA GeForce RTX 5090, [N/A], 1000, 32607, 40, [Not Supported]\nbad line\n")
    assert rows[0] == {"name": "NVIDIA GeForce RTX 4080", "util": 37.0, "vram_used_mib": 4012.0,
                       "vram_total_mib": 16376.0, "temp_c": 52.0, "power_w": 120.5}
    assert rows[1]["util"] is None and rows[1]["power_w"] is None and len(rows) == 2
    assert sensors.rate(512) == "512 B/s" and sensors.rate(3 * 2 ** 20) == "3.0 MB/s" and sensors.rate(None) == "—"
    assert sensors.duration(90061) == "1 d 1 h 1 min"
    sample = sensors.Sampler()
    sample.need("disk")
    data = sample.sample()
    assert 0 <= data["cpu"] <= 100 and data["ram_total_gb"] > 0 and "drives" in data
    folder = tmp_path / "dl"
    folder.mkdir()
    for i, name in enumerate(("old.zip", "new.iso", "part.crdownload")):
        (folder / name).write_bytes(b"x")
        os.utime(folder / name, (1000 + i, 1000 + i))
    assert [p.name for p in sensors.recent_files(folder)] == ["new.iso", "old.zip"]
    assert sensors.last_benchmark(Path(db.path)) is None
    db.execute("INSERT INTO games(id, data, updated_at) VALUES(?,?,?)",
               ("g1", json.dumps({"id": "g1", "name": "Speedway"}), "now"))
    db.add_benchmark("g1", "DLSS 5", {"stats": {"avg_fps": 144.4, "low_1pct_fps": 98.0}})
    last = sensors.last_benchmark(Path(db.path))
    assert last["avg_fps"] == 144.4 and last["low_1pct_fps"] == 98.0 and last["game"] == "Speedway"


def test_every_widget_builds_and_the_manager_saves(qapp, tmp_path):
    from akeno.desktop import sensors
    from akeno.desktop import widgets as dw

    class Meter:
        def __init__(self):
            self.v = [0.1] * 128

        def peak(self):
            return 0.5

        def values(self):
            return self.v
    ctx = dw.Context(sensors.Sampler(), Meter(), tmp_path / "none.sqlite3", license_view=None)
    saved = []
    mgr = dw.WidgetManager(ctx, lambda widgets, locked: saved.append((widgets, locked)))
    specs = []
    for kind in cfgmod.WIDGET_TYPES:
        specs.append(dw.new_widget(kind, specs))
    assert len({s["id"] for s in specs}) == len(specs)
    mgr.sync(specs, False)
    assert len(mgr.windows) == len(cfgmod.WIDGET_TYPES)
    for win in mgr.windows.values():
        win.panel.refresh()
        assert win.grab().width() > 0
    weather = next(w for w in mgr.windows.values() if w.spec["type"] == "weather")
    assert "open-meteo" in weather.panel.sub.text(), "no place: the weather widget sends nothing"
    notes = next(w for w in mgr.windows.values() if w.spec["type"] == "notes")
    notes.panel.edit.setPlainText("buy milk")
    notes.panel._store()
    assert saved and any(w["options"].get("text") == "buy milk" for w in saved[-1][0])
    todo = next(w for w in mgr.windows.values() if w.spec["type"] == "todo")
    todo.panel.entry.setText("ship 3.0")
    todo.panel._add()
    assert any(w["options"].get("items") == [{"text": "ship 3.0", "done": False}] for w in saved[-1][0])
    mgr._toggle_lock()
    assert saved[-1][1] is True
    mgr._removed(specs[0]["id"])
    assert len(saved[-1][0]) == len(specs) - 1
    mgr.set_paused(True)
    assert not any(w.timer.isActive() for w in mgr.windows.values())
    mgr.set_paused(False)
    mgr.sync([s for s in specs if s["type"] == "cpu"], False)
    assert list(mgr.windows) == [next(s["id"] for s in specs if s["type"] == "cpu")]
    mgr.close()


def test_widget_layout_export_import(tmp_path):
    from akeno.desktop import widgets as dw
    layout = [{"id": "w1", "type": "notes", "x": 1, "y": 2, "w": 300, "h": 200, "options": {"text": "secret"}},
              {"id": "w2", "type": "weather", "x": 40, "y": 40, "w": 260, "h": 150,
               "options": {"place": "Berlin", "lat": 52.5, "lon": 13.4}}]
    file = tmp_path / "layout.json"
    dw.export_layout(layout, file)
    assert "secret" not in file.read_text()
    back = dw.import_layout(file)
    assert [w["type"] for w in back] == ["notes", "weather"] and back[1]["options"]["place"] == "Berlin"
    assert back[0]["x"] == 1 and back[0]["w"] == 300
    file.write_text(json.dumps({"format": "other"}))
    with pytest.raises(ValueError):
        dw.import_layout(file)


# -- dock -------------------------------------------------------------------------------------------------------------
def shell_link(target: str) -> bytes:
    """A minimal [MS-SHLLINK] file with a LinkInfo local base path."""
    header = bytearray(0x4C)
    header[0:4] = struct.pack("<I", 0x4C)
    header[0x14:0x18] = struct.pack("<I", 0x2)                       # HasLinkInfo
    path = target.encode("latin-1") + b"\0"
    info = struct.pack("<IIIIIII", 0x1C + len(path), 0x1C, 0x1, 0, 0x1C, 0, 0) + path
    return bytes(header) + info


def test_dock_shortcut_target_and_editing(qapp, tmp_path):
    from akeno.desktop import dock
    lnk = tmp_path / "Game.lnk"
    lnk.write_bytes(shell_link("C:\\Games\\Speedway\\speedway.exe"))
    assert dock.lnk_target(str(lnk)) == "C:\\Games\\Speedway\\speedway.exe"
    (tmp_path / "bad.lnk").write_bytes(b"not a link")
    assert dock.lnk_target(str(tmp_path / "bad.lnk")) is None
    changes = []
    cfg = dict(cfgmod.DOCK_DEFAULTS, enabled=True)
    win = dock.DockWindow(cfg, changes.append)
    win.add_path(str(lnk))
    win.add_group("Tools", [sys.executable])
    assert [i["type"] for i in changes[-1]["items"]] == ["app", "group"]
    assert win.buttons[0].target() == "C:\\Games\\Speedway\\speedway.exe"
    exe = dock.DockButton({"type": "app", "path": sys.executable}, 48, lambda _: None)
    assert exe.target() in (sys.executable, None)
    win.remove_item(changes[-1]["items"][0])
    assert [i["type"] for i in changes[-1]["items"]] == ["group"]
    for edge in dock.EDGES:
        win.cfg["edge"] = edge
        win.rebuild()
        assert win.vertical() == (edge in ("left", "right"))
    win.close()


# -- local socket, renderer, host -------------------------------------------------------------------------------------
def test_ipc_server_and_channel(qapp):
    from akeno.desktop import ipc
    server = ipc.Server(ipc.server_name() + "-ipc")
    assert server.listen()
    got = []
    server.connected.connect(lambda ch: ch.message.connect(lambda m, c=ch: (got.append(m), c.send({"cmd": "pong"}))))
    client = ipc.Channel.open(server.name, {"role": "app"})
    answers = []
    client.message.connect(answers.append)
    client.send({"cmd": "ping"})
    assert wait(qapp, lambda: answers)
    assert [m["cmd"] for m in got] == ["hello", "ping"] and got[0]["role"] == "app"
    second = ipc.Server(server.name)
    assert not second.listen(), "only one host per user"
    client.close()
    assert wait(qapp, lambda: not server.channels)
    server.close()
    assert ipc.Channel.open(server.name + "-none", timeout_ms=200) is None
    for obj in (client, second, server):              # gone before the QApplication is (no crash at exit)
        obj.deleteLater()
    qapp.processEvents()


def test_renderer_shows_the_playlist_and_pauses(qapp, tmp_path):
    from akeno.desktop import renderer
    a = picture(tmp_path / "a.png", "#ff0000")
    b = picture(tmp_path / "b.png", "#00ff00")
    cfg = cfgmod.load()
    cfg["wallpapers"]["M"] = dict(cfgmod.WALLPAPER_DEFAULTS, enabled=True, playlist=[
        {"path": str(a), "kind": "image"}, {"path": str(b), "kind": "image"}], per_virtual_desktop={"desk-b": 1})
    file = tmp_path / "desktop.json"
    cfgmod.save(cfg, file)
    win = renderer.WallpaperWindow("M", file)
    win.resize(320, 180)
    win.show()
    qapp.processEvents()
    assert win.index == 0 and isinstance(win.view, renderer.ImageView)
    assert win.grab().toImage().pixelColor(160, 90).name() == "#ff0000"
    win.set_virtual_desktop("desk-b")
    qapp.processEvents()
    assert win.index == 1 and win.grab().toImage().pixelColor(160, 90).name() == "#00ff00"
    win.set_paused(True)
    assert win.paused and not win.fast.isActive()
    cfg["wallpapers"]["M"]["playlist"][1]["path"] = str(tmp_path / "gone.png")
    cfgmod.save(cfg, file)
    win.reload()
    assert win.error, "a missing file is reported, the window stays black instead of crashing"
    win.close()


def test_renderer_folder_scheme_stays_inside_the_folder(tmp_path):
    from akeno.desktop import renderer
    web = web_folder(tmp_path / "web")
    (tmp_path / "secret.txt").write_text("secret")
    assert renderer.resolve(web, "/app.js") == (web / "app.js").resolve()
    assert renderer.resolve(web, "/%2e%2e/secret.txt") is None
    assert renderer.resolve(web, "/../secret.txt") is None
    assert renderer.resolve(web, "/missing.js") is None


def test_crash_backoff_gives_up_after_repeated_crashes():
    from akeno.desktop.host import Crashes, GIVE_UP_AFTER
    c = Crashes()
    delays = [c.record(100.0 + i) for i in range(GIVE_UP_AFTER)]
    assert delays[:4] == [2, 5, 15, 60] and delays[-1] is None
    calm = Crashes()
    assert [calm.record(t) for t in (0, 700, 1400, 2100, 2800)] == [2, 2, 2, 2, 2], "old crashes are forgotten"


@pytest.mark.skipif(sys.platform == "win32", reason="real renderer processes would attach to the CI desktop")
def test_host_runs_restarts_and_pauses_real_renderers(qapp, akeno_home, tmp_path):
    from PySide6.QtGui import QGuiApplication
    from akeno.desktop import host as hostmod
    from akeno.desktop import ipc
    img = picture(tmp_path / "wall.png")
    name = QGuiApplication.primaryScreen().name()
    cfg = cfgmod.load()
    cfg["wallpapers"][name] = dict(cfgmod.WALLPAPER_DEFAULTS, enabled=True,
                                   playlist=[{"path": str(img), "kind": "image"}])
    cfgmod.save(cfg)
    repo = str(Path(__file__).resolve().parents[1])

    def spawn(*args):
        return sys.executable, ["-c", f"import sys; sys.path.insert(0, {repo!r}); from akeno.app import main; "
                                      f"raise SystemExit(main(sys.argv[1:]))", *args]
    host = hostmod.DesktopHost(spawn=spawn, db_file=akeno_home / "none.sqlite3", exit_when_idle=False)
    assert host.start()
    try:
        r = host.renderers[name]
        assert wait(qapp, lambda: r.state == "running", 40), r.state
        pid = r.process.processId()
        r.process.kill()                                  # a crashing wallpaper
        assert wait(qapp, lambda: r.state == "waiting", 10)
        assert wait(qapp, lambda: r.state == "running" and r.process and r.process.processId() != pid, 40)
        app = ipc.Channel.open(host.server.name, {"role": "app"})
        app.send({"cmd": "game", "running": True})
        assert wait(qapp, lambda: r.paused is True, 10)
        assert host.status()["renderers"][name]["reasons"]
        app.send({"cmd": "game", "running": False})
        assert wait(qapp, lambda: r.paused is False, 10)
        cfg["wallpapers"][name]["enabled"] = False
        cfgmod.save(cfg)
        assert wait(qapp, lambda: name not in host.renderers, 10), "the config file is followed live"
        assert wait(qapp, lambda: r.process is None, 10), "the renderer quits when its wallpaper is switched off"
    finally:
        for renderer in host.renderers.values():
            if renderer.process is not None:
                renderer.process.kill()
                renderer.process.waitForFinished(3000)
        host.server.close()
        host.lock.unlock()


def test_host_scans_library_games_while_the_suite_is_closed(db, akeno_home):
    from akeno.desktop import host as hostmod
    import psutil
    exe = psutil.Process().exe()                          # the real path (sys.executable may be a venv link)
    db.execute("INSERT INTO games(id, data, updated_at) VALUES(?,?,?)",
               ("g1", json.dumps({"exe_path": exe, "exe_candidates": []}), "now"))
    games = hostmod.library_games(Path(db.path))
    assert [g.id for g in games] == ["g1"] and games[0].exe_path == exe
    from akeno.performance.watcher import GameWatcher
    assert "g1" in GameWatcher(lambda: games).scan(), "this test's own Python counts as the running game"
    assert hostmod.library_games(akeno_home / "missing.sqlite3") == []


def test_stop_keeps_the_setup_and_nothing_starts(qapp, tmp_path):
    from akeno.desktop import client
    from akeno.desktop import host as hostmod
    cfg = cfgmod.load()
    cfg["widgets"] = [{"id": "w1", "type": "clock"}]
    assert client.wanted(cfg)
    cfg["enabled"] = False
    assert not client.wanted(cfg), "Stop: nothing is started, also after a restart of the PC"
    cfgmod.save(cfg)
    host = hostmod.DesktopHost(exit_when_idle=False)
    assert host.idle() and cfgmod.load()["widgets"], "the widgets stay configured"
    host.deleteLater()


def test_desktop_processes_are_dispatched_before_the_suite():
    from akeno import app
    assert app.desktop_process(["--minimized"]) is None


# -- pages ------------------------------------------------------------------------------------------------------------
@pytest.fixture
def desktop_window(akeno_home, qtbot, monkeypatch):
    from akeno.core.db import Database
    from akeno.desktop import client
    from akeno.services import AppServices
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    started = []
    monkeypatch.setattr(client.DesktopClient, "start", lambda self: started.append(True) or True)
    svc = AppServices(db=Database(akeno_home / "suite.sqlite3"))
    win = MainWindow(Controller(svc))
    qtbot.addWidget(win)
    win.resize(1500, 950)
    win.show()
    yield win, started
    win.sessions.stop()
    from akeno.ui import workers
    workers.drain(10000)


def test_desktop_pages_edit_the_configuration(desktop_window, tmp_path, qapp):
    win, started = desktop_window
    for key in ("wallpapers", "dock", "widgets", "themes"):
        win.go(key)
        qapp.processEvents()
        assert win.current_page() == key
    page = win.pages["wallpapers"]
    img = picture(tmp_path / "pics" / "one.png")
    problems = page.add_paths([str(img), str(tmp_path / "pics" / "notes.txt")])
    assert len(problems) == 1
    w = cfgmod.wallpaper(cfgmod.load(), page.name())
    assert w["enabled"] and [Path(i["path"]).name for i in w["playlist"]] == ["one.png"]
    assert started, "switching a wallpaper on starts the desktop host"
    page.chk_scheduled.setChecked(True)
    assert cfgmod.wallpaper(cfgmod.load(), page.name())["schedule"] == [{"from": "08:00", "index": 0}]
    page.rule_checks["battery"].setChecked(False)
    assert cfgmod.load()["pause"]["battery"] is False
    page._remove()
    assert not cfgmod.wallpaper(cfgmod.load(), page.name())["enabled"]
    page._stop()
    assert cfgmod.load()["enabled"] is False
    page.add_paths([str(img)])
    assert cfgmod.load()["enabled"] is True, "switching a wallpaper on ends the Stop"

    dock = win.pages["dock"]
    dock.chk_enabled.setChecked(True)
    dock.add_items([{"type": "app", "path": sys.executable, "name": "python"}])
    dock.edge.setCurrentIndex(dock.edge.findData("right"))
    d = cfgmod.load()["dock"]
    assert d["enabled"] and d["edge"] == "right" and len(d["items"]) == 1

    widgets = win.pages["widgets"]
    widgets.on_show()
    spec = widgets.add("clock")
    widgets.add("weather")
    assert [w["type"] for w in cfgmod.load()["widgets"]] == ["clock", "weather"] and spec["id"] == "w1"
    widgets.place.setText("Berlin")
    widgets._set_place()
    assert cfgmod.load()["widgets"][1]["options"]["place"] == "Berlin"
    cfg = cfgmod.load()
    cfgmod.save_profile(cfg, "Work")
    cfgmod.save(cfg)
    widgets.on_show()
    assert widgets.profiles.findText("Work") >= 0

    booster = win.pages["booster"]
    booster.chk_wall.setChecked(False)
    assert cfgmod.load()["pause"]["game"] is False


def test_theme_workshop_export_and_import(desktop_window, tmp_path, qapp, monkeypatch):
    win, _ = desktop_window
    wp = win.pages["wallpapers"]
    wp.add_paths([str(picture(tmp_path / "p" / "dawn.png", "#123456"))])
    page = win.pages["themes"]
    page.on_show()
    page.name.setText("Dawn")
    page.author.setText("Tester")
    out = page.export_to(tmp_path / "Dawn.akenotheme")
    assert out.is_file()
    th = page.import_file(out, ask=False)
    assert th is not None and th.name == "Dawn" and th.preview is not None
    assert page.grid.count() == 1
    page.use(th)
    w = cfgmod.wallpaper(cfgmod.load(), wp.name())
    assert w["enabled"] and Path(w["playlist"][0]["path"]).is_relative_to(themes.library_dir())
    errors = []
    from akeno.ui.pages import desktop as dpage
    monkeypatch.setattr(dpage, "info", lambda *a, **k: errors.append(a))
    bad = tmp_path / "bad.akenotheme"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("theme.json", json.dumps({"format": "akeno-theme", "version": 1, "name": "Bad"}))
        z.writestr("media/payload.exe", b"MZ")
    assert page.import_file(bad, ask=False) is None and errors
    assert [x.name for x in themes.library()] == ["Dawn"]
