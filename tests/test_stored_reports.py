"""Results the SYSTEM tools saved on a PC must never keep AKENO from starting (the 3.1.0 start crash).

3.1.0 stored the Hardware Health report with the CPU temperature's note as JSON (``{"t": template, "p": values}``,
``model.enc``); on the next start ``HardwareTab._show`` passed that dict to ``t()`` and AKENO ended with
``TypeError: unhashable type: 'dict'`` before its window was up. On Windows the CPU temperature is always missing
(no documented sensor without a vendor driver), so every PC that had run Hardware Health once was affected.

These tests store real reports the services produce (with the missing CPU sensor), and the exact JSON 3.1.0 wrote,
in ``system.sqlite3``, start AKENO again (a new process, ``--check-start``) and build every page in English and
German. The stored results and undo records must stay exactly as they were.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data"
CPU_NOTE_EN = "Windows has no documented CPU temperature sensor without a vendor driver."
CPU_NOTE_DE = "Windows hat ohne Herstellertreiber keinen dokumentierten CPU-Temperatursensor."
WINDOWS_DRIVES = {"error": "", "disks": [
    {"FriendlyName": "Samsung SSD 990 PRO 2TB", "MediaType": "SSD", "BusType": "NVMe", "HealthStatus": "Healthy",
     "OperationalStatus": "OK", "Size": 2000398934016, "DeviceId": "0", "Temperature": 41, "Wear": 2,
     "PowerOnHours": 1234, "ReadErrorsUncorrected": 0, "WriteErrorsUncorrected": 0}],
    "smart": [{"Model": "Samsung SSD 990 PRO 2TB", "Status": "OK", "InterfaceType": "SCSI", "Size": 2000396321280}]}


def windows_probe():
    """A Windows PC as AKENO 3.1 reads it: drives and memory, no CPU temperature sensor."""
    from akeno.system.doctor.providers import FakeProbe
    return FakeProbe(cpu={"percent": 12.0, "per_core": [12.0], "logical": 8, "physical": 4},
                     volumes=[{"mount": "C:\\", "total": 500 * 2 ** 30, "free": 120 * 2 ** 30, "removable": False}],
                     physical_disks=WINDOWS_DRIVES, cpu_temperature=None,
                     events={"app_crash": [{"time": f"2026-10-2{i}T10:00:00+00:00",
                                            "props": ["Game.exe", "1.0", "", "dxgi.dll", "", "", "0xc0000005"]}
                                           for i in range(3)]})


def seed(akeno_home: Path, language: str) -> dict:
    """Run the analyses like a user does, so ``system.sqlite3`` holds what they really store; plus an undo record
    of each module and the stored JSON of AKENO 3.1.0. Returns the database rows to compare afterwards."""
    from akeno.core.db import Database
    from akeno.core.settings import Settings
    from akeno.paths import db_path
    from akeno.services import AppServices
    Settings(Database(db_path())).set("language", language)
    svc = AppServices()
    tools = svc.system_tools
    tools.doctor.probe = windows_probe()
    hw = tools.doctor.hardware()
    assert hw.data["cpu_temperature"]["note"] == {"t": CPU_NOTE_EN, "p": {}}, "the stored form of 3.1.0"
    tools.doctor.slow_pc()
    tools.doctor.crashes()
    tools.network._prober = lambda method, target: None if method == "icmp" else 21.0
    tools.network.stability(count=3)
    store = tools.store
    a = store.begin("doctor", "startup.disable", "OneDrive", "disabled at start", undo={"value": "OneDrive.exe"})
    store.finish(a, True)
    b = store.begin("network", "dns.set", "Ethernet", "1.1.1.1", undo={"previous": {"dhcp": True}})
    store.finish(b, True)
    legacy = json.loads((DATA / "akeno-3.1.0-doctor-hardware.json").read_text(encoding="utf-8"))
    store.add_history("doctor.hardware", legacy, keep=50)    # the newest one: exactly what 3.1.0 wrote
    store.close()
    svc.db.close()
    return rows(akeno_home)


def rows(akeno_home: Path) -> dict:
    con = sqlite3.connect(akeno_home / "system.sqlite3")
    try:
        return {table: con.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("history", "actions", "kv")}
    finally:
        con.close()


def window(language: str):
    """AKENO's main window as a new start builds it, from what is stored."""
    from akeno import i18n
    from akeno.services import AppServices
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    i18n.set_language(language)
    svc = AppServices()
    return MainWindow(Controller(svc)), svc


