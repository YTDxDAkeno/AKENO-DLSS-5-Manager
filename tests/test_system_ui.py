"""The SYSTEM pages through the UI (offscreen Qt) with stand-in providers: every action runs in the background, ends
in a visible result, and nothing changes without the confirmation."""

import os
import time

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def suite(qtbot, akeno_home, tmp_path):
    import importlib
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    fixture_home = importlib.import_module("fixture_home")
    sys.argv = ["x", str(akeno_home), str(tmp_path / "games"), "--with-benchmarks"]
    fixture_home.main()
    from akeno import i18n
    from akeno.services import AppServices
    from akeno.system.doctor.providers import FakeProbe
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    i18n.set_language("en")
    svc = AppServices()
    tools = svc.system_tools
    tools.doctor.probe = FakeProbe(
        cpu={"percent": 64.0, "per_core": [64.0], "logical": 8, "physical": 4},
        processes=[{"pid": 99, "name": "SearchIndexer.exe", "exe": "C:\\Windows\\System32\\SearchIndexer.exe",
                    "cpu_percent": 41.0, "rss": 200 * 2 ** 20, "io_bps": 3e6}],
        volumes=[{"mount": "C:\\", "total": 500 * 2 ** 30, "free": 9 * 2 ** 30, "removable": False}],
        events={"app_crash": [{"time": f"2026-10-2{i}T10:00:00+00:00", "props": ["Game.exe", "1.0", "", "dxgi.dll",
                                                                                "", "", "0xc0000005"]}
                              for i in range(3)]})
    ctl = Controller(svc)
    win = MainWindow(ctl)
    qtbot.addWidget(win)
    win.show()
    yield win, ctl, svc
    from akeno.ui import workers
    workers.drain(30000)
    tools.store.close()
    svc.db.close()


def test_system_section_in_navigation(suite):
    win, ctl, svc = suite
    from akeno.ui.main_window import SECTIONS
    system = dict(SECTIONS)["SYSTEM"]
    assert [k for k, _, _ in system][:2] == ["doctor", "cleaner"]
    win.go("doctor")
    assert win.current_page() == "doctor"


def test_doctor_overview_and_slow_pc(suite, qtbot):
    win, ctl, svc = suite
    win.go("doctor")
    page = win.pages["doctor"]
    qtbot.waitUntil(lambda: page.overview.score_value.text() != "—", timeout=10000)
    assert page.overview.score_value.text().endswith("/ 100")
    page.show_tab("slowpc")
    page.slowpc.run()
    qtbot.waitUntil(lambda: not page.slowpc.task.running, timeout=15000)
    titles = [w.finding.title for w in page.slowpc.findings.findChildren(type(page.slowpc.findings.lay.itemAt(0)
                                                                                .widget()))]
    assert any("processor load" in str(x).lower() for x in titles)
    assert page.slowpc.table.rowCount() == 1


def test_doctor_link_opens_other_module(suite):
    win, ctl, svc = suite
    from akeno.ui.system_ui import open_link
    open_link(win, "cleaner", "scan")
    assert win.current_page() == "cleaner"
    win.go("doctor")
    win.pages["doctor"].link("doctor", "crashes")
    assert win.pages["doctor"].current == "crashes"


def test_crash_tab(suite, qtbot):
    win, ctl, svc = suite
    win.go("doctor")
    page = win.pages["doctor"]
    page.show_tab("crashes")
    page.crashes.run()
    qtbot.waitUntil(lambda: not page.crashes.task.running, timeout=10000)
    assert page.crashes.table.rowCount() == 1


def test_startup_disable_needs_confirmation(suite, qtbot, monkeypatch, tmp_path):
    win, ctl, svc = suite
    from akeno.system import lnk
    from akeno.system.doctor.startup import APPROVED, RUN, MemoryStartupRegistry, StartupAnalyzer
    tool = tmp_path / "tool.exe"
    tool.write_bytes(b"MZ")
    reg = MemoryStartupRegistry({("HKCU", RUN): {"Tool": f'"{tool}"'}})
    doctor = svc.system_tools.doctor
    doctor.startup = StartupAnalyzer(reg, {}, lambda paths, ctx=None: {}, lambda p: {}, svc.system_tools.store)
    win.go("doctor")
    page = win.pages["doctor"]
    page.show_tab("startup")
    page.startup.refresh()
    qtbot.waitUntil(lambda: page.startup.table.rowCount() == 1, timeout=10000)
    page.startup.table.selectRow(0)
    assert page.startup.b_disable.isEnabled()
    answers = iter([False, True])
    monkeypatch.setattr("akeno.ui.pages.doctor.confirm", lambda *a, **k: next(answers))
    page.startup._toggle(False)                        # declined: nothing changes
    assert reg.values("HKCU", APPROVED + "\\Run") == {}
    page.startup._toggle(False)
    qtbot.waitUntil(lambda: "Tool" in reg.values("HKCU", APPROVED + "\\Run"), timeout=10000)
    qtbot.waitUntil(lambda: page.startup.hist.rowCount() == 1, timeout=10000)


