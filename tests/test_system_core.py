"""AKENO 3.1 shared system infrastructure: tasks, commands, store, file-system safety, findings."""

import os
import sys
import threading
import time

import pytest

from akeno.system import fs, lnk
from akeno.system.model import Evidence, EvidenceKind, Finding, Level, Reading, Report, human_bytes
from akeno.system.store import SystemStore
from akeno.system.tasks import Cancelled, LimitReached, TaskContext, TimedOut


def test_task_context_cancel_and_timeout():
    ctx = TaskContext()
    ctx.check()
    ctx.cancel()
    with pytest.raises(Cancelled):
        ctx.check()
    clock = [0.0]
    ctx = TaskContext(timeout=5, now=lambda: clock[0])
    ctx.check()
    clock[0] = 5.1
    with pytest.raises(TimedOut):
        ctx.check()


def test_task_context_sleep_wakes_on_cancel():
    ctx = TaskContext()
    threading.Timer(0.2, ctx.cancel).start()
    t0 = time.monotonic()
    with pytest.raises(Cancelled):
        ctx.sleep(10)
    assert time.monotonic() - t0 < 2


def test_progress_reports_only_known_totals():
    seen = []
    ctx = TaskContext(progress=seen.append)
    ctx.report("scanning")
    ctx.report("files", 3, 10)
    assert seen[0].fraction is None and seen[1].fraction == pytest.approx(0.3)


def test_command_runner_timeout_and_cancel():
    from akeno.system import commands
    res = commands.run([sys.executable, "-c", "print('hi')"], timeout=20)
    assert res.ok and "hi" in res.stdout
    res = commands.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert res.timed_out and not res.ok
    ctx = TaskContext()
    threading.Timer(0.3, ctx.cancel).start()
    res = commands.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=20, ctx=ctx)
    assert res.cancelled
    with pytest.raises(commands.CommandUnavailable):
        commands.run(["not-an-absolute-tool"], timeout=1)


def test_powershell_is_refused_outside_windows():
    from akeno.system import commands
    if os.name == "nt":
        pytest.skip("Windows has PowerShell")
    with pytest.raises(commands.CommandUnavailable):
        commands.powershell_json("Get-Date")


def test_ps_quote_escapes_single_quotes():
    from akeno.system.commands import ps_quote
    assert ps_quote("C:\\It's\\x") == "'C:\\It''s\\x'"


def test_store_history_is_pruned_and_journal_records_before_running(tmp_path):
    s = SystemStore(tmp_path / "system.sqlite3")
    for i in range(12):
        s.add_history("k", {"i": i}, keep=5)
    rows = s.history("k", 50)
    assert [r["i"] for r in rows] == [11, 10, 9, 8, 7]
    aid = s.begin("doctor", "startup.disable", "x", "X", {"before": None})
    assert s.action(aid)["state"] == "planned"
    s.finish(aid, True)
    assert s.action(aid)["state"] == "done"
    s.mark_undone(aid)
    assert s.action(aid)["state"] == "undone"
    s.set("consent", True)
    assert s.get("consent") is True and s.get("missing", 3) == 3
    s.close()


def test_findings_keep_translatable_text_through_storage():
    from akeno import i18n
    from akeno.i18n import msg, t
    f = Finding("x", "doctor", Level.WARN, msg("Little free space on {d}", d="C:\\"))
    f.measured(msg("{pct:.1f} % free", pct=4.25))
    back = Finding.from_dict(f.to_dict())
    assert str(back.title) == "Little free space on C:\\"
    assert isinstance(back.title, i18n.Msg) and back.title.template == "Little free space on {d}"
    assert back.evidence[0].text.params == {"pct": 4.25}
    rep = Report("doctor", [f])
    assert Report.from_dict(rep.to_dict()).worst == Level.WARN


def test_reading_missing_has_no_value():
    r = Reading.missing("no sensor")
    assert not r.available and r.value is None and r.note == "no sensor"


