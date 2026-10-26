"""System Doctor with stand-in providers: no sensors, failing Windows queries, high load, crashes, drive data,
startup entries. Mock data proves the logic, not the behaviour of real Windows installations or drivers."""

import os
import time

import pytest

from akeno.system.commands import CommandUnavailable, QueryError
from akeno.system.doctor import crashes, hardware, health, repair, slowpc
from akeno.system.doctor.providers import FakeProbe, norm_luid, parse_nvidia
from akeno.system.doctor.startup import (APPROVED, RUN, MemoryStartupRegistry, StartupAnalyzer, approved_enabled,
                                         disabled_bytes, split_command)
from akeno.system.model import EvidenceKind, Level
from akeno.system.store import SystemStore
from akeno.system import lnk


@pytest.fixture
def store(tmp_path):
    s = SystemStore(tmp_path / "system.sqlite3")
    yield s
    s.close()


def test_no_sensors_shows_unavailable_never_a_value():
    probe = FakeProbe(volumes=[{"mount": "C:\\", "total": 500 * 2 ** 30, "free": 200 * 2 ** 30, "used": 0,
                                "percent": 60, "removable": False}])
    snap = health.collect(probe, None, sample_s=0)
    assert snap.cpu["load"].available
    assert not snap.cpu["temperature"].available and snap.cpu["temperature"].note
    assert not snap.memory["commit"].available
    assert not snap.cpu["clock"].available
    assert snap.gpus == []
    assert any("Windows" in str(e) for e in snap.errors)
    score = health.score(health.findings(snap, "C:\\"))
    assert score.value == 100 and score.deductions == [], "missing sensors are not a defect"


def test_low_disk_space_links_to_cleaner():
    probe = FakeProbe(volumes=[{"mount": "C:\\", "total": 500 * 2 ** 30, "free": 8 * 2 ** 30, "used": 0,
                                "percent": 98, "removable": False}])
    snap = health.collect(probe, None, sample_s=0)
    found = health.findings(snap, "C:\\")
    assert found and found[0].id.startswith("disk.low") and found[0].level == Level.PROBLEM
    assert (found[0].links[0].module, found[0].links[0].target) == ("cleaner", "space:C:\\")
    sc = health.score(found)
    assert sc.value == 85 and sc.deductions[0][0] == 15


def test_score_caps_repeated_families():
    from akeno.system.model import Finding
    many = [Finding(f"crash.app.{i}", "doctor", Level.WARN, "x") for i in range(10)]
    assert health.score(many).value == 100 - 3 * 6


def test_incomplete_gpu_data_is_reported_as_unavailable():
    from akeno.core.models import GpuInfo
    from akeno.core.models import SystemInfo
    system = SystemInfo(gpus=[GpuInfo(name="AMD Radeon RX 7800 XT", vram_bytes=16 * 2 ** 30, luid="00000000:0000ABCD")])
    probe = FakeProbe(perf={"memory": {}, "cpu": {}, "thermal": [], "gpu_3d": {"00000000:0000ABCD": 41.0},
                            "gpu_memory": {}, "gpu_3d_by_pid": {}})
    snap = health.collect(probe, system, sample_s=0)
    g = snap.gpus[0]["readings"]
    assert g["load"].value == 41.0 and "3D engine" in g["load"].source
    assert not g["temperature"].available


def test_nvidia_parsing_and_thermal_flags():
    rows = parse_nvidia("NVIDIA GeForce RTX 4080, 97, 9000, 16376, 88, 310.5, 2505, 3105, Active, Not Active, "
                        "Not Active\nbad,row\n")
    assert len(rows) == 1 and rows[0]["thermal_hw"] is True and rows[0]["temp_c"] == 88
    assert parse_nvidia("NVIDIA X, [N/A], [N/A], [N/A], [N/A], [N/A], [N/A], [N/A], [N/A], [N/A], [N/A]")[0]["util"] is None


def test_luid_normalisation():
    assert norm_luid("luid_0x00000000_0x0000D1E3") == norm_luid("00000000:0000D1E3") == "00000000:0000D1E3"


