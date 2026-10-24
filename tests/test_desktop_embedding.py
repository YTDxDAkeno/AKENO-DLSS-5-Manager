"""The wallpaper embedding against a model of Explorer's windows: the classic WorkerW layout (Windows 10, Windows 11
before 24H2), the raised desktop of Windows 11 24H2+, no Explorer at all, and an Explorer restart.

This checks our algorithm, not Windows: the model follows the window trees Lively Wallpaper and ScreenPlay document
(Spy++ listings in WinDesktopCore.cs / windowsintegration.cpp) and Microsoft's description of the raised desktop.
Whether a real PC behaves like that is checked on a real Windows desktop (tools/desktop_check.py in CI, and the
physical-PC checklist in docs/DESKTOP_STUDIO.md)."""

from __future__ import annotations

import itertools

import pytest

from akeno.desktop import winapi as w

ROOT = 0


class Win:
    def __init__(self, cls, parent=ROOT, style=0, ex=0, rect=(0, 0, 0, 0)):
        self.cls, self.parent, self.style, self.ex, self.rect = cls, parent, style, ex, rect
        self.alpha = None
        self.visible = False
        self.children: list[int] = []        # z-order, top first


class Shell:
    """A model of the desktop windows. ``layout``: classic | raised | broken (the icons are never split off)."""

    def __init__(self, layout="classic", virtual=(-1920, 0, 2560, 1440)):
        self.ids = itertools.count(100)
        self.wins: dict[int, Win] = {}
        self.top: list[int] = []
        self.layout = layout
        self.virtual = virtual               # left, top, right, bottom of the virtual screen
        self.calls: list[tuple] = []
        self.progman = self.new("Progman", ex=w.WS_EX_NOREDIRECTIONBITMAP if layout == "raised" else 0,
                                rect=virtual)
        self.defview = self.new("SHELLDLL_DefView", parent=self.progman, rect=virtual)
        self.workerw = 0
        self.icon_host = self.progman
        if layout == "raised":
            self.workerw = self.new("WorkerW", parent=self.progman, rect=virtual)
        self.app = self.new("Chrome_WidgetWin_1", rect=(0, 0, 800, 600))   # some program on top

    def new(self, cls, parent=ROOT, **kw) -> int:
        hwnd = next(self.ids)
        self.wins[hwnd] = Win(cls, parent, **kw)
        if parent == ROOT:
            self.top.insert(0, hwnd)                        # new top-level windows come on top
        else:
            self.wins[parent].children.append(hwnd)
        return hwnd

    def popup(self) -> int:
        """Our renderer's window as Qt creates it: a frameless top-level popup, not yet shown."""
        return self.new("Qt683QWindowIcon", style=w.WS_POPUP, rect=(0, 0, 640, 480))

    def destroy(self, hwnd) -> None:
        for child in list(self.wins[hwnd].children):
            self.destroy(child)
        win = self.wins.pop(hwnd)
        siblings = self.top if win.parent == ROOT else self.wins[win.parent].children
        siblings.remove(hwnd)

    def _siblings(self, hwnd):
        parent = self.wins[hwnd].parent
        return self.top if parent == ROOT else self.wins[parent].children

    # -- the Win32 facade ------------------------------------------------------------------------------------------
    def find_window(self, cls):
        return next((h for h in self.top if self.wins[h].cls == cls), 0)

    def find_child(self, parent, after, cls):
        siblings = self.top if parent == ROOT else self.wins[parent].children
        start = siblings.index(after) + 1 if after else 0
        return next((h for h in siblings[start:] if self.wins[h].cls == cls), 0)

    def top_level(self):
        return list(self.top)

    def children(self, parent):
        return list(self.wins[parent].children)

    def class_name(self, hwnd):
        return self.wins[hwnd].cls

    def style(self, hwnd):
        return self.wins[hwnd].style

    def ex_style(self, hwnd):
        return self.wins[hwnd].ex

    def set_style(self, hwnd, value):
        self.calls.append(("style", hwnd))
        self.wins[hwnd].style = value

    def set_ex_style(self, hwnd, value):
        self.calls.append(("ex", hwnd))
        self.wins[hwnd].ex = value

    def set_alpha(self, hwnd, alpha):
        self.wins[hwnd].alpha = alpha

    def spawn_worker(self, progman):
        """0x052C: the classic shell moves the icons into a WorkerW and puts a wallpaper WorkerW behind them."""
        self.calls.append(("spawn",))
        if self.layout == "classic" and not self.workerw:
            self.wins[self.progman].children.remove(self.defview)
            host = self.new("WorkerW", rect=self.virtual)
            self.wins[self.defview].parent = host
            self.wins[host].children.append(self.defview)
            self.icon_host = host
            self.workerw = next(self.ids)
            self.wins[self.workerw] = Win("WorkerW", ROOT, rect=self.virtual)
            self.top.insert(self.top.index(host) + 1, self.workerw)    # right behind the icons
            self.top.remove(self.progman)
            self.top.append(self.progman)

    def set_parent(self, hwnd, parent):
        self.calls.append(("parent", hwnd, parent))
        self._siblings(hwnd).remove(hwnd)
        self.wins[hwnd].parent = parent
        (self.top if parent == ROOT else self.wins[parent].children).insert(0, hwnd)
        return True

    def parent(self, hwnd):
        return self.wins[hwnd].parent

    def root(self, hwnd):
        while self.wins[hwnd].parent != ROOT:
            hwnd = self.wins[hwnd].parent
        return hwnd

    def set_pos(self, hwnd, after, x, y, cx, cy, flags):
        self.calls.append(("pos", hwnd, after, flags))
        win = self.wins[hwnd]
        if not flags & w.SWP_NOMOVE:
            ox, oy = (0, 0)
            if win.parent != ROOT:
                pl, pt, _, _ = self.wins[win.parent].rect
                ox, oy = pl, pt
            win.rect = (x + ox, y + oy, x + ox + cx, y + oy + cy)
        if not flags & w.SWP_NOZORDER:
            siblings = self._siblings(hwnd)
            siblings.remove(hwnd)
            if after == w.HWND_BOTTOM:
                siblings.append(hwnd)
            elif after == w.HWND_TOP:
                siblings.insert(0, hwnd)
            else:
                siblings.insert(siblings.index(after) + 1, hwnd)
        if flags & w.SWP_SHOWWINDOW:
            win.visible = True
        return True

    def screen_to_client(self, hwnd, x, y):
        left, top, _, _ = self.wins[hwnd].rect
        return x - left, y - top

    def rect(self, hwnd):
        return self.wins[hwnd].rect

    def is_window(self, hwnd):
        return hwnd in self.wins

    def visible(self, hwnd):
        return self.wins[hwnd].visible

    def show(self, hwnd, cmd):
        self.wins[hwnd].visible = cmd != w.SW_HIDE


