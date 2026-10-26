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
    from akeno.system.securitycenter.providers import FakeSecurity, defender_active_status
    tools.security.provider = FakeSecurity(
        health={"antivirus": "good", "firewall": "good"},
        products={"antivirus": [{"name": "Microsoft Defender Antivirus", "state": "on", "signature": "up_to_date"}]},
        firewall=[{"name": "Domain", "enabled": True}, {"name": "Public", "enabled": True}],
        defender_status=defender_active_status(), history=[],
        procs=[{"pid": 41, "name": "svchost.exe", "exe": "C:\\Users\\p\\AppData\\Roaming\\svchost.exe"}])
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
    open_link(win, "cleaner", "space:C:\\")                  # a full drive: what fills it
    assert win.pages["cleaner"].current == "space" and win.pages["cleaner"].space.path.text() == "C:\\"
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


def test_maintenance_tick_after_shutdown_is_quiet(suite):
    win, ctl, svc = suite
    page = win.pages["cleaner"]
    svc.system_tools.store.close()                      # the app shut down while the page still exists
    page.maintenance_tick()
    assert not page.timer.isActive()


def test_network_stability_and_bufferbloat_consent(suite, qtbot):
    win, ctl, svc = suite
    net = svc.system_tools.network
    net._prober = lambda method, target: None if method == "icmp" else 21.0      # ICMP blocked, TCP works
    win.go("network")
    page = win.pages["network"]
    page.show_tab("stability")
    tab = page.tabs_w["stability"]
    tab.duration.setCurrentIndex(0)
    tab.run()
    qtbot.waitUntil(lambda: not tab.task.running, timeout=30000)
    assert tab.table.rowCount() >= 6
    from akeno.ui.system_ui import FindingView
    ids = [w.finding.id for w in tab.findings.findChildren(FindingView)]
    assert "net.icmp_blocked" in ids and "net.offline" not in ids
    page.show_tab("bufferbloat")
    bb = page.tabs_w["bufferbloat"]
    assert not bb.btn.isEnabled(), "no load test without consent"
    bb.consent.setChecked(True)
    assert bb.btn.isEnabled()


def test_network_gaming_mode_pause_restore(suite, qtbot, monkeypatch):
    import subprocess
    import sys
    win, ctl, svc = suite
    net = svc.system_tools.network
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        calls = []
        net.gaming._suspend = lambda pid: calls.append(("s", pid)) or True
        net.gaming._resume = lambda pid: calls.append(("r", pid)) or True
        net._prober = lambda method, target: 20.0
        monkeypatch.setattr(net, "gaming_candidates", lambda ctx=None: [
            {"pid": proc.pid, "name": "OneDrive.exe", "title": "OneDrive", "tcp": 3, "udp": 0, "io_bps": 1e6,
             "known": True, "remotes": []}])
        monkeypatch.setattr("akeno.ui.pages.network.confirm", lambda *a, **k: True)
        win.go("network")
        page = win.pages["network"]
        page.show_tab("gaming")
        tab = page.tabs_w["gaming"]
        tab.find()
        qtbot.waitUntil(lambda: tab.table.rowCount() == 1, timeout=10000)
        tab.table.selectRow(0)
        tab.pause()
        qtbot.waitUntil(lambda: net.gaming.active(), timeout=30000)
        qtbot.waitUntil(lambda: not tab.task.running, timeout=30000)
        assert ("s", proc.pid) in calls and tab.b_restore.isEnabled()
        tab.restore()
        qtbot.waitUntil(lambda: not net.gaming.active(), timeout=10000)
        assert ("r", proc.pid) in calls
    finally:
        proc.kill()
        proc.wait()