def _procs():
    return [{"pid": 10, "name": "MsMpEng.exe", "exe": "C:\\ProgramData\\x\\MsMpEng.exe", "cpu_percent": 35.0,
             "rss": 300 * 2 ** 20, "io_bps": 50e6},
            {"pid": 11, "name": "game.exe", "exe": "C:\\Games\\game.exe", "cpu_percent": 40.0, "rss": 4 * 2 ** 30,
             "io_bps": 0},
            {"pid": 12, "name": "notepad.exe", "exe": "", "cpu_percent": 0.1, "rss": 2 ** 20, "io_bps": 0}]


def test_slow_pc_high_background_load_names_process_and_possible_cause():
    probe = FakeProbe(cpu={"percent": 72.0, "per_core": [72.0], "logical": 8, "physical": 4},
                      processes=_procs())
    rep = slowpc.run(probe, sample_s=0, exclude_pids={11})
    f = next(x for x in rep.findings if x.id == "cpu.background")
    assert f.level == Level.PROBLEM
    measured = " ".join(str(e.text) for e in f.evidence if e.kind == EvidenceKind.MEASURED)
    assert "MsMpEng.exe" in measured and "game.exe" not in measured, "the running game is excluded"
    assert any(e.kind == EvidenceKind.POSSIBLE_CAUSE and "Defender" in str(e.text) for e in f.evidence)
    assert any("Windows" in str(e) for e in rep.errors), "missing Windows counters are reported, not hidden"


def test_slow_pc_quiet_pc_says_no_cause_found():
    rep = slowpc.run(FakeProbe(), sample_s=0)
    assert [f.id for f in rep.findings] == ["slowpc.none"]


def test_slow_pc_survives_failing_windows_queries():
    probe = FakeProbe(perf=QueryError("WMI is broken"), events=QueryError("Event log service stopped"),
                      processes=RuntimeError("process list failed"))
    rep = slowpc.run(probe, sample_s=0)
    text = " ".join(str(e) for e in rep.errors)
    assert "WMI is broken" in text and "Event log service stopped" in text and "process list failed" in text


def test_slow_pc_memory_pressure_with_paging():
    probe = FakeProbe(memory={"total": 8 * 2 ** 30, "available": 2 ** 28, "used": 7.75 * 2 ** 30, "percent": 96.9,
                              "swap_total": 0, "swap_used": 0, "swap_percent": 0},
                      perf={"memory": {"PagesPersec": 1800}, "cpu": {}, "thermal": [], "gpu_3d": {}, "gpu_memory": {}})
    rep = slowpc.run(probe, sample_s=0)
    f = next(x for x in rep.findings if x.id == "ram.pressure")
    assert f.level == Level.PROBLEM and any(e.kind == EvidenceKind.POSSIBLE_CAUSE for e in f.evidence)


def test_slow_pc_events_changes_are_correlations_only():
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    probe = FakeProbe(events={"driver_install": [{"time": now, "id": 20001, "message": "Driver NVIDIA installed"}],
                              "cpu_limited": [{"time": now, "id": 37}] * 4,
                              "disk": [{"time": now, "id": 153, "provider": "disk", "message": "IO retried"},
                                       {"time": now, "id": 7, "provider": "SomethingElse"}]})
    rep = slowpc.run(probe, sample_s=0)
    ch = next(f for f in rep.findings if f.id == "changes.recent")
    assert ch.level == Level.INFO and all(e.kind == EvidenceKind.CORRELATION for e in ch.evidence)
    assert any(f.id == "thermal.firmware" for f in rep.findings)
    disk = next(f for f in rep.findings if f.id == "events.disk")
    assert "1 disk" in str(disk.observation)


def test_slow_pc_new_busy_process_since_last_check():
    prev = {"top_cpu": [{"name": "chrome.exe"}], "created_at": "2026-10-01T10:00:00+00:00"}
    probe = FakeProbe(processes=[{"pid": 5, "name": "miner.exe", "cpu_percent": 25.0, "rss": 1, "io_bps": 0}],
                      cpu={"percent": 26.0, "per_core": [26.0], "logical": 4, "physical": 2})
    rep = slowpc.run(probe, previous=prev, sample_s=0)
    f = next(x for x in rep.findings if x.id == "cpu.new_load")
    assert "miner.exe" in str(f.evidence[0].text) and f.evidence[0].kind == EvidenceKind.CORRELATION


