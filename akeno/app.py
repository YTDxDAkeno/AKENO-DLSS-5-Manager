"""Application entry point."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import time

from akeno import APP_NAME, __version__


def setup_logging(debug: bool, name: str = "akeno.log") -> None:
    from akeno.paths import logs_dir
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.handlers.RotatingFileHandler(logs_dir() / name, maxBytes=2_000_000, backupCount=3,
                                              encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setLevel(logging.WARNING)
        root.addHandler(sh)


def desktop_process(argv: list[str]) -> int | None:
    """Desktop Studio runs in processes of its own: the host (``--desktop``) and one wallpaper renderer per
    monitor (``--wallpaper-renderer``); so does the updater (``--apply-update``, ``--recover-update``). They start
    before anything of the suite's window is loaded."""
    import re
    if "--apply-update" in argv[:-1] or "--recover-update" in argv[:-1]:
        from akeno.updater import apply
        return apply.main(argv)
    if "--wallpaper-renderer" in argv:
        monitor = argv[argv.index("--monitor") + 1] if "--monitor" in argv[:-1] else "monitor"
        setup_logging(False, f"wallpaper-{re.sub(r'[^A-Za-z0-9]+', '', monitor)[-12:] or 'monitor'}.log")
        from akeno.desktop import renderer
        return renderer.main(argv)
    if "--desktop" in argv:
        setup_logging(False, "desktop.log")
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AKENO.Desktop")
            except (AttributeError, OSError):
                pass
        from akeno.desktop import host
        return host.main(argv)
    return None


def run(argv: list[str] | None = None) -> int:
    """The program's entry point: ``main``, with a crash while starting reported to the user (``startguard``)."""
    try:
        return main(argv)
    except SystemExit:
        raise
    except Exception as exc:                     # noqa: BLE001 (the last line of defence of the start)
        from akeno import startguard
        return startguard.crashed(exc)