LEFT = w.Monitor(r"\\.\DISPLAY2", -1920, 0, 1920, 1080)
MAIN = w.Monitor(r"\\.\DISPLAY1", 0, 0, 2560, 1440, True)


def test_classic_layout_puts_the_wallpaper_into_the_worker_behind_the_icons():
    shell = Shell("classic")
    hwnd = shell.popup()
    result = w.attach(hwnd, MAIN, shell)
    assert result.ok, result.problems
    assert result.mode == "workerw" and shell.parent(hwnd) == shell.workerw
    win = shell.wins[hwnd]
    assert win.style & w.WS_CHILD and not win.style & w.WS_POPUP, "a child window: no focus, no Alt+Tab entry"
    assert win.ex & w.WS_EX_NOACTIVATE and not win.ex & w.WS_EX_LAYERED
    assert win.rect == (0, 0, 2560, 1440), "the monitor's rectangle, from the worker's client coordinates"
    assert shell.top.index(shell.icon_host) < shell.top.index(shell.workerw), "the icons stay in front"
    assert not win.visible, "attach never shows the window: the caller does, after checking"
    w.place(hwnd, MAIN, result.layer, shell)
    assert w.verify(hwnd, MAIN, result.layer, shell) == []


def test_classic_layout_on_a_monitor_left_of_the_main_one():
    shell = Shell("classic")
    hwnd = shell.popup()
    result = w.attach(hwnd, LEFT, shell)
    assert result.ok and shell.wins[hwnd].rect == (-1920, 0, 0, 1080)