# -- crashes --------------------------------------------------------------------------------------------------------
def _crash(t, app="Game.exe", module="nvwgf2umx.dll", code="0xc0000005", app_path="C:\\Games\\G\\Game.exe",
           module_path="C:\\Windows\\System32\\DriverStore\\nvwgf2umx.dll"):
    return {"time": t, "id": 1000, "props": [app, "1.0", "", module, "31.0", "", code, "0x1", "", "", app_path,
                                             module_path]}


def test_crash_detective_groups_repeated_crashes_and_explains():
    rows = [_crash(f"2026-10-2{i}T10:00:00+00:00") for i in range(4)]
    probe = FakeProbe(events={"app_crash": rows,
                              "driver_install": [{"time": "2026-10-19T09:00:00+00:00", "message": "Display driver"}]})
    rep = crashes.analyse(probe, game_dirs={"C:\\Games\\G": "Game"})
    f = next(x for x in rep.findings if x.id.startswith("crash.app."))
    assert "4" in str(f.title)
    causes = [str(e.text) for e in f.evidence if e.kind == EvidenceKind.POSSIBLE_CAUSE]
    assert any("Access violation" in c for c in causes) and any("graphics driver" in c for c in causes)
    assert any(e.kind == EvidenceKind.CORRELATION for e in f.evidence), "driver installed just before"
    assert f.data.get("game") == "Game"


def test_crash_from_proxy_dll_in_game_folder_links_to_mods():
    rows = [_crash(f"2026-10-2{i}T10:00:00+00:00", module="dxgi.dll", module_path="C:\\Games\\G\\dxgi.dll")
            for i in range(3)]
    rep = crashes.analyse(FakeProbe(events={"app_crash": rows}))
    f = next(x for x in rep.findings if x.id.startswith("crash.app."))
    assert any(link.module == "installed" for link in f.links)


def test_bugcheck_and_power_loss():
    probe = FakeProbe(events={
        "bugcheck": [{"time": "2026-10-20T10:00:00+00:00", "props": ["0x000000ef (0x1, 0x2)", "C:\\x.dmp"]}],
        "unexpected_shutdown": [{"time": "2026-10-20T10:05:00+00:00", "id": 41, "props": ["239"]},
                                {"time": "2026-10-22T10:00:00+00:00", "id": 41, "props": ["0"]}]})
    rep = crashes.analyse(probe)
    ids = [f.id for f in rep.findings]
    assert "crash.bugcheck.ef" in ids and "crash.power_loss" in ids
    bc = next(f for f in rep.findings if f.id == "crash.bugcheck.ef")
    assert "CRITICAL_PROCESS_DIED" in str(bc.title) and any(link.target == "repair" for link in bc.links)
    assert crashes.parse_bugcheck("0x0000009f (0x3)") == 0x9F and crashes.parse_bugcheck("none") is None


def test_crash_detective_event_log_failure_is_honest():
    rep = crashes.analyse(FakeProbe(events=QueryError("Access is denied")))
    assert rep.findings == [] and "Access is denied" in str(rep.errors[0])
    rep = crashes.analyse(FakeProbe())
    assert rep.findings == [] and rep.errors


def test_no_crashes_is_ok():
    rep = crashes.analyse(FakeProbe(events={}))
    assert [f.id for f in rep.findings] == ["crash.none"]