def test_network_dns_benchmark_table(suite, qtbot, monkeypatch):
    win, ctl, svc = suite
    monkeypatch.setattr("akeno.system.network.dns.query", lambda server, name, timeout=2.0, sock_factory=None:
                        (12.0 if server != "9.9.9.9" else None, "ok" if server != "9.9.9.9" else "timeout"))
    win.go("network")
    page = win.pages["network"]
    page.show_tab("dns")
    tab = page.tabs_w["dns"]
    tab.run()
    qtbot.waitUntil(lambda: tab.table.rowCount() >= 5, timeout=30000)
    assert tab.results[-1]["server"] == "9.9.9.9"


def _minecraft_fixture(tmp_path):
    import json
    import zipfile
    base = tmp_path / "Roaming" / "PrismLauncher" / "instances" / "Pack"
    mods = base / ".minecraft" / "mods"
    mods.mkdir(parents=True)
    (base / "instance.cfg").write_text("name=Test Pack\n")
    (base / "mmc-pack.json").write_text(json.dumps({"components": [
        {"uid": "net.minecraft", "version": "1.20.1"}, {"uid": "net.fabricmc.fabric-loader", "version": "0.15.11"}]}))
    for name, mid in (("a.jar", "sodium"), ("b.jar", "sodium"), ("c.jar", "lithium")):
        with zipfile.ZipFile(mods / name, "w") as z:
            z.writestr("fabric.mod.json", json.dumps({"id": mid, "version": "1.0", "name": mid.title()}))
    return {"APPDATA": str(tmp_path / "Roaming"), "USERPROFILE": str(tmp_path / "User")}


def test_installed_mods_library(suite, qtbot, tmp_path):
    win, ctl, svc = suite
    svc.system_tools.mods.env = _minecraft_fixture(tmp_path)
    win.go("installed")
    page = win.pages["installed"]
    page.scan()
    qtbot.waitUntil(lambda: not page.task.running and page.list.count() >= 1, timeout=30000)
    keys = [page.list.item(i).data(0x0100) for i in range(page.list.count())]
    mc = next(k for k in keys if k.startswith("minecraft:"))
    page.list.setCurrentRow(keys.index(mc))
    assert page.d_table.rowCount() == 3
    from akeno.ui.system_ui import FindingView
    assert any(w.finding.id == "mods.duplicate.sodium" for w in page.d_findings.findChildren(FindingView))
    page.filter.setCurrentIndex(page.filter.findData("minecraft"))
    assert page.list.count() == 1
    page.filter.setCurrentIndex(0)
    page.search.setText("lithium")
    assert page.list.count() == 1 and page.d_table.rowCount() == 1
    page.search.setText("")
    page.d_table.selectRow(0)
    assert page.b_open.isEnabled() and "AKENO does not change this file" in page.d_mod.text()


def test_installed_mods_empty_state_is_centered(suite, qtbot):
    win, ctl, svc = suite
    page = win.pages["installed"]
    svc.system_tools.mods.groups = []
    page.refresh()
    page._render_list()
    win.go("installed")
    assert page.empty_box.isVisible() and not page.library.isVisible()
    assert page.empty_box.minimumHeight() >= 360


def test_security_overview_and_navigation(suite, qtbot):
    win, ctl, svc = suite
    from akeno.ui.main_window import SECTIONS
    assert [k for k, _, _ in dict(SECTIONS)["SYSTEM"]] == ["doctor", "cleaner", "network", "security"]
    win.go("security")
    page = win.pages["security"]
    tab = page.tabs_w["overview"]
    qtbot.waitUntil(lambda: tab.loaded_at > 0, timeout=15000)
    from akeno.ui.system_ui import FindingView
    ids = {w.finding.id for w in tab.findings.findChildren(FindingView)}
    assert {"sec.av.on", "sec.firewall.on"} <= ids
    assert tab.products.rowCount() == 1 and tab.t_av.value.text() == "OK"


