"""Desktop Studio on a real Windows desktop (the CI runner, or any PC): runs the desktop host with a picture, a video
and a web wallpaper, rendered by the given AKENO.exe (or the source), and measures from outside how each wallpaper
window sits in Explorer's window tree (winapi.inspect).

    python tools/desktop_check.py --exe dist/AKENO/AKENO.exe --out screenshots/desktop-studio

It fails when a renderer does not start or reports an error, and, when Windows offers a desktop layer (Explorer
runs), when a wallpaper window
* is not a child behind the desktop icons (winapi.verify),
* is a top-level window (it would show in Alt+Tab and could cover the desktop), or
* receives clicks at any of 25 points of the monitor (the icon view or programs must get them).
Throughout the whole run, every 50 ms, it also fails when
* any window of a renderer process is a visible top-level window (it would cover the desktop and the icons), or
* a renderer ends by itself (an unexpected restart; only the Explorer restart below may end one).
Without an Explorer shell the renderer must refuse to show anything, and that is what is checked then.

Phases: picture -> video -> web (the same renderer reloads; Qt replaces the native window when the page starts
drawing) -> picture again -> a renderer that starts with the web wallpaper -> Windows' own wallpaper redrawn
(refresh_wallpaper, as a renderer does when it ends) -> the suite connects and disconnects -> Explorer restarts.

A CI runner is Windows Server with the classic desktop layout; Windows 11 24H2's raised desktop is not covered here.
Nothing here measures or claims GPU performance.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SETTLE_S = 3                # first frames drawn
WEB_SETTLE_S = 12           # a web page starts drawing (and Qt replaces the native window) a few seconds in


def visible_top_level_of(pids: set[int]) -> list[int]:
    """Visible top-level windows that belong to these processes (a wallpaper window must never be one)."""
    if sys.platform != "win32" or not pids:
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    found: list[int] = []

    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(proc, 0)
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", help="the packaged AKENO.exe (default: run the renderers from source)")
    ap.add_argument("--out", default="desktop-check")
    ap.add_argument("--timeout", type=float, default=45)
    ap.add_argument("--no-explorer-restart", action="store_true", help="leave Explorer alone (a PC in use)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("AKENO_HOME", tempfile.mkdtemp(prefix="akeno-desktop-check-"))
    os.environ.setdefault("AKENO_DESKTOP_SUFFIX", "-check")

    from PySide6.QtGui import QColor, QGuiApplication, QImage
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    from akeno.desktop import config as cfgmod
    from akeno.desktop import host as hostmod
    from akeno.desktop import winapi
    from akeno.desktop.audio import AudioMeter

    home = Path(os.environ["AKENO_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    picture = home / "check.png"
    img = QImage(640, 360, QImage.Format.Format_RGB32)
    img.fill(QColor("#4E31D5"))
    img.save(str(picture))
    web = home / "web-check"
    web.mkdir(exist_ok=True)
    (web / "index.html").write_text("<!doctype html><html><body style='margin:0;background:#12C7FF'>"
                                    "<canvas id=c></canvas><script src='app.js'></script></body></html>",
                                    encoding="utf-8")
    (web / "app.js").write_text("var g=document.getElementById('c').getContext('webgl');"
                                "document.title=g?'webgl':'no-webgl';", encoding="utf-8")
    video = ROOT / "tests" / "fixtures" / "media" / "sample.mp4"
    screen = QGuiApplication.primaryScreen()
    name = winapi.screen_key(screen, QGuiApplication.screens())
    mon = winapi.monitor_of(name, QGuiApplication.screens())
    report: dict = {"monitor": name, "platform": sys.platform, "exe": args.exe or "source",
                    "desktop": winapi.describe(),
                    "monitor_match": {"qt_name": name, "qt_geometry": list(screen.geometry().getRect()),
                                      "device_pixel_ratio": screen.devicePixelRatio(),
                                      "qt_native_rect": list(winapi.native_rect(screen)),
                                      "win32": dataclasses.asdict(mon) if mon else None,
                                      "win32_monitors": [dataclasses.asdict(m) for m in winapi.monitors()]}}
    shell = report["desktop"].get("layer") is not None
    failures: list[str] = [] if mon is not None or sys.platform != "win32" else \
        [f"the Qt screen {name} matches no Windows monitor"]
    pids: set[int] = set()
    phase_now = ["start"]
    uncovered: dict[str, dict] = {}

    def configure(item: dict) -> None:
        cfg = cfgmod.load()
        cfg["wallpapers"][name] = dict(cfgmod.WALLPAPER_DEFAULTS, enabled=True, playlist=[item])
        cfg["pause"] = dict(cfg["pause"], covered=False, fullscreen=False, cpu_percent=0)
        cfgmod.save(cfg)

    def guard() -> None:
        """Every renderer process ever started is watched: none of its windows may be a visible top-level one."""
        for r in host.renderers.values():
            if r.process is not None and r.process.processId():
                pids.add(int(r.process.processId()))
        for hwnd in visible_top_level_of(pids):
            seen = uncovered.setdefault(phase_now[0], {"samples": 0, "classes": []})
            seen["samples"] += 1
            cls = winapi.Win32().class_name(hwnd)
            if cls not in seen["classes"]:
                seen["classes"].append(cls)
            winapi.hide(hwnd)                     # measured; the next samples show whether it comes back

    def wait(condition, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            guard()
            if condition():
                return True
            time.sleep(0.05)
        return condition()

    def measure(phase: str, renderer, colour: str | None, exits_before: int, settle: float) -> dict:
        """The renderer's state and window, as Windows sees it, after ``settle`` seconds of running."""
        settled = renderer is not None and wait(lambda: renderer.state in ("running", "failed"), args.timeout)
        running = settled and renderer.state == "running"
        windows = set()
        wait(lambda: windows.add(renderer.hwnd) if renderer is not None else False, settle)
        shot = screen.grabWindow(0)
        shot.save(str(out / f"desktop-{phase}.png"))
        result = {"state": renderer.state if renderer else "missing", "error": renderer.error if renderer else "",
                  "layer": renderer.layer if renderer else "", "embedded": renderer.embedded if renderer else False,
                  "windows_reported": len(windows - {0}),
                  "native_window_replaced": renderer.replaced if renderer else 0,
                  "replaced_window_already_visible": renderer.replaced_shown if renderer else 0,
                  "renderer_exits": renderer.exits[exits_before:] if renderer else []}
        if colour:
            image = shot.toImage()
            target = QColor(colour)
            samples = [(x, y) for x in range(5, 100, 10) for y in range(5, 100, 10)]
            hits = 0
            for fx, fy in samples:
                c = image.pixelColor(int(image.width() * fx / 100), int(image.height() * fy / 100))
                if abs(c.red() - target.red()) + abs(c.green() - target.green()) + \
                        abs(c.blue() - target.blue()) < 40:
                    hits += 1
            result["screen_share_with_wallpaper_colour"] = round(hits / len(samples), 2)
        if renderer is not None and renderer.hwnd and mon is not None:
            result["window"] = winapi.inspect(renderer.hwnd, mon)
        if not shell:
            if running and renderer.embedded:
                failures.append(f"{phase}: shown although Windows offers no desktop layer")
            return result
        if not running:
            failures.append(f"{phase}: renderer {result['state']} {result['error']}")
            return result
        if renderer.error:
            failures.append(f"{phase}: {renderer.error}")
        if result["renderer_exits"]:
            failures.append(f"{phase}: the renderer ended by itself (exit {result['renderer_exits']}) and was "
                            "restarted")
        if result["replaced_window_already_visible"]:
            failures.append(f"{phase}: Qt's replacement window was already visible before it went behind the icons")
        win = result.get("window") or {}
        if not win.get("exists"):
            failures.append(f"{phase}: the reported wallpaper window does not exist")
        if win.get("problems"):
            failures.append(f"{phase}: {'; '.join(win['problems'])}")
        if win.get("top_level"):
            failures.append(f"{phase}: the wallpaper is a top-level window")
        if win.get("clicks_reach_wallpaper"):
            failures.append(f"{phase}: the wallpaper received clicks at {win['clicks_reach_wallpaper']} points")
        return result

    def switch(phase: str, item: dict, colour: str | None, settle: float = SETTLE_S) -> None:
        """The same renderer process loads another wallpaper (the host sends "reload")."""
        phase_now[0] = phase
        renderer = host.renderers.get(name)
        exits = len(renderer.exits) if renderer else 0
        if renderer is not None:
            renderer.state = "starting"
        configure(item)
        host.reload()
        phases[phase] = measure(phase, host.renderers.get(name), colour, exits, settle)

    spawn = (lambda *a: (str(Path(args.exe).resolve()), list(a))) if args.exe else hostmod.self_command
    configure({"path": str(picture), "kind": "image"})
    host = hostmod.DesktopHost(spawn=spawn, license_ok=lambda: True, exit_when_idle=False)
    ok = host.start()
    report["host_started"] = ok
    if not ok:
        failures.append("the desktop host did not start")
    phases: dict = {}
    try:
        phase_now[0] = "picture"
        phases["picture"] = measure("picture", host.renderers.get(name), "#4E31D5", 0, SETTLE_S)
        switch("video", {"path": str(video), "kind": "video"}, None)
        switch("web", {"path": str(web), "kind": "web"}, "#12C7FF", WEB_SETTLE_S)
        switch("picture-again", {"path": str(picture), "kind": "image"}, "#4E31D5")
        configure({"path": str(web), "kind": "web"})

        # a renderer process that starts with the web wallpaper (after a reboot, a crash or an Explorer restart)
        phase_now[0] = "web-fresh"
        host.cfg = cfgmod.load()
        renderer = host.renderers.get(name)
        if renderer is not None:
            renderer.config = json.dumps(cfgmod.wallpaper(host.cfg, name), sort_keys=True)
            renderer.stop()
            wait(lambda: renderer.process is None, 10)
            exits = len(renderer.exits)
            renderer.start()
            phases["web-fresh"] = measure("web-fresh", renderer, "#12C7FF", exits, WEB_SETTLE_S)

        # Windows' own wallpaper is redrawn, as every renderer does when it ends: the others must stay attached
        phase_now[0] = "refresh-wallpaper"
        renderer = host.renderers.get(name)
        if renderer is not None and shell:
            before = winapi.find_layer()
            exits = len(renderer.exits)
            winapi.refresh_wallpaper()
            wait(lambda: False, 5)                        # two of the renderer's checks
            after = winapi.find_layer()
            result = measure("refresh-wallpaper", renderer, None, exits, 0)
            result["layer_window_kept"] = bool(before and after and before.parent == after.parent)
            phases["refresh-wallpaper"] = result

        # the suite connects, reports no game, and closes: the runtime and its wallpaper keep running
        phase_now[0] = "suite-closes"
        renderer = host.renderers.get(name)
        if renderer is not None:
            from akeno.desktop.client import DesktopClient
            pid = renderer.process.processId() if renderer.process else 0
            exits = len(renderer.exits)
            client = DesktopClient(host.server.name)
            connected = client.running()
            client.game(False)
            wait(lambda: bool(host.apps), 5)
            client.channel.close()
            client.channel = None
            wait(lambda: not host.apps, 5)
            result = measure("suite-closes", renderer, None, exits, 3)
            result["suite_connected"] = connected
            result["same_renderer_process"] = bool(renderer.process) and renderer.process.processId() == pid
            if not connected or not result["same_renderer_process"]:
                failures.append("suite-closes: the wallpaper did not keep running when the suite disconnected")
            phases["suite-closes"] = result

        report["fullscreen_app"] = winapi.fullscreen_app()
        report["remote_session"] = winapi.remote_session()
        report["on_battery"] = winapi.on_battery()
        report["virtual_desktop"] = winapi.virtual_desktop_id()
        report["audio_meter"] = AudioMeter().peak() is not None
        from akeno.desktop.dock import DockWindow
        dock = DockWindow(dict(cfgmod.DOCK_DEFAULTS, enabled=True), lambda c: None)
        dock.show()
        app.processEvents()
        report["dock_acrylic"] = winapi.set_acrylic(int(dock.winId()))
        dock.close()

        if shell and sys.platform == "win32" and not args.no_explorer_restart:
            phases["explorer-restart"] = explorer_restart(host.renderers.get(name), wait, measure, failures,
                                                          phase_now)
        report["status"] = host.status()
    finally:
        phase_now[0] = "quit"
        host.quit()
        wait(lambda: all(r.process is None for r in host.renderers.values()), 10)
        for r in host.renderers.values():
            if r.process is not None:
                r.process.kill()
    for phase, seen in uncovered.items():
        failures.append(f"{phase}: a renderer window was a visible top-level window ({seen['samples']} samples of "
                        f"50 ms; window class {', '.join(seen['classes'])})")
    report["renderer_processes"] = len(pids)
    report["phases"] = phases
    report["failures"] = failures
    text = json.dumps(report, indent=1, default=str)
    (out / "desktop-check.json").write_text(text, encoding="utf-8")
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Desktop Studio on the runner desktop\n```json\n" + text + "\n```\n")
    return 1 if failures else 0