# -- drives ---------------------------------------------------------------------------------------------------------
def test_drive_health_findings_from_the_drive_itself():
    data = {"disks": [{"name": "Samsung SSD 980", "media": "SSD", "bus": "NVMe", "health": "Healthy",
                       "operational": ["OK"], "temperature": 74, "temperature_max": 82, "wear": 93,
                       "read_errors_uncorrected": 0},
                      {"name": "WDC HDD", "health": "Unhealthy", "operational": ["Predictive Failure"]},
                      {"name": "Kingston", "health": "Healthy", "reliability_error": "Access denied"}],
            "smart": [{"Model": "WDC HDD", "Status": "Pred Fail"}]}
    rows, found = hardware.analyse_drives(data)
    ids = {f.id for f in found}
    assert "drive.failing.WDC HDD" in ids and "drive.wear.Samsung SSD 980" in ids and "drive.hot.Samsung SSD 980" in ids
    kingston = next(r for r in rows if r["name"] == "Kingston")
    assert "administrator" in kingston["counters_note"] and kingston["temperature"] is None
    assert not any("Kingston" in f.id for f in found), "unknown counters are not a problem"


def test_drive_health_tolerates_garbage():
    rows, found = hardware.analyse_drives({"disks": [{"name": None, "wear": "n/a", "temperature": "hot"}],
                                           "smart": [{"Model": None}]})
    assert len(rows) == 1 and found == []
    rep = hardware.analyse(FakeProbe(physical_disks=QueryError("Storage module missing")))
    assert "Storage module missing" in str(rep.errors[0]) and rep.findings == []


# -- startup --------------------------------------------------------------------------------------------------------
def _analyzer(tmp_path, store, entries=None, exists=None):
    tool = tmp_path / "Tool" / "tool.exe"
    tool.parent.mkdir(parents=True, exist_ok=True)
    tool.write_bytes(b"MZ")
    reg = MemoryStartupRegistry({
        ("HKCU", RUN): {"Tool": f'"{tool}" --tray', "Gone": r'"C:\Nowhere\gone.exe"'},
        ("HKLM", RUN): {"SecurityHealth": r"C:\Windows\system32\SecurityHealthSystray.exe"},
    })
    folder = tmp_path / "Startup"
    folder.mkdir(exist_ok=True)
    (folder / "Helper.lnk").write_bytes(lnk.build(str(tool)))
    (folder / "desktop.ini").write_text("x")

    def sig(paths, ctx=None):
        from akeno.system.signatures import Signature
        return {p: Signature("unsigned") for p in paths}
    a = StartupAnalyzer(reg, {"folder_user": str(folder)}, sig, lambda p: {"CompanyName": "Tool Corp"}, store)
    return a, reg, tool


def test_startup_lists_sources_and_flags_problems(tmp_path, store):
    a, reg, tool = _analyzer(tmp_path, store)
    entries = {e.name: e for e in a.entries()}
    assert set(entries) == {"Tool", "Gone", "SecurityHealth", "Helper"}
    assert entries["Tool"].path == str(tool) and entries["Tool"].publisher == "Tool Corp"
    assert entries["Helper"].source == "folder_user" and entries["Helper"].path == str(tool)
    assert any("does not exist" in str(p) for p in entries["Gone"].problems)
    assert entries["SecurityHealth"].protected


def test_startup_disable_enable_and_restore_are_journalled(tmp_path, store):
    a, reg, tool = _analyzer(tmp_path, store)
    e = next(x for x in a.entries() if x.name == "Tool")
    a.set_enabled(e, False)
    assert not approved_enabled(reg.values("HKCU", APPROVED + "\\Run")["Tool"])
    assert not next(x for x in a.entries() if x.name == "Tool").enabled
    hist = a.history()
    assert hist[0]["kind"] == "startup.disable" and hist[0]["state"] == "done"
    a.restore(hist[0]["id"])
    assert "Tool" not in reg.values("HKCU", APPROVED + "\\Run"), "restore puts back exactly what was there"
    assert next(x for x in a.entries() if x.name == "Tool").enabled
    with pytest.raises(ValueError):
        a.restore(hist[0]["id"])


def test_startup_never_disables_protected_entries(tmp_path, store):
    a, reg, tool = _analyzer(tmp_path, store)
    sec = next(x for x in a.entries() if x.name == "SecurityHealth")
    with pytest.raises(PermissionError):
        a.set_enabled(sec, False)
    assert reg.values("HKLM", APPROVED + "\\Run") == {}