def main(argv: list[str] | None = None) -> int:
    child = desktop_process(sys.argv[1:] if argv is None else list(argv))
    if child is not None:
        return child
    if getattr(sys, "frozen", False):
        # an update of this program folder was interrupted (power cut, restart): the staged updater finishes it
        # and starts AKENO again; this process must not hold the folder meanwhile
        from akeno.paths import sub
        from akeno.updater import apply
        from akeno.updater.service import install_dir
        if apply.handoff_recovery(install_dir(), sub("updates")):
            return 0
    parser = argparse.ArgumentParser(prog="akeno", description=APP_NAME)
    parser.add_argument("--minimized", action="store_true", help="start minimised (used by 'Launch with Windows')")
    parser.add_argument("--smoke-test", action="store_true",
                        help="build every page, run startup checks, print a summary and exit (CI)")
    parser.add_argument("--screenshot", metavar="DIR", help="save a PNG of every page into DIR and exit")
    parser.add_argument("--screenshot-size", metavar="WxH", default="1920x1009",
                        help="window size for --screenshot (default: a maximised window on a 1920x1080 screen)")
    parser.add_argument("--screenshot-scale", metavar="FACTOR", type=float, default=None,
                        help="render screenshots at this display scaling (e.g. 1.25, 1.5) to check High-DPI layout")
    parser.add_argument("--safe-mode", action="store_true",
                        help="start without showing the results the System tools saved on this PC (they stay)")
    parser.add_argument("--check-start", action="store_true",
                        help="build the suite with this PC's saved data, open every page, report and exit")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AKENO.GamingSuite")
        except (AttributeError, OSError):
            pass

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtWidgets import QApplication

    if args.screenshot and args.screenshot_scale:
        os.environ["QT_SCALE_FACTOR"] = str(args.screenshot_scale)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("AKENO")
    from akeno.ui import interaction
    interaction.install(app)           # the wheel scrolls pages; it never changes a control by passing over it

    from akeno import i18n
    from akeno.core.db import Database
    from akeno.core.settings import Settings
    from akeno.licensing import manager as license_manager
    from akeno.paths import db_path
    from akeno.ui import art, theme, widgets

    # Only what the welcome screen needs is read before the license check: language, theme and animations.
    prefs = Settings(Database(db_path()))
    setup_logging(bool(prefs.get("debug_logging")))
    logging.getLogger(__name__).info("%s %s starting", APP_NAME, __version__)
    i18n.set_language(prefs.get("language"))
    widgets.ANIMATIONS["on"] = bool(prefs.get("animations"))
    theme.apply(app, prefs.get("theme_accent"))
    app.setWindowIcon(QIcon(art.app_icon_pixmap(256)))
    lic = license_manager()

    if args.check_start:
        # the start check of an update or a repair: this PC's saved data, every page, nothing scanned or changed
        args.smoke_test = True                        # build_suite: no tray icon, no start-up scans
        return check_start(app, *build_suite(app, args, lic))
    if args.smoke_test or args.screenshot:
        # CI checks of a build: every window is created, nothing stays open (no use without a license)
        ctl, win = build_suite(app, args, lic)
        from akeno.ui.license_gate import LicenseGate
        gate = LicenseGate(lic, probe=False)          # build checks never contact the license server
        if args.smoke_test:
            return smoke_test(app, ctl, win, gate)
        return screenshots(app, ctl, win, args.screenshot, args.screenshot_size, gate)

    from akeno import startguard
    from akeno.ui import system_ui
    failed_starts = startguard.begin(__version__)
    system_ui.SAFE_MODE["on"] = bool(args.safe_mode or failed_starts)
    if failed_starts:
        logging.getLogger(__name__).warning("the last start of %s did not finish: safe mode", __version__)

    from akeno.ui.shell import SuiteShell
    holder: dict = {}

    def services():
        # one AppServices for the suite and the welcome screen's "Restore game files" (which only undoes)
        if "services" not in holder:
            from akeno.services import AppServices
            holder["services"] = AppServices()
        return holder["services"]

    def build():
        ctl, win = build_suite(app, args, lic, services())
        holder["ctl"], holder["win"] = ctl, win
        if system_ui.SAFE_MODE["on"]:
            from PySide6.QtCore import QTimer as _Timer
            _Timer.singleShot(800, lambda: safe_mode_notice(win, failed_starts))
        return win

    def suite_check():
        # the update's start check (health_report): the suite is built with this PC's saved data, also when the
        # welcome screen is shown (no license yet) — hidden, never usable, closed right away
        if holder.get("win") is None:
            check_args = argparse.Namespace(**{**vars(args), "smoke_test": True, "screenshot": None})
            _ctl, win = build_suite(app, check_args, lic, services())
            win.close()
            win.deleteLater()
        return "; ".join(system_ui.STORED_FAILURES) or True
    from akeno.updater import service as update_service
    update_service.SUITE_CHECK = suite_check

    try:
        # programs a Network Gaming Mode paused run again, whatever the license state
        from akeno.recovery import resume_paused_processes
        resume_paused_processes()
    except Exception as exc:                     # never keeps AKENO from starting
        logging.getLogger(__name__).warning("resume paused programs: %s", exc)

    def language(lang: str) -> None:
        prefs.set("language", lang)
        i18n.set_language(lang)

    shell = SuiteShell(lic, build, language, busy=lambda: bool(holder.get("ctl") and holder["ctl"].busy),
                       recovery=services)
    shell.minimized = args.minimized
    shell.start()
    from PySide6.QtCore import QTimer
    from akeno.updater.service import write_health_report
    QTimer.singleShot(3000, write_health_report)       # an update that just ran: this version's own start check
    QTimer.singleShot(startguard.STABLE_SECONDS * 1000, startguard.done)   # the start worked
    return app.exec()


def safe_mode_notice(win, failed_starts: int) -> None:
    """After a start that did not finish: say what safe mode leaves out and open the way out (Updates)."""
    from akeno.i18n import t
    from akeno.ui.widgets import info
    page = win.pages.get("updates")
    if page is not None:
        win.go("updates")
    info(win, t("Safe mode"),
         t("The last start of AKENO did not finish." if failed_starts else "AKENO was started in safe mode."),
         t("The results the System tools saved on this PC are not shown this time; they stay stored, and so do "
           "your games, mods, backups, settings and license. Look for a corrected version here, or go back to "
           "the previous version. The next normal start shows everything again."))
    if page is not None and hasattr(page, "check"):
        page.check(quiet=True)