def test_raised_desktop_puts_a_layered_child_below_the_icon_view_and_above_windows_wallpaper():
    shell = Shell("raised")
    hwnd = shell.popup()
    result = w.attach(hwnd, MAIN, shell)
    assert result.ok, result.problems
    assert result.mode == "raised" and shell.parent(hwnd) == shell.progman
    kids = shell.children(shell.progman)
    assert kids == [shell.defview, hwnd, shell.workerw], "icons on top, then the wallpaper, Windows' WorkerW last"
    win = shell.wins[hwnd]
    assert win.ex & w.WS_EX_LAYERED and win.alpha == 255, "Microsoft: a WS_EX_LAYERED child, alpha 255"
    assert shell.calls.index(("ex", hwnd)) < shell.calls.index(("parent", hwnd, shell.progman)), \
        "layered before SetParent"
    assert win.style & w.WS_CHILD
    w.place(hwnd, MAIN, result.layer, shell)
    assert w.verify(hwnd, MAIN, result.layer, shell) == []
    assert w.raised_desktop(shell)


def test_raised_desktop_keeps_windows_worker_at_the_bottom():
    shell = Shell("raised")
    other = shell.new("SomeOverlay", parent=shell.progman)      # something else ended up below the WorkerW
    hwnd = shell.popup()
    assert w.attach(hwnd, MAIN, shell).ok
    assert shell.children(shell.progman)[-1] == shell.workerw
    assert other in shell.children(shell.progman)


def test_raised_desktop_alternative_inside_windows_wallpaper_window():
    shell = Shell("raised")
    hwnd = shell.popup()
    result = w.attach(hwnd, MAIN, shell, prefer="workerw")
    assert result.ok and result.mode == "raised-workerw" and shell.parent(hwnd) == shell.workerw
    assert not shell.wins[hwnd].ex & w.WS_EX_LAYERED


def test_no_explorer_means_no_wallpaper_and_the_window_stays_untouched():
    shell = Shell("classic")
    shell.destroy(shell.progman)
    hwnd = shell.popup()
    result = w.attach(hwnd, MAIN, shell)
    assert not result.ok and "Explorer" in result.error
    assert shell.parent(hwnd) == ROOT and shell.wins[hwnd].style == w.WS_POPUP and not shell.wins[hwnd].visible


def test_icons_that_were_not_split_off_are_refused_instead_of_covering_them():
    shell = Shell("broken")                     # 0x052C did nothing: the icons still live in Progman
    hwnd = shell.popup()
    result = w.attach(hwnd, MAIN, shell)
    assert not result.ok, "no safe place behind the icons: refused, never a window in front of them"
    assert not any(c[0] == "parent" for c in shell.calls)