def test_security_third_party_disables_defender_scans(suite, qtbot):
    win, ctl, svc = suite
    from akeno.system.securitycenter.providers import FakeSecurity
    svc.system_tools.security.provider = FakeSecurity(
        health={"antivirus": "good", "firewall": "good"},
        products={"antivirus": [{"name": "Contoso Antivirus", "state": "on"}]}, firewall=[], defender_status=None,
        history=[])
    svc.system_tools.security._status = None
    win.go("security")
    page = win.pages["security"]
    page.show_tab("scan")
    scan = page.tabs_w["scan"]
    qtbot.waitUntil(lambda: not scan.b_quick.isEnabled(), timeout=10000)
    assert "antivirus" in scan.ready.text()
    page.show_tab("overview")
    tab = page.tabs_w["overview"]
    tab.refresh()
    qtbot.waitUntil(lambda: not tab.task.running and tab.loaded_at > 0, timeout=15000)
    from akeno.ui.system_ui import FindingView
    assert "sec.av.third_party" in {w.finding.id for w in tab.findings.findChildren(FindingView)}


def test_security_quick_scan_and_full_scan_confirmation(suite, qtbot, monkeypatch):
    win, ctl, svc = suite
    win.go("security")
    page = win.pages["security"]
    page.show_tab("scan")
    scan = page.tabs_w["scan"]
    monkeypatch.setattr("akeno.ui.pages.security.confirm", lambda *a, **k: False)
    scan.run("full")
    assert not scan.task.running and svc.system_tools.security.provider.scans == []
    scan.run("quick")
    qtbot.waitUntil(lambda: not scan.task.running and bool(scan.result.text()), timeout=10000)
    assert scan.result.text().startswith("No threats found")
    assert svc.system_tools.security.provider.scans == [("quick", None, False)]
    assert scan.table.rowCount() == 1


def test_security_file_check_from_mod_link(suite, qtbot, tmp_path):
    win, ctl, svc = suite
    mod = tmp_path / "dinput8.dll"
    mod.write_bytes(b"MZ" + b"\0" * 200)
    from akeno.ui.system_ui import open_link
    open_link(win, "security", "file:" + str(mod))
    page = win.pages["security"]
    files = page.tabs_w["files"]
    assert page.current == "files"
    qtbot.waitUntil(lambda: not files.task.running and files.table.rowCount() == 1, timeout=15000)
    assert files.b_open.isEnabled()
    rows = {files.kv.grid.itemAtPosition(i, 0).widget().text(): files.kv.grid.itemAtPosition(i, 1).widget()
            for i in range(files.kv.grid.rowCount()) if files.kv.grid.itemAtPosition(i, 0)}
    assert "SHA-256" in rows
    assert svc.system_tools.security.provider.scans == [("custom", str(mod), True)]   # report only


def test_security_process_review(suite, qtbot):
    win, ctl, svc = suite
    win.go("security")
    page = win.pages["security"]
    page.show_tab("processes")
    tab = page.tabs_w["processes"]
    tab.refresh()
    qtbot.waitUntil(lambda: not tab.task.running and tab.table.rowCount() == 1, timeout=15000)
    from akeno.ui.system_ui import FindingView
    assert [w.finding.id for w in tab.findings.findChildren(FindingView)] == ["proc.masquerade.svchost.exe"]


def test_security_report_export(suite, qtbot, tmp_path, monkeypatch):
    import json
    win, ctl, svc = suite
    win.go("security")
    tab = win.pages["security"].tabs_w["overview"]
    qtbot.waitUntil(lambda: tab.loaded_at > 0, timeout=15000)
    target = tmp_path / "report.json"
    monkeypatch.setattr("akeno.ui.system_ui.choose_save_path", lambda *a: str(target))
    monkeypatch.setattr("akeno.ui.widgets.info", lambda *a, **k: None)
    from akeno.ui.system_ui import export_report
    assert export_report(tab, svc.system_tools.store, "security", {"overview": tab.last}) == str(target)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["module"] == "security" and data["overview"]["findings"]