def check_start(app, ctl, win) -> int:
    """``--check-start``: every page built with the saved data of this PC and shown once; nothing is scanned.
    Exit code 0, or 4 when a saved result or a page could not be shown (printed and in ``check-start.txt``)."""
    from PySide6.QtCore import QEventLoop, QTimer
    from akeno.ui import system_ui, workers
    win.show()
    for key in list(win.pages):
        win.go(key)
        loop = QEventLoop()
        QTimer.singleShot(150, loop.quit)
        loop.exec()
    workers.drain(30000)
    failures = list(system_ui.STORED_FAILURES)
    lines = [f"AKENO {__version__} start check {'OK' if not failures else 'FAILED'}",
             f"Pages: {', '.join(win.pages)}"] + [f"Not shown: {f}" for f in failures]
    text = "\n".join(lines)
    from akeno.paths import logs_dir
    (logs_dir() / "check-start.txt").write_text(text, encoding="utf-8")   # windowed builds have no console
    if sys.stdout is not None:
        print(text)
    win.close()
    return 4 if failures else 0


def build_suite(app, args, lic, services=None):
    """Everything behind the license: services (database, game library, mod engine), controller, main window,
    tray icon and the start-up scans."""
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QSystemTrayIcon

    from akeno import i18n
    from akeno.services import AppServices
    from akeno.ui import art, widgets
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow

    services = services or AppServices()
    ctl = Controller(services)
    win = MainWindow(ctl, lic)

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable() and not args.smoke_test and not args.screenshot:
        tray = QSystemTrayIcon(QIcon(art.app_icon_pixmap(64)), app)
        tray.setToolTip(APP_NAME)
        tray.activated.connect(lambda reason: (win.showNormal(), win.activateWindow()) if win.isEnabled() else None)
        tray.show()
    win.tray = tray
    win.notifier.tray = tray

    labels = {"install": "Installation finished.", "benchmark": "Benchmark finished.", "games": "Game scan finished.",
              "restore": "Restore finished."}

    def busy(key: str, on: bool) -> None:
        if not on and key in labels:
            win.notifier.notify("tasks", i18n.t(labels[key]), when_hidden=True)

    ctl.busy_changed.connect(busy)

    def license_reminder() -> None:
        from akeno.licensing import Status
        view = lic.view()
        if view.status is Status.EXPIRING:
            win.notifier.notify("license", i18n.t("Your AKENO license ends soon. Redeem a code to extend it."),
                                once=time.strftime("license-%Y-%m-%d"))
    reminder = QTimer(win)
    reminder.setInterval(3600_000)
    reminder.timeout.connect(license_reminder)
    reminder.start()

    def game_started(game_id: str, _pid: int) -> None:
        game = services.library.get(game_id)
        if game is not None and services.boost.configured(game_id):
            win.notifier.notify("games", i18n.t("FPS Booster session settings are active for {g}.", g=game.name))
    win.sessions.started.connect(game_started)

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
        elif stale := services.library.stale_games():
            # records from an older AKENO (e.g. a renderer read from the import table) are refreshed once
            ctl.run("games", lambda: [services.library.reanalyze(g.id) for g in stale],
                    lambda _: (ctl.games_changed.emit(), ctl.discover_dependencies()), silent=True)
        else:
            ctl.discover_dependencies()
        if services.settings.get("auto_update_compat_db"):
            win.pages["settings"].update_compat()
        win.pages["updates"].auto_check(on_start=True)   # when enabled (not again within the hour)
        def report_update() -> None:
            outcome = win.pages["updates"].svc.after_start()
            if outcome:
                win.statusBar().showMessage(
                    i18n.t("AKENO {v} was installed.", v=outcome["version"]) if outcome.get("ok") else
                    i18n.t("The update to {v} did not start; the previous version is back.", v=outcome.get("version")),
                    15000)
                win.pages["updates"].refresh()
        report_update()
        # right after an update the updater is still confirming this start (its health check): report it then
        QTimer.singleShot(45_000, report_update)
        license_reminder()
        win.pages["downloads"].auto_check()
        try:
            win.desktop.ensure()                # wallpapers, dock and widgets come back with the suite
        except Exception as exc:                # Desktop Studio must never keep the suite from starting
            logging.getLogger(__name__).warning("desktop studio: %s", exc)

    if not args.smoke_test and not args.screenshot:
        QTimer.singleShot(200, startup)

        def recover_system() -> None:
            # processes a Network Gaming Mode paused before AKENO was closed unexpectedly run again
            try:
                services.system_tools.recover()
            except Exception as exc:              # never keeps the suite from starting or quitting
                logging.getLogger(__name__).warning("system tools recovery: %s", exc)
        QTimer.singleShot(1500, recover_system)
        app.aboutToQuit.connect(recover_system)
    return ctl, win


