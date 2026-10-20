"""Application entry point."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys

from akeno import APP_NAME, __version__


def setup_logging(debug: bool) -> None:
    from akeno.paths import logs_dir
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.handlers.RotatingFileHandler(logs_dir() / "akeno.log", maxBytes=2_000_000, backupCount=3,
                                              encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setLevel(logging.WARNING)
        root.addHandler(sh)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="akeno", description=APP_NAME)
    parser.add_argument("--minimized", action="store_true", help="start minimised (used by 'Launch with Windows')")
    parser.add_argument("--smoke-test", action="store_true",
                        help="build every page, run startup checks, print a summary and exit (CI)")
    parser.add_argument("--screenshot", metavar="DIR", help="save a PNG of every page into DIR and exit")
    parser.add_argument("--screenshot-size", metavar="WxH", default="1920x1009",
                        help="window size for --screenshot (default: a maximised window on a 1920x1080 screen)")
    parser.add_argument("--screenshot-scale", metavar="FACTOR", type=float, default=None,
                        help="render screenshots at this display scaling (e.g. 1.25, 1.5) to check High-DPI layout")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AKENO.DLSS5Manager")
        except (AttributeError, OSError):
            pass

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    if args.screenshot and args.screenshot_scale:
        os.environ["QT_SCALE_FACTOR"] = str(args.screenshot_scale)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("AKENO")

    from akeno import i18n
    from akeno.services import AppServices
    from akeno.ui import art, theme, widgets
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow

    services = AppServices()
    setup_logging(bool(services.settings.get("debug_logging")))
    logging.getLogger(__name__).info("%s %s starting", APP_NAME, __version__)
    i18n.set_language(services.settings.get("language"))
    widgets.ANIMATIONS["on"] = bool(services.settings.get("animations"))
    app.setFont(theme.ui_font(10))
    app.setStyleSheet(theme.stylesheet(services.settings.get("theme_accent")))
    app.setWindowIcon(QIcon(art.app_icon_pixmap(256)))

    ctl = Controller(services)
    win = MainWindow(ctl)

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable() and not args.smoke_test and not args.screenshot:
        tray = QSystemTrayIcon(QIcon(art.app_icon_pixmap(64)), app)
        tray.setToolTip(APP_NAME)
        tray.activated.connect(lambda reason: (win.showNormal(), win.activateWindow()))
        tray.show()

    labels = {"install": "Installation finished.", "benchmark": "Benchmark finished.", "games": "Game scan finished.",
              "restore": "Restore finished."}

    def busy(key: str, on: bool) -> None:
        if on or key not in labels or tray is None:
            return
        if services.settings.get("notifications") and not win.isActiveWindow():
            tray.showMessage(APP_NAME, i18n.t(labels[key]), QSystemTrayIcon.MessageIcon.Information, 4000)

    ctl.busy_changed.connect(busy)

    def setting(key: str) -> None:
        if key in ("debug_logging", "*"):
            logging.getLogger().setLevel(logging.DEBUG if services.settings.get("debug_logging") else logging.INFO)
        if key in ("animations", "*"):
            widgets.ANIMATIONS["on"] = bool(services.settings.get("animations"))

    ctl.settings_changed.connect(setting)

    def startup() -> None:
        if services.system is None or services.settings.get("rescan_on_start"):
            ctl.scan_hardware()
        if not services.library.games() or services.settings.get("rescan_on_start"):
            # the runtime search also looks in library games, so it runs after the library scan
            ctl.scan_games(on_done=lambda games: ctl.discover_dependencies())
        else:
            ctl.discover_dependencies()
        if services.settings.get("auto_update_compat_db"):
            win.pages["settings"].update_compat()
        if services.settings.get("check_app_updates"):
            win.pages["settings"].check_updates()

    if args.smoke_test:
        return smoke_test(app, ctl, win)
    if args.screenshot:
        return screenshots(app, ctl, win, args.screenshot, args.screenshot_size)
    if args.minimized:
        win.showMinimized()
    else:
        win.show()
    QTimer.singleShot(200, startup)
    return app.exec()


def smoke_test(app, ctl, win) -> int:
    """Non-interactive check used by CI on a real Windows runner: real hardware scan, real library scan,
    every page built and shown. Prints what was found; exits non-zero on any exception."""
    from akeno.ui import workers
    from PySide6.QtCore import QEventLoop, QTimer
    from akeno.ui import assets
    s = ctl.s
    info = s.scan_hardware()
    games = s.library.scan()
    reports = s.discover_dependencies()
    ctl.hardware_changed.emit()
    ctl.games_changed.emit()
    ctl.deps_changed.emit()
    win.show()
    for key in list(win.pages):
        win.go(key)
        loop = QEventLoop()
        QTimer.singleShot(150, loop.quit)
        loop.exec()
    workers.drain(30000)
    lines = [f"AKENO {__version__} smoke test OK",
             f"OS: {info.os_name} {info.os_display_version or ''} build {info.os_build}"]
    for g in info.gpus:
        lines.append(f"GPU: {g.name} vendor={g.vendor.value} dev=0x{(g.device_id or 0):04X} arch={g.architecture} "
                     f"gen={g.generation} vram={g.vram_gb and round(g.vram_gb, 1)}GB driver={g.driver_version} "
                     f"fl={g.feature_level} via={','.join(g.sources)}")
    for d in info.displays:
        lines.append(f"Display: {d.name} {d.width}x{d.height}@{d.refresh_hz}")
    lines.append(f"Detection notes: {info.detection_errors}")
    lines.append(f"Games found: {len(games)}; library errors: {s.library.last_errors}")
    for name, rep in reports.items():
        lines.append(f"Runtime search {name}: {len(rep.searched)} locations, {len(rep.candidates)} candidate(s), "
                     f"{len(rep.verified)} verified, {len(rep.refused)} refused")
        for c in rep.candidates[:5]:
            lines.append(f"  {c.trust.value:10} {c.version or '?':14} {c.source:14} {c.path} — {c.detail}")
    lines.append(assets.summary())
    problems = assets.verify_all()
    lines.append(f"Pages: {', '.join(win.pages)}")
    text = "\n".join(lines)
    from akeno.paths import logs_dir
    (logs_dir() / "smoke-test.txt").write_text(text, encoding="utf-8")   # windowed builds have no console
    if sys.stdout is not None:
        print(text)
    win.close()
    return 3 if problems else 0


def screenshots(app, ctl, win, out_dir: str, size: str = "1920x1009") -> int:
    from PySide6.QtCore import QEventLoop, QTimer
    from akeno.ui import workers
    os.makedirs(out_dir, exist_ok=True)
    w, h = (int(x) for x in size.lower().split("x"))
    win.setMinimumSize(min(w, 1100), min(h, 680))
    win.resize(w, h)
    win.show()
    ctl.s.discover_dependencies()          # pages show the real dependency state of this PC
    ctl.hardware_changed.emit()
    ctl.games_changed.emit()
    ctl.deps_changed.emit()
    for key in list(win.pages):
        win.go(key)
        loop = QEventLoop()
        QTimer.singleShot(300, loop.quit)
        loop.exec()
        workers.drain(30000)
        app.processEvents()
        win.grab().save(os.path.join(out_dir, f"{key}.png"))
    print(f"saved screenshots to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