def close(svc) -> None:
    from akeno.ui import workers
    workers.drain(30000)                     # background tasks the pages started finish before the stores close
    svc.system_tools.store.close()
    svc.db.close()


def grid_texts(grid) -> list[str]:
    from PySide6.QtWidgets import QLabel
    return [w.text() for w in grid.findChildren(QLabel)]


@pytest.fixture(autouse=True)
def fresh_failures():
    from akeno.ui import system_ui
    system_ui.STORED_FAILURES.clear()
    system_ui.SAFE_MODE["on"] = False
    yield
    system_ui.STORED_FAILURES.clear()
    system_ui.SAFE_MODE["on"] = False


def test_t_translates_messages_stored_as_json():
    from akeno import i18n
    from akeno.i18n import msg, t
    i18n.set_language("en")
    assert t({"t": CPU_NOTE_EN, "p": {}}) == CPU_NOTE_EN
    assert t({"t": "{p} % of the rated maximum", "p": {"p": 95}}) == "95 % of the rated maximum"
    assert t(None) == "" and t(42) == "42" and t("A {x}", y=1) == "A {x}"      # never raises
    i18n.set_language("de")
    try:
        assert t({"t": CPU_NOTE_EN, "p": {}}) == CPU_NOTE_DE
        assert t({"t": "{p} % of the rated maximum", "p": {"p": 95}}) == "95 % des Nennmaximums"
        assert t(msg("{p} % of the rated maximum", p={"t": "x", "p": {}})).startswith("x")   # nested
    finally:
        i18n.set_language("en")


def test_reading_note_survives_storage():
    from akeno.i18n import Msg
    from akeno.system.model import Reading
    r = Reading.missing(Msg(CPU_NOTE_EN))
    back = Reading.from_dict(json.loads(json.dumps(r.to_dict())))
    assert isinstance(back.note, Msg) and back.note.template == CPU_NOTE_EN


@pytest.mark.parametrize("language,note", [("en", CPU_NOTE_EN), ("de", CPU_NOTE_DE)])
def test_saved_hardware_report_without_cpu_sensor_builds_the_doctor(akeno_home, qtbot, language, note):
    before = seed(akeno_home, language)
    win, svc = window(language)
    qtbot.addWidget(win)
    win.show()
    for key in list(win.pages):
        win.go(key)
    from akeno.ui import system_ui
    assert system_ui.STORED_FAILURES == []
    doctor = win.pages["doctor"]
    texts = grid_texts(doctor.hardware.temp_grid)
    assert note in texts, texts                          # the missing CPU sensor, explained in the UI language
    assert doctor.hardware.table.rowCount() >= 1         # the drives from the saved report
    close(svc)
    assert rows(akeno_home)["history"][:len(before["history"])] == before["history"]
    assert rows(akeno_home)["actions"] == before["actions"]     # undo records untouched