def test_human_bytes():
    assert human_bytes(512) == "512 B"
    assert human_bytes(1536) == "1.5 KB"
    assert human_bytes(None) == "—"


# -- file-system safety -------------------------------------------------------------------------------------------
def test_walk_skips_links_and_stays_inside(tmp_path):
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "a" / "f.txt").write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("keep")
    try:
        os.symlink(outside, root / "link", target_is_directory=True)
        (root / "flink.txt").symlink_to(outside / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    errors = []
    found = [e.path for e in fs.walk(root, errors=errors)]
    assert found == [str(root / "a" / "f.txt")]
    assert any("not followed" in e for e in errors)
    assert not fs.within(root, root / "link" / "secret.txt")
    assert fs.within(root, root / "a" / "f.txt")
    assert not fs.within(root, root / "a" / ".." / ".." / "outside")


def test_walk_limits_and_cancel(tmp_path):
    for i in range(30):
        (tmp_path / f"{i}.tmp").write_text("x")
    with pytest.raises(LimitReached):
        list(fs.walk(tmp_path, max_files=10))
    ctx = TaskContext()
    ctx.cancel()
    with pytest.raises(Cancelled):
        list(fs.walk(tmp_path, ctx))


def test_identity_detects_change(tmp_path):
    p = tmp_path / "f"
    p.write_text("a")
    before = fs.identity(p)
    time.sleep(0.01)
    p.write_text("abc")
    assert fs.identity(p) != before
    assert fs.identity(tmp_path) is None


# -- shortcuts -------------------------------------------------------------------------------------------------------
def test_shortcut_roundtrip_and_garbage():
    data = lnk.build(r"C:\Games\Tool\tool.exe", "--fast")
    sc = lnk.parse(data)
    assert sc.target == r"C:\Games\Tool\tool.exe" and sc.arguments == "--fast"
    assert lnk.parse(b"not a shortcut") is None
    assert lnk.parse(data[:90]) is None or lnk.parse(data[:90]).target is None


def test_report_leaves_out_private_details(tmp_path):
    from akeno.system import report
    from akeno.system.store import SystemStore
    env = {"USERNAME": "PlayerOne", "COMPUTERNAME": "GAMING-RIG"}
    data = {"gateway": "192.168.178.1", "dns": ["1.1.1.1", "fd00::1"], "public_ip": "203.0.113.7",
            "text": "Router 192.168.178.1 at aa:bb:cc:dd:ee:ff answered PlayerOne on GAMING-RIG",
            "exe": "C:\\Users\\PlayerOne\\AppData\\Roaming\\svchost.exe", "path": "C:\\Games\\x",
            "signature_version": "1.439.120.0", "time": "2026-10-26T10:00:00", "serial": "S4EVNX0N",
            "url": "https://example.com/mod.zip"}
    out = report.scrub(data, env)
    assert out["gateway"] == "[address omitted]" and out["dns"] == ["1.1.1.1", "[address omitted]"]
    assert "public_ip" not in out and "serial" not in out and "path" not in out
    assert "aa:bb" not in out["text"] and "PlayerOne" not in out["text"] and "GAMING-RIG" not in out["text"]
    assert "PlayerOne" not in out["exe"]
    assert out["signature_version"] == "1.439.120.0" and out["time"] == "2026-10-26T10:00:00"
    assert out["url"] == "https://example.com/mod.zip"
    store = SystemStore(tmp_path / "s.sqlite3")
    store.add_history("security.scan", {"kind": "custom", "target": "C:\\Users\\PlayerOne\\Downloads",
                                        "status": "clean"})
    rep = report.build(store, "security", {"overview": {"title": {"t": "Virus protection is on", "p": {}}}}, env=env)
    assert rep["module"] == "security" and rep["overview"]["title"] == "Virus protection is on"
    assert "target" not in rep["security.scan"][0] and rep["security.scan"][0]["status"] == "clean"
    store.close()