def explorer_restart(renderer, wait, measure, failures: list, phase_now: list) -> dict:
    """Explorer ends and starts again: the wallpaper leaves with Explorer's windows, never shows while there is no
    desktop layer, and comes back behind the new icons. Explorer is always started again."""
    from akeno.desktop import winapi
    api = winapi.Win32()
    if renderer is None:
        return {"measured": False}
    phase_now[0] = "explorer-down"
    exits = len(renderer.exits)
    subprocess.run(["taskkill", "/F", "/IM", "explorer.exe"], capture_output=True)
    result: dict = {"measured": True,
                    "explorer_ended": wait(lambda: not api.find_window("Progman"), 15)}
    result["renderer_noticed"] = wait(lambda: len(renderer.exits) > exits, 15)
    wait(lambda: False, 4)                                   # restarts while there is no desktop: nothing shows
    result["shown_without_explorer"] = renderer.state == "running" and renderer.embedded
    phase_now[0] = "explorer-up"
    subprocess.Popen(["explorer.exe"], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    result["explorer_back"] = wait(lambda: bool(api.find_window("Progman")) and winapi.find_layer() is not None, 60)
    if not result["explorer_back"]:
        result["note"] = "Explorer did not come back as the shell on this machine; nothing more is measured"
        return result
    result["recovered"] = wait(lambda: renderer.state == "running" and renderer.embedded, 75)
    result["exit_codes_during_the_restart"] = renderer.exits[exits:]
    result["after"] = measure("explorer-restart", renderer, None, len(renderer.exits), 3)
    if not result["explorer_ended"]:
        failures.append("explorer-restart: Explorer did not end")
    if not result["renderer_noticed"]:
        failures.append("explorer-restart: the renderer did not notice that Explorer's windows were gone")
    if result["shown_without_explorer"]:
        failures.append("explorer-restart: a wallpaper ran while Explorer offered no desktop layer")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
