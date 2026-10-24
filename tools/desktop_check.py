"""Desktop Studio on a real Windows desktop (the CI runner, or any PC): runs the desktop host with a picture and then
a web wallpaper, rendered by the given AKENO.exe (or the source), and reports what Windows reports.

    python tools/desktop_check.py --exe dist/AKENO/AKENO.exe --out screenshots/desktop-studio

It fails when a renderer does not start or reports an error (a missing Qt module in the build, for example).
Whether the wallpaper really sits behind the desktop icons is reported, not required: a CI runner may have no
Explorer shell. Nothing here measures or claims GPU performance.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", help="the packaged AKENO.exe (default: run the renderers from source)")
    ap.add_argument("--out", default="desktop-check")
    ap.add_argument("--timeout", type=float, default=45)
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
    screen = QGuiApplication.primaryScreen()
    name = screen.name()
    report: dict = {"monitor": name, "platform": sys.platform, "exe": args.exe or "source"}

    def configure(item: dict) -> None:
        cfg = cfgmod.load()
        cfg["wallpapers"][name] = dict(cfgmod.WALLPAPER_DEFAULTS, enabled=True, playlist=[item])
        cfg["pause"] = dict(cfg["pause"], covered=False, fullscreen=False, cpu_percent=0)
        cfgmod.save(cfg)

    def wait(condition, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if condition():
                return True
            time.sleep(0.05)
        return condition()

    spawn = (lambda *a: (str(Path(args.exe).resolve()), list(a))) if args.exe else hostmod.self_command
    configure({"path": str(picture), "kind": "image"})
    host = hostmod.DesktopHost(spawn=spawn, license_ok=lambda: True, exit_when_idle=False)
    ok = host.start()
    report["host_started"] = ok
    failures = [] if ok else ["the desktop host did not start"]
    phases = {}
    try:
        for phase, item in (("picture", {"path": str(picture), "kind": "image"}),
                            ("web", {"path": str(web), "kind": "web"})):
            if phase == "web":
                renderer = host.renderers.get(name)
                if renderer is not None:
                    renderer.state = "starting"
                configure(item)
                host.reload()
            renderer = host.renderers.get(name)
            running = renderer is not None and wait(lambda: renderer.state == "running", args.timeout)
            wait(lambda: False, 3)                           # let the first frames draw
            shot = screen.grabWindow(0)
            shot.save(str(out / f"desktop-{phase}.png"))
            image = shot.toImage()
            target = QColor("#4E31D5" if phase == "picture" else "#12C7FF")
            samples = [(x, y) for x in range(5, 100, 10) for y in range(5, 100, 10)]
            hits = 0
            for fx, fy in samples:
                c = image.pixelColor(int(image.width() * fx / 100), int(image.height() * fy / 100))
                if abs(c.red() - target.red()) + abs(c.green() - target.green()) + abs(c.blue() - target.blue()) < 40:
                    hits += 1
            phases[phase] = {"state": renderer.state if renderer else "missing",
                             "error": renderer.error if renderer else "", "screen_share_with_wallpaper_colour":
                             round(hits / len(samples), 2)}
            if not running:
                failures.append(f"{phase}: renderer {phases[phase]['state']}")
            elif renderer.error:
                failures.append(f"{phase}: {renderer.error}")
        parent = winapi.desktop_parent()
        report["desktop_parent"] = None if parent is None else (
            "Progman child below SHELLDLL_DefView (Windows 11 24H2+)" if parent[1] else "WorkerW behind the icons")
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
        report["status"] = host.status()
    finally:
        host.quit()
        wait(lambda: all(r.process is None for r in host.renderers.values()), 10)
        for r in host.renderers.values():
            if r.process is not None:
                r.process.kill()
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


if __name__ == "__main__":
    raise SystemExit(main())