def test_verify_notices_every_wrong_placement():
    shell = Shell("raised")
    hwnd = shell.popup()
    result = w.attach(hwnd, MAIN, shell)
    layer = result.layer
    shell.set_pos(hwnd, w.HWND_TOP, 0, 0, 0, 0, w.SWP_NOMOVE | w.SWP_NOSIZE)   # above the icons
    assert "not below the desktop icons" in " ".join(w.verify(hwnd, MAIN, layer, shell))
    shell.set_pos(hwnd, w.HWND_BOTTOM, 0, 0, 0, 0, w.SWP_NOMOVE | w.SWP_NOSIZE)
    assert "below Windows' own wallpaper" in " ".join(w.verify(hwnd, MAIN, layer, shell))
    w.place(hwnd, MAIN, layer, shell)
    assert w.verify(hwnd, MAIN, layer, shell) == []
    shell.set_pos(hwnd, 0, 0, 0, 100, 100, w.SWP_NOZORDER)
    assert "does not cover the monitor" in " ".join(w.verify(hwnd, MAIN, layer, shell))


def test_explorer_restart_is_noticed():
    shell = Shell("classic")
    hwnd = shell.popup()
    layer = w.attach(hwnd, MAIN, shell).layer
    assert w.still_attached(hwnd, layer, shell)
    shell.destroy(shell.workerw)                # Explorer ends: its windows go, and with them our child window
    assert not w.still_attached(hwnd, layer, shell)
    assert w.verify(hwnd, MAIN, layer, shell) == ["the desktop window is gone (Explorer restarted?)"]


def test_hide_never_detaches():
    shell = Shell("classic")
    hwnd = shell.popup()
    layer = w.attach(hwnd, MAIN, shell).layer
    w.place(hwnd, MAIN, layer, shell)
    w.hide(hwnd, shell)
    assert not shell.wins[hwnd].visible and shell.parent(hwnd) == layer.parent, \
        "closing hides the window where it is; it never becomes a top-level window over the desktop"


class FakeScreen:
    def __init__(self, name, x, y, width, height, dpr=1.0):
        self._name, self.rect, self.dpr = name, (x, y, width, height), dpr

    def name(self):
        return self._name

    def geometry(self):
        from PySide6.QtCore import QRect
        return QRect(*self.rect)

    def devicePixelRatio(self):
        return self.dpr


def real_monitor(monkeypatch) -> str:
    """Win32 knows the main screen as DISPLAY1 while Qt 6 names it by model (on the CI runner: "HyperVMonitor")."""
    from PySide6.QtGui import QGuiApplication
    screen = QGuiApplication.primaryScreen()
    x, y, width, height = w.native_rect(screen)
    monkeypatch.setattr(w, "monitors", lambda: [w.Monitor(r"\\.\DISPLAY1", x, y, width, height, True)])
    return screen.name()


def test_qt_screens_are_matched_to_windows_monitors_by_position_not_name():
    d1, d2, d3 = r"\\.\DISPLAY1", r"\\.\DISPLAY2", r"\\.\DISPLAY3"
    mons = [w.Monitor(d1, 0, 0, 3840, 2160, True), w.Monitor(d2, 3840, 0, 1920, 1080),
            w.Monitor(d3, -2560, -200, 2560, 1440)]
    screens = [FakeScreen("DELL U3223QE", 0, 0, 2560, 1440, 1.5), FakeScreen("HyperVMonitor", 3840, 0, 1920, 1080),
               FakeScreen("LG ULTRAGEAR", -2560, -200, 1707, 960, 1.5)]
    assert w.monitor_of("DELL U3223QE", screens, mons).name == d1, "4K at 150 %: Qt size 2560x1440"
    assert w.monitor_of("HyperVMonitor", screens, mons).name == d2
    assert w.monitor_of("LG ULTRAGEAR", screens, mons).name == d3, "left of and above the main monitor"
    assert w.monitor_of("unplugged", screens, mons) is None
    assert w.monitor_of(d2, [FakeScreen(d2, 1, 1, 10, 10)], mons).name == d2, "Qt builds that use device names"
    assert w.match_monitor("x", (3900, 10, 100, 100), mons).name == d2, "otherwise the monitor under its centre"


@pytest.mark.parametrize("layout", ["classic", "raised"])
def test_find_layer_describes_the_desktop(layout):
    shell = Shell(layout)
    layer = w.find_layer(shell)
    assert layer is not None and layer.defview == shell.defview
    assert layer.mode == ("raised" if layout == "raised" else "workerw")