def test_startup_machine_entries_use_elevation_when_denied(tmp_path, store):
    a, reg, tool = _analyzer(tmp_path, store)
    reg.data[("HKLM", RUN.lower())]["Vendor"] = f'"{tool}"'
    reg.denied_hives.add("HKLM")
    calls = []
    e = next(x for x in a.entries() if x.name == "Vendor")
    a.set_enabled(e, False, elevate=lambda *args: calls.append(args))
    assert calls and calls[0][0] == "HKLM" and calls[0][2] == "Vendor"


def test_startup_failed_write_is_journalled_as_failed(tmp_path, store):
    a, reg, tool = _analyzer(tmp_path, store)
    reg.denied_hives.add("HKCU")
    e = next(x for x in a.entries() if x.name == "Tool")
    with pytest.raises(PermissionError):
        a.set_enabled(e, False)
    assert a.history()[0]["state"] == "failed"


def test_split_command_variants(tmp_path):
    exe = tmp_path / "My Tool" / "app.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    assert split_command(f'"{exe}" -a') == (str(exe), "-a")
    assert split_command(f"{exe} /min") == (str(exe), "/min")
    assert split_command("rundll32.exe shell32.dll,Control_RunDLL") == ("rundll32.exe", "shell32.dll,Control_RunDLL")
    assert split_command("") == (None, "")
    assert approved_enabled(None) and approved_enabled(b"\x02\x00") and not approved_enabled(disabled_bytes(0))


# -- repair ---------------------------------------------------------------------------------------------------------
def test_repair_recommended_only_with_a_reason():
    assert repair.recommendations(None, None) == {}
    rec = repair.recommendations({"findings": [{"id": "crash.bugcheck.ef"}]},
                                 {"findings": [{"id": "disk.low.C:\\"}]})
    assert "sfc" in rec and "dism_scan" in rec and "space" in rec
    assert all(s.what and s.effect for s in repair.STEPS)
    assert {s.id for s in repair.STEPS if s.admin} == {"dism_scan", "dism_restore", "sfc"}


def test_broken_shortcuts_found_and_recycled_after_recheck(tmp_path, store):
    desk = tmp_path / "Desktop"
    desk.mkdir()
    ok_target = tmp_path / "ok.exe"
    ok_target.write_bytes(b"MZ")
    (desk / "ok.lnk").write_bytes(lnk.build(str(ok_target)))
    (desk / "broken.lnk").write_bytes(lnk.build(str(tmp_path / "missing.exe")))
    found = repair.broken_shortcuts({"Desktop": (str(desk), True)})
    assert [os.path.basename(f["path"]) for f in found] == ["broken.lnk"]

    class Bin:
        def __init__(self):
            self.removed = []

        def remove(self, p):
            self.removed.append(p)
            os.unlink(p)
    b = Bin()
    res = repair.recycle_shortcuts(found, store, b)
    assert res["done"] == [found[0]["path"]] and not (desk / "broken.lnk").exists() and (desk / "ok.lnk").exists()
    # a shortcut that changed after the scan is left alone
    (desk / "broken2.lnk").write_bytes(lnk.build(str(tmp_path / "missing2.exe")))
    found = repair.broken_shortcuts({"Desktop": (str(desk), True)})
    (desk / "broken2.lnk").write_bytes(lnk.build(str(tmp_path / "missing2.exe"), "--changed"))
    res = repair.recycle_shortcuts(found, store, b)
    assert res["done"] == [] and (desk / "broken2.lnk").exists()


def test_doctor_service_snapshot_and_history(tmp_path, store):
    from akeno.system.doctor.service import DoctorService
    a, reg, tool = _analyzer(tmp_path, store)
    svc = DoctorService(store, probe=FakeProbe(), startup=a)
    out = svc.snapshot()
    assert out["score"].value == 100 and out["history"]
    rep = svc.slow_pc()
    assert store.latest("doctor.slowpc") is not None
    assert any(f.id == "startup.flagged" for f in rep.findings)
    assert svc.last("slowpc").findings