@pytest.mark.parametrize("language", ["en", "de"])
def test_a_new_start_with_saved_reports_builds_every_page(akeno_home, language):
    """A real restart: a new AKENO process builds the suite from system.sqlite3 and opens every page."""
    before = seed(akeno_home, language)
    env = dict(os.environ, AKENO_HOME=str(akeno_home), QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([sys.executable, "-m", "akeno", "--check-start"], cwd=ROOT, env=env, capture_output=True,
                       text=True, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "start check OK" in p.stdout and "doctor" in p.stdout
    after = rows(akeno_home)
    assert after["actions"] == before["actions"]
    assert after["history"][:len(before["history"])] == before["history"]


def test_a_saved_result_that_cannot_be_shown_does_not_stop_the_start(akeno_home, qtbot, monkeypatch):
    seed(akeno_home, "en")
    from akeno.ui.pages import doctor
    monkeypatch.setattr(doctor.HardwareTab, "_show", lambda self, rep: 1 / 0)
    win, svc = window("en")
    qtbot.addWidget(win)
    from akeno.ui import system_ui
    assert any(f.startswith("doctor.hardware: ZeroDivisionError") for f in system_ui.STORED_FAILURES)
    assert "could not be shown" in win.pages["doctor"].hardware.task.text.text()
    assert svc.system_tools.store.latest("doctor.hardware") is not None    # kept
    close(svc)


def test_a_system_page_that_cannot_be_built_leaves_the_rest_of_akeno(akeno_home, qtbot, monkeypatch):
    from akeno.ui.pages import doctor

    def broken(self, ctl, window):
        raise RuntimeError("doctor page")
    monkeypatch.setattr(doctor.DoctorPage, "__init__", broken)
    win, svc = window("en")
    qtbot.addWidget(win)
    from akeno.ui.system_ui import STORED_FAILURES, UnavailablePage
    assert isinstance(win.pages["doctor"], UnavailablePage)
    win.go("doctor")
    win.pages["doctor"].link("cleaner")                   # what other pages call on it does nothing
    win.go("library")
    assert any("page doctor" in f for f in STORED_FAILURES)
    close(svc)


def test_safe_mode_shows_no_saved_results_and_keeps_them(akeno_home, qtbot):
    before = seed(akeno_home, "en")
    from akeno.ui import system_ui
    system_ui.SAFE_MODE["on"] = True
    win, svc = window("en")
    qtbot.addWidget(win)
    assert win.pages["doctor"].hardware.table.rowCount() == 0
    close(svc)
    after = rows(akeno_home)
    assert after["history"][:len(before["history"])] == before["history"] and after["actions"] == before["actions"]


def test_check_start_reports_what_cannot_be_shown(akeno_home, qtbot, monkeypatch):
    seed(akeno_home, "en")
    from akeno import app
    from akeno.ui.pages import doctor
    monkeypatch.setattr(doctor.HardwareTab, "_show", lambda self, rep: 1 / 0)
    win, svc = window("en")
    assert app.check_start(None, None, win) == 4
    assert "Not shown: doctor.hardware" in (akeno_home / "logs" / "check-start.txt").read_text(encoding="utf-8")
    close(svc)


def test_health_report_fails_when_the_suite_cannot_show_this_pcs_data(akeno_home, monkeypatch):
    from akeno.updater import service
    monkeypatch.setattr(service, "SUITE_CHECK", lambda: "doctor.hardware: TypeError: unhashable type: 'dict'")
    rep = service.health_report()
    assert rep["ok"] is False and "unhashable" in rep["checks"]["suite"]
    monkeypatch.setattr(service, "SUITE_CHECK", lambda: True)
    assert service.health_report()["checks"]["suite"] is True


def test_start_guard_counts_starts_that_never_got_their_window(akeno_home, monkeypatch):
    from akeno import startguard
    assert startguard.begin("3.1.1") == 0
    marker = json.loads(startguard.marker().read_text(encoding="utf-8"))
    marker["pid"] = 999999999                             # that start's process is gone: it crashed
    startguard.marker().write_text(json.dumps(marker), encoding="utf-8")
    assert startguard.begin("3.1.1") == 1                 # this start runs in safe mode
    startguard.done()
    assert not startguard.marker().exists()
    assert startguard.begin("3.1.1") == 0                 # the next start is normal again
    marker = json.loads(startguard.marker().read_text(encoding="utf-8"))
    marker["pid"] = 999999999
    startguard.marker().write_text(json.dumps(marker), encoding="utf-8")
    assert startguard.begin("3.1.2") == 0                 # another version (an update or a rollback) starts fresh


def test_a_crash_while_starting_is_written_to_the_log(akeno_home, capsys, monkeypatch):
    from akeno import startguard
    monkeypatch.delenv("AKENO_UPDATE_MARKER", raising=False)
    try:
        raise TypeError("unhashable type: 'dict'")
    except TypeError as exc:
        assert startguard.crashed(exc) == 1
    log = (akeno_home / "logs" / "crash.log").read_text(encoding="utf-8")
    assert "TypeError: unhashable type: 'dict'" in log and "Traceback" in log
    assert "safe mode" in capsys.readouterr().err