def test_renderer_refuses_to_show_a_wallpaper_it_could_not_attach(qapp, tmp_path, monkeypatch):
    """The safety rule: if attaching fails, the window is never shown and the error is reported."""
    from akeno.desktop import config as cfgmod
    from akeno.desktop import renderer
    from PySide6.QtGui import QColor, QGuiApplication, QImage
    img = QImage(64, 36, QImage.Format.Format_RGB32)
    img.fill(QColor("#ff0000"))
    img.save(str(tmp_path / "a.png"))
    cfg = cfgmod.load()
    cfg["wallpapers"][QGuiApplication.primaryScreen().name()] = dict(cfgmod.WALLPAPER_DEFAULTS, enabled=True,
                                  playlist=[{"path": str(tmp_path / "a.png"), "kind": "image"}])
    file = tmp_path / "desktop.json"
    cfgmod.save(cfg, file)
    monkeypatch.setattr(w, "WINDOWS", True)
    monkeypatch.setattr(w, "virtual_desktop_id", lambda: None)
    name = real_monitor(monkeypatch)
    monkeypatch.setattr(w, "attach", lambda *a, **k: w.Embedding(False, problems=["no desktop layer"]))
    monkeypatch.setattr(w, "hide", lambda hwnd, api=None: None)
    win = renderer.WallpaperWindow(name, file)
    shown = []
    monkeypatch.setattr(win, "show", lambda: shown.append(True))
    assert not win.embed(tries=2, delay=0)
    assert not shown and not win.isVisible() and not win.embedded
    assert "behind the desktop icons" in win.error and "no desktop layer" in win.error
    win.close()


def test_renderer_hides_when_verification_fails_after_showing(qapp, tmp_path, monkeypatch):
    from akeno.desktop import config as cfgmod
    from akeno.desktop import renderer
    file = tmp_path / "desktop.json"
    cfgmod.save(cfgmod.load(), file)
    shell = Shell("raised")
    monkeypatch.setattr(w, "WINDOWS", True)
    monkeypatch.setattr(w, "virtual_desktop_id", lambda: None)
    name = real_monitor(monkeypatch)
    layer = w.find_layer(shell)
    monkeypatch.setattr(w, "attach", lambda *a, **k: w.Embedding(True, "raised", [], layer))
    monkeypatch.setattr(w, "place", lambda *a, **k: None)
    monkeypatch.setattr(w, "verify", lambda *a, **k: ["the wallpaper is not below the desktop icons"])
    hidden = []
    monkeypatch.setattr(w, "hide", lambda hwnd, api=None: hidden.append(hwnd))
    win = renderer.WallpaperWindow(name, file)
    monkeypatch.setattr(win, "show", lambda: None)
    assert not win.embed(tries=1, delay=0)
    assert hidden and not win.embedded and "not below the desktop icons" in win.error
    win.close()


def test_renderer_leaves_when_explorer_restarts(qapp, tmp_path, monkeypatch):
    from akeno.desktop import config as cfgmod
    from akeno.desktop import renderer
    file = tmp_path / "desktop.json"
    cfgmod.save(cfgmod.load(), file)
    monkeypatch.setattr(w, "WINDOWS", True)
    monkeypatch.setattr(w, "virtual_desktop_id", lambda: None)
    name = real_monitor(monkeypatch)
    monkeypatch.setattr(w, "hide", lambda hwnd, api=None: None)
    monkeypatch.setattr(w, "still_attached", lambda *a, **k: False)
    win = renderer.WallpaperWindow(name, file)
    win.layer = w.Layer("workerw", 1, 2, 3, 1)
    codes = []
    win.lost = codes.append
    win._watch()
    assert codes == [renderer.DESKTOP_LOST]
    win.close()