def smoke_test(app, ctl, win, gate=None) -> int:
    """Non-interactive check used by CI on a real Windows runner: real hardware scan, real library scan,
    every page built and shown. Prints what was found; exits non-zero on any exception."""
    from akeno.ui import workers
    from PySide6.QtCore import QEventLoop, QTimer
    from akeno.ui import assets
    s = ctl.s
    if gate is not None:
        gate.show()
        app.processEvents()
        gate_status = gate.m.view().status.value
        gate.close()
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
             f"License: {gate_status if gate is not None else 'not checked'} (welcome screen built)",
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
    lines.extend(build_configuration())
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


def build_configuration() -> list[str]:
    """What this build trusts and talks to (public values only: addresses and key ids, never a secret)."""
    from akeno import distribution
    from akeno.licensing import config as licensing_config
    from akeno.updater import manifest
    lic = licensing_config.load()
    dist = distribution.load()
    return [f"License API: {lic.server_url or 'none'} (website {lic.website or 'none'}; "
            f"{'fixed in this build' if lic.locked else 'running from source'})",
            f"License verification keys: {', '.join(sorted(lic.keys)) or 'NONE (no license can be accepted)'}",
            f"Updates: {dist.label}; manifest {dist.channel_manifest('stable')}",
            f"Release verification keys: {', '.join(sorted(manifest.release_keys())) or 'NONE (no update install)'}"]


def screenshots(app, ctl, win, out_dir: str, size: str = "1920x1009", gate=None) -> int:
    from PySide6.QtCore import QEventLoop, QTimer
    from akeno.ui import workers
    os.makedirs(out_dir, exist_ok=True)
    w, h = (int(x) for x in size.lower().split("x"))
    if gate is not None:
        gate.resize(min(w, 1400), min(h, 860))
        gate.show()
        app.processEvents()
        gate.grab().save(os.path.join(out_dir, "license-gate.png"))
        gate.close()
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
        if key == "install" and getattr(win.pages[key], "fivem_panel", None) and win.pages[key].fivem_panel.isVisible():
            panel = win.pages[key].fivem_panel
            for index in range(panel.tabs.count()):
                panel.tabs.setCurrentIndex(index)
                app.processEvents()
                win.grab().save(os.path.join(out_dir, f"fivem-step-{index + 1}.png"))
            panel.tabs.setCurrentIndex(0)
    from akeno.ui.community_runtime import CommunityRuntimeDialog
    dialog = CommunityRuntimeDialog(ctl, win)
    dialog.resize(max(1000, min(w, 1100)), max(600, min(h, 760)))
    dialog.show()
    app.processEvents()
    for index in range(dialog.tabs.count()):
        dialog.tabs.setCurrentIndex(index)
        app.processEvents()
        dialog.grab().save(os.path.join(out_dir, f"community-step-{index + 1}.png"))
    from akeno.community.candidates import BANDUKIDS
    dialog.tabs.setCurrentIndex(0)
    dialog.candidate_choice.setCurrentIndex(dialog.candidate_choice.findData(BANDUKIDS.id))
    app.processEvents()
    dialog.grab().save(os.path.join(out_dir, "bandukids-runtime.png"))
    dialog.close()
    workers.drain(30000)               # no background result may arrive after the window is gone
    print(f"saved screenshots to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