def test_repair_marks_recommended_steps(suite, qtbot):
    win, ctl, svc = suite
    from akeno.system.model import Finding, Level, Report
    svc.system_tools.store.add_history("doctor.crashes", Report("doctor", [
        Finding("crash.bugcheck.ef", "doctor", Level.PROBLEM, "x")]).to_dict())
    win.go("doctor")
    page = win.pages["doctor"]
    page.show_tab("repair")
    from PySide6.QtWidgets import QLabel
    texts = [w.text() for w in page.repair.findChildren(QLabel)]
    assert any("Why now" in x for x in texts)


@pytest.fixture
def junk(tmp_path):
    env = {"LOCALAPPDATA": str(tmp_path / "Local"), "TEMP": str(tmp_path / "Local" / "Temp"),
           "SystemRoot": str(tmp_path / "Windows"), "USERPROFILE": str(tmp_path / "User")}
    old = time.time() - 20 * 86400
    for name in ("a.tmp", "b.tmp"):
        p = tmp_path / "Local" / "Temp" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 4096)
        os.utime(p, (old, old))
    shader = tmp_path / "Local" / "NVIDIA" / "DXCache" / "s.bin"
    shader.parent.mkdir(parents=True)
    shader.write_bytes(b"s")
    os.utime(shader, (old, old))
    return env, tmp_path


def test_cleaner_scan_preview_clean_report(suite, qtbot, junk, monkeypatch):
    win, ctl, svc = suite
    env, root = junk
    cleaner = svc.system_tools.cleaner
    cleaner.env = env
    win.go("cleaner")
    page = win.pages["cleaner"]
    page.junk.scan()
    qtbot.waitUntil(lambda: not page.junk.task.running and bool(page.junk.checks), timeout=15000)
    assert page.junk.checks["user_temp"].isChecked()
    assert not page.junk.checks["shader_nvidia"].isChecked(), "shader caches are never preselected"
    from PySide6.QtWidgets import QDialog
    from akeno.ui.pages import cleaner as cleaner_page
    seen = {}

    def fake_exec(self):
        seen["count"] = self.pv.count
        seen["shader"] = self.shader_ok.isVisible() or self.pv.needs_shader_confirmation
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(cleaner_page.PreviewDialog, "exec", fake_exec)
    page.junk.preview()                                 # cancelled preview: nothing removed
    assert seen["count"] == 2 and not seen["shader"]
    assert (root / "Local" / "Temp" / "a.tmp").exists()
    monkeypatch.setattr(cleaner_page.PreviewDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    page.junk.preview()
    qtbot.waitUntil(lambda: page.junk.report.isVisible(), timeout=15000)
    assert not (root / "Local" / "Temp" / "a.tmp").exists()
    assert (root / "Local" / "NVIDIA" / "DXCache" / "s.bin").exists()
    from PySide6.QtWidgets import QLabel
    grid_text = " ".join(lb.text() for lb in page.junk.report_grid.findChildren(QLabel))
    assert "2 files" in grid_text


def test_cleaner_disk_space_tab(suite, qtbot, tmp_path):
    win, ctl, svc = suite
    (tmp_path / "big").mkdir()
    (tmp_path / "big" / "f.bin").write_bytes(b"\0" * 10000)
    win.go("cleaner")
    page = win.pages["cleaner"]
    page.show_tab("space")
    page.space.analyse(str(tmp_path))
    qtbot.waitUntil(lambda: page.space.report is not None, timeout=15000)
    assert page.space.largest.rowCount() >= 1


def test_maintenance_setting_persists(suite):
    win, ctl, svc = suite
    win.go("cleaner")
    page = win.pages["cleaner"]
    page.show_tab("maintenance")
    page.maint.interval.setCurrentIndex(page.maint.interval.findData(7))
    assert svc.system_tools.cleaner.maintenance()["interval_days"] == 7
