"""The desktop runtime's own behaviour: renderer exit codes (could not attach, Explorer restarted), "try again",
separate switches for wallpaper, dock, widgets and the runtime, start with Windows, and the tray menu."""

from __future__ import annotations

import pytest

from akeno.desktop import config as cfgmod


class FakeHost:
    def __init__(self):
        self.wanted = True

    def renderer_wanted(self, monitor):
        return self.wanted


@pytest.fixture
def renderer(qapp, monkeypatch):
    from akeno.desktop import host as hostmod
    r = hostmod.Renderer.__new__(hostmod.Renderer)
    hostmod.QObject.__init__(r)
    r.host, r.monitor = FakeHost(), "M"
    r.process = r.channel = None
    r.crashes = hostmod.Crashes()
    r.state, r.error, r.paused, r.config, r.stopping = "running", "", None, "", False
    r.embed_failures, r.losses, r.layer, r.embedded, r.hwnd = 0, [], "", False, 0
    r.exits, r.replaced, r.replaced_shown = [], 0, 0
    later = []
    monkeypatch.setattr(hostmod.QTimer, "singleShot", lambda ms, fn: later.append(ms))
    started = []
    monkeypatch.setattr(r, "start", lambda: started.append(True))
    r.later, r.started = later, started
    return r


def test_a_renderer_that_cannot_attach_is_tried_a_few_times_then_reports(renderer):
    from akeno.desktop import host as hostmod
    renderer.error = "the wallpaper could not be placed behind the desktop icons: no layer"
    for expected in hostmod.EMBED_RETRY_S:
        renderer._finished(hostmod.EMBED_FAILED, None)
        assert renderer.state == "waiting" and renderer.later[-1] == expected * 1000
    renderer._finished(hostmod.EMBED_FAILED, None)
    assert renderer.state == "failed" and "behind the desktop icons" in renderer.error
    assert renderer.crashes.times == [], "not counted as a crash"
    renderer.retry()
    assert renderer.started and renderer.embed_failures == 0 and renderer.error == ""


def test_explorer_restarts_restart_the_wallpaper_quickly(renderer):
    from akeno.desktop import host as hostmod
    renderer._finished(hostmod.DESKTOP_LOST, None)
    assert renderer.state == "waiting" and renderer.later[-1] == hostmod.LOST_DELAY_S * 1000
    for _ in range(hostmod.LOST_GIVE_UP - 1):
        renderer._finished(hostmod.DESKTOP_LOST, None)
    assert renderer.state == "failed" and "Explorer" in renderer.error
    assert renderer.exits == [hostmod.DESKTOP_LOST] * hostmod.LOST_GIVE_UP


def test_exit_codes_are_kept_for_the_diagnostics_but_not_a_requested_stop(renderer):
    """tools/desktop_check.py fails on any renderer that ended by itself; a stop the host asked for is not one."""
    from akeno.desktop import host as hostmod
    renderer._finished(hostmod.EMBED_FAILED, None)
    renderer.stopping = True
    renderer._finished(0, None)
    assert renderer.exits == [hostmod.EMBED_FAILED] and renderer.state == "stopped"


def test_switches_are_separate(akeno_home):
    cfg = cfgmod.load()
    cfg["wallpapers"]["M"] = dict(cfgmod.WALLPAPER_DEFAULTS, enabled=True, playlist=[{"path": "a.png"}])
    cfg["dock"]["enabled"] = True
    cfg["widgets"] = [{"id": "w1", "type": "clock"}]
    assert cfgmod.wallpapers_on(cfg) and cfgmod.widgets_on(cfg) and cfgmod.anything_on(cfg)
    cfgmod.stop_wallpapers(cfg)
    assert not cfgmod.wallpapers_on(cfg) and cfg["dock"]["enabled"] and cfgmod.widgets_on(cfg), \
        "stopping the wallpaper leaves the dock and the widgets"
    assert cfg["wallpapers"]["M"]["playlist"], "and keeps the playlist"
    cfg["widgets_enabled"] = False
    assert not cfgmod.widgets_on(cfg) and cfg["widgets"], "hidden widgets keep their layout"
    cfg["dock"]["enabled"] = False
    assert not cfgmod.anything_on(cfg)
    cfg["dock"]["enabled"] = True
    cfg["enabled"] = False
    assert not cfgmod.anything_on(cfg), "quitting the runtime switches everything off without forgetting it"


class Store:
    def __init__(self):
        self.value = None

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def test_start_with_windows_uses_the_users_run_entry():
    from akeno.desktop import startup
    store = Store()
    assert startup.available(store) and not startup.enabled(store)
    assert startup.set_enabled(True, store)
    assert "--desktop" in store.value and startup.enabled(store)
    store.value = '"C:\\old copy\\AKENO.exe" --desktop'
    assert not startup.enabled(store), "an entry of another copy counts as off"
    startup.set_enabled(False, store)
    assert store.value is None


def test_tray_menu_actions_do_one_thing_each(qapp, akeno_home, monkeypatch):
    from akeno.desktop import host as hostmod
    from akeno.desktop.tray import RuntimeTray
    cfg = cfgmod.load()
    cfg["wallpapers"]["M"] = dict(cfgmod.WALLPAPER_DEFAULTS, enabled=True, playlist=[{"path": "a.png"}])
    cfg["dock"]["enabled"] = True
    cfg["widgets"] = [{"id": "w1", "type": "clock"}]
    cfgmod.save(cfg)
    host = hostmod.DesktopHost(exit_when_idle=False)
    applied = []
    monkeypatch.setattr(host, "apply", lambda: applied.append(True))
    tray = RuntimeTray(host)
    assert tray.a_dock.isChecked() and tray.a_widgets.isChecked() and tray.a_stop.isEnabled()
    tray.a_stop.trigger()
    saved = cfgmod.load()
    assert not cfgmod.wallpapers_on(saved) and saved["dock"]["enabled"] and cfgmod.widgets_on(saved)
    tray.a_widgets.trigger()
    assert cfgmod.load()["widgets_enabled"] is False and cfgmod.load()["widgets"]
    tray.a_pause.setEnabled(True)
    tray.a_pause.trigger()
    assert host.user_paused
    sent = []

    class Ch:
        def send(self, m):
            sent.append(m)
    host.apps = [Ch()]
    tray.open_suite()
    assert sent == [{"cmd": "show"}], "a running suite is brought up, not started a second time"
    quits = []
    monkeypatch.setattr(host, "quit", lambda: quits.append(True))
    tray.quit_runtime()
    assert cfgmod.load()["enabled"] is False and quits
    tray.hide()
    host.deleteLater()


def test_user_pause_is_a_pause_reason():
    from akeno.desktop import pause
    decision = pause.decide(dict(cfgmod.PAUSE_DEFAULTS), pause.State(user_paused=True))
    assert decision.paused and "tray" in str(decision.reasons[0])
