"""Security Center with stand-in providers: Defender on/off/passive, another antivirus, missing interfaces, scan
results, errors, cancellation, file checks, the process review and the download veto. No real antivirus."""

import io
import os
import struct
import threading
import zipfile

import pytest

from akeno.system import lnk
from akeno.system.model import Level
from akeno.system.securitycenter import defender, filecheck, processes
from akeno.system.securitycenter.defender import ScanResult
from akeno.system.securitycenter.providers import FakeSecurity, defender_active_status
from akeno.system.securitycenter.service import SecurityService, overview_findings
from akeno.system.signatures import Signature
from akeno.system.store import SystemStore
from akeno.system.tasks import Cancelled, TaskContext

DEFENDER = {"name": "Microsoft Defender Antivirus", "state": "on", "signature": "up_to_date", "default": True}
OTHER_AV = {"name": "Contoso Antivirus", "state": "on", "signature": "up_to_date", "default": True}
GOOD = {"firewall": "good", "antivirus": "good", "antispyware": "good", "autoupdate": "good"}


@pytest.fixture
def store(tmp_path):
    s = SystemStore(tmp_path / "system.sqlite3")
    yield s
    s.close()


def svc(store, **kw):
    kw.setdefault("health", GOOD)
    kw.setdefault("products", {"antivirus": [DEFENDER], "firewall": []})
    kw.setdefault("firewall", [{"name": n, "enabled": True} for n in ("Domain", "Private", "Public")])
    kw.setdefault("defender_status", defender_active_status())
    kw.setdefault("history", [])
    return SecurityService(store, FakeSecurity(**kw))


def ids(report):
    return {f.id for f in report.findings}


def pe_bytes(dll=False):
    """A minimal PE header (enough for the type sniffer and the PE parser)."""
    mz = bytearray(b"MZ" + b"\0" * 62)
    struct.pack_into("<I", mz, 0x3C, 64)
    hdr = b"PE\0\0" + struct.pack("<HHIIIHH", 0x8664, 0, 0, 0, 0, 0, 0x2002 if dll else 0x0002)
    return bytes(mz) + hdr + b"\0" * 64


# -- status ---------------------------------------------------------------------------------------------------
def test_defender_active_and_healthy(store):
    rep = svc(store).overview()
    assert {"sec.av.on", "sec.firewall.on"} <= ids(rep)
    assert rep.worst in (Level.OK, Level.INFO)
    assert svc(store).defender_ready()[0]


def test_defender_realtime_off_and_old_signatures(store):
    st = defender_active_status(realtime=False, signature_age_days=12, tamper=False, computer_state=["reboot"])
    rep = svc(store, defender_status=st, health={**GOOD, "antivirus": "poor"}).overview()
    found = ids(rep)
    assert {"sec.av.realtime", "sec.defender.signatures", "sec.defender.tamper",
            "sec.defender.pending.reboot"} <= found
    assert "sec.av.poor" not in found                       # one finding per problem, not two
    assert rep.worst == Level.PROBLEM


def test_third_party_antivirus_is_respected(store):
    st = defender_active_status(mode="passive", antivirus=False, realtime=False)
    s = svc(store, products={"antivirus": [OTHER_AV, {**DEFENDER, "state": "off"}]}, defender_status=st)
    rep = s.overview()
    found = ids(rep)
    assert {"sec.av.on", "sec.av.third_party", "sec.defender.passive"} <= found
    assert "sec.av.realtime" not in found                   # Defender being passive is no problem here
    third = next(f for f in rep.findings if f.id == "sec.av.third_party")
    assert "Contoso" in str(third.title)
    assert s.defender_ready()[0]                            # on-demand scans are allowed in passive mode


def test_defender_off_with_no_other_antivirus(store):
    rep = svc(store, health={**GOOD, "antivirus": "poor"},
              products={"antivirus": [{**DEFENDER, "state": "off"}]},
              defender_status=defender_active_status(antivirus=False, realtime=False)).overview()
    assert "sec.av.poor" in ids(rep) and rep.worst == Level.PROBLEM


def test_defender_missing_scan_is_unavailable(store):
    s = svc(store, defender_status=None, products={"antivirus": [OTHER_AV]})
    ok, reason = s.defender_ready()
    assert not ok and reason
    res = s.scan("quick")
    assert res.status == "unavailable" and s.provider.scans == []
    v = s.verdict(str(store.path))
    assert v["status"] == "not_checked"


def test_interfaces_missing_everywhere(store):
    rep = SecurityService(store, FakeSecurity()).overview()
    assert ids(rep) == {"sec.unavailable"} and rep.worst == Level.UNKNOWN
    assert rep.errors                                         # each missing interface is reported


def test_firewall_off_is_reported_never_changed(store):
    rep = svc(store, health={**GOOD, "firewall": "poor"},
              firewall=[{"name": "Public", "enabled": False}, {"name": "Private", "enabled": True}]).overview()
    f = next(f for f in rep.findings if f.id == "sec.firewall.off")
    assert f.level == Level.PROBLEM and "Public" in str(f.observation)
    assert f.links[0].target == "uri:network"                 # Windows Security opens; AKENO switches nothing


def test_open_and_recent_threats(store):
    hist = [{"name": "Trojan:Win32/Test", "status": "detected", "unresolved": True, "resources": ["C:\\x.exe"],
             "time": "2026-10-20T10:00:00+00:00"},
            {"name": "PUA:Win32/Bundler", "status": "quarantined", "unresolved": False, "resources": [],
             "time": "2099-01-01T00:00:00+00:00"}]
    rep = svc(store, history=hist).overview()
    assert "sec.threats.open" in ids(rep)


def test_combine_history_and_resources():
    det = [{"ThreatID": 7, "ThreatStatusID": 3, "Resources": ["file:_C:\\Games\\bad.exe", "file:_\\\\?\\C:\\a.zip->b.exe"],
            "Time": "2026-10-01T10:00:00Z", "ProcessName": "Unknown"},
           {"ThreatID": 8, "ThreatStatusID": 1, "Resources": "file:_C:\\x.dll", "Time": "2026-10-02T10:00:00Z"}]
    thr = [{"ThreatID": 7, "ThreatName": "Trojan:Win32/A", "SeverityID": 5, "IsActive": False},
           {"ThreatID": 8, "ThreatName": "HackTool:Win32/B", "SeverityID": 4, "IsActive": True}]
    h = defender.combine_history(det, thr)
    assert [e["name"] for e in h] == ["HackTool:Win32/B", "Trojan:Win32/A"]
    assert h[0]["unresolved"] and not h[1]["unresolved"]
    assert h[1]["resources"] == ["C:\\Games\\bad.exe", "C:\\a.zip"] and h[1]["severity"] == "severe"


def test_status_normalisation():
    st = defender.normalise_status({"AMServiceEnabled": True, "AntivirusEnabled": True, "AMRunningMode": "Passive Mode",
                                    "QuickScanAge": 4294967295, "AntivirusSignatureAge": 2, "ComputerState": 6})
    assert st["mode"] == "passive" and st["quick_scan_age_days"] is None and st["signature_age_days"] == 2
    assert st["computer_state"] == ["reboot", "manual_steps"]
    assert defender.usable(None)[0] is False
    assert defender.usable({"service": True, "mode": "off"})[0] is False


# -- scans ------------------------------------------------------------------------------------------------------
REPORT_ONLY_OUTPUT = """Scan starting...
Scan finished.
Scanning C:\\t\\eicar.com found 1 threats.

<===========================LIST OF DETECTED THREATS==========================>
----------------------------- Threat information ------------------------------
Threat                  : Virus:DOS/EICAR_Test_File
Resources               : 1 total
    file                : C:\\t\\eicar.com
-------------------------------------------------------------------------------
"""


def test_parse_scan_output():
    assert defender.parse_scan_output("Scan starting...\nScan finished.\nScanning C:\\t found no threats.\n") == (0, [])
    found, threats = defender.parse_scan_output(REPORT_ONLY_OUTPUT)
    assert found == 1 and threats == [{"name": "Virus:DOS/EICAR_Test_File", "resources": ["C:\\t\\eicar.com"]}]


def _fake_mpcmdrun(tmp_path, rc, out):
    """A stand-in MpCmdRun: a Python script behind an absolute path (commands.run needs a real program)."""
    import sys
    script = tmp_path / "mp.py"
    script.write_text(f"import sys\nsys.stdout.write({out!r})\nsys.exit({rc})\n")
    return sys.executable, str(script)


@pytest.mark.parametrize("rc,out,status", [(0, "Scan finished.\nScanning C:\\t found no threats.\n", "clean"),
                                           (2, REPORT_ONLY_OUTPUT, "threats"),
                                           (0x80508023 & 0x7FFFFFFF, "Scan failed\n", "error")])
def test_scan_exit_codes(tmp_path, monkeypatch, rc, out, status):
    exe, script = _fake_mpcmdrun(tmp_path, rc, out)
    calls = []
    from akeno.system import commands

    def fake_run(args, timeout=30.0, ctx=None, **kw):
        calls.append(args)
        return commands.run([exe, script], timeout, ctx)
    monkeypatch.setattr(defender, "run", fake_run)
    res = defender.scan("custom", str(tmp_path), report_only=True, exe=exe)
    assert res.status == status
    assert "-DisableRemediation" in calls[0] and calls[0][calls[0].index("-File") + 1] == os.path.normpath(str(tmp_path))
    if status == "threats":
        assert res.threats[0]["name"] == "Virus:DOS/EICAR_Test_File"


def test_quick_scan_never_report_only(monkeypatch, tmp_path):
    seen = []
    from akeno.system.commands import CommandResult
    monkeypatch.setattr(defender, "run", lambda args, timeout=30.0, ctx=None, **kw:
                        seen.append(args) or CommandResult(0, "found no threats", "", False, False))
    defender.scan("quick", report_only=True, exe=str(tmp_path / "MpCmdRun.exe"))
    assert seen[0][1:4] == ["-Scan", "-ScanType", "1"] and "-DisableRemediation" not in seen[0]


def test_cancelled_scan(store):
    s = svc(store, scan_delay=5.0)
    ctx = TaskContext()
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", s.scan("full", ctx=ctx)))
    t.start()
    ctx.cancel()
    t.join(5)
    assert out["r"].status == "cancelled"


def test_scan_error_and_permission(store):
    s = svc(store, scan_results={"quick": ScanResult("quick", None, "error", detail="Access denied.")},
            update_error=defender.QueryError("Update-MpSignature: Access denied"))
    assert s.scan("quick").status == "error"
    with pytest.raises(defender.QueryError):
        s.update_signatures()
    assert s.scans()[0]["status"] == "error"                  # recorded in the local history


def test_mpcmdrun_prefers_newest_platform(tmp_path):
    plat = tmp_path / "ProgramData" / "Microsoft" / "Windows Defender" / "Platform"
    for v in ("4.18.2001.10-0", "4.18.25090.3009-0", "4.18.9000.1-0"):
        (plat / v).mkdir(parents=True)
        (plat / v / "MpCmdRun.exe").write_bytes(b"")
    exe = defender.mpcmdrun({"ProgramData": str(tmp_path / "ProgramData"), "ProgramFiles": str(tmp_path / "PF")})
    assert "4.18.25090.3009-0" in exe
    assert defender.mpcmdrun({"ProgramData": str(tmp_path / "none"), "ProgramFiles": str(tmp_path / "PF")}) is None


# -- file checks ------------------------------------------------------------------------------------------------
def _scanner(threat_files=()):
    def scan(path, ctx):
        hits = [{"name": "Trojan:Win32/Test", "resources": [f]} for f in threat_files
                if os.path.normcase(f).startswith(os.path.normcase(path))]
        return {"status": "threats" if hits else "clean", "engine": "Microsoft Defender", "threats": hits}
    return scan


def _signer(mapping=None):
    return lambda paths, ctx: {p: (mapping or {}).get(os.path.basename(p), Signature("unsigned")) for p in paths}


def test_file_check_types_and_threat(tmp_path):
    (tmp_path / "tool.exe").write_bytes(pe_bytes())
    (tmp_path / "readme.txt.exe").write_bytes(pe_bytes())
    (tmp_path / "picture.png").write_bytes(pe_bytes())
    (tmp_path / "texture.dds").write_bytes(b"DDS " + b"\0" * 100)
    bad = tmp_path / "bad.dll"
    bad.write_bytes(pe_bytes(dll=True))
    reports, errors = filecheck.check([str(tmp_path)], _scanner([str(bad)]), _signer())
    by = {r.name: r for r in reports}
    assert "texture.dds" not in by                            # data files go to the antivirus scan only
    assert by["bad.dll"].kind == "library" and by["bad.dll"].level == Level.PROBLEM
    assert by["tool.exe"].level == Level.OK                    # unsigned alone is no warning
    assert by["tool.exe"].sha256 and len(by["tool.exe"].sha256) == 64
    assert {f.id.rsplit(".", 1)[1] for f in by["readme.txt.exe"].findings} == {"double"}
    assert by["picture.png"].kind == "program" and any(f.id.endswith(".decoy") for f in by["picture.png"].findings)
    assert reports[0].name == "bad.dll"                        # worst first


def test_signature_states(tmp_path):
    for n in ("signed.exe", "broken.exe", "plain.exe"):
        (tmp_path / n).write_bytes(pe_bytes())
    sigs = {"signed.exe": Signature("valid", "NVIDIA Corporation"), "broken.exe": Signature("invalid", detail="HashMismatch")}
    reports, _ = filecheck.check([str(tmp_path)], _scanner(), _signer(sigs))
    by = {r.name: r for r in reports}
    assert by["broken.exe"].level == Level.WARN
    assert by["signed.exe"].level == Level.OK and by["plain.exe"].level == Level.OK
    rows = dict(filecheck.summary_rows(by["plain.exe"]))
    assert "not a sign of malware" in str(rows["Signature"])


def test_archive_contents_minecraft_jar(tmp_path):
    jar = tmp_path / "coolmod.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("fabric.mod.json", "{}")
        z.writestr("com/example/Mod.class", b"\xca\xfe\xba\xbe")
        z.writestr("natives/discord.dll", b"MZ")
        z.writestr("payload/update.exe", b"MZ")
        z.writestr("readme.pdf.scr", b"MZ")
    reports, _ = filecheck.check([str(jar)], _scanner(), _signer(), minecraft=True)
    r = reports[0]
    kinds = {f.id.rsplit(".", 1)[1]: f.level for f in r.findings}
    assert r.kind == "java_archive"
    assert kinds["risky"] == Level.WARN and kinds["programs"] == Level.WARN and kinds["native"] == Level.INFO


def test_game_mod_zip_with_installer_is_info(tmp_path):
    z = tmp_path / "reshade-preset.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Setup.exe", b"MZ")
        zf.writestr("preset.ini", "x")
    reports, _ = filecheck.check([str(z)], _scanner(), _signer())
    f = next(f for f in reports[0].findings if f.id.endswith(".programs"))
    assert f.level == Level.INFO


def test_zip_bomb_is_refused_without_extraction(tmp_path):
    (tmp_path / "w").mkdir()
    bomb = tmp_path / "w" / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a.bin", b"\0" * (50 * 2 ** 20))
    reports, _ = filecheck.check([str(bomb)], _scanner(), _signer())
    assert reports[0].errors and "zip bomb" in reports[0].errors[0]
    assert sorted(os.listdir(tmp_path / "w")) == ["bomb.zip"]  # nothing was unpacked


def test_shortcut_to_interpreter(tmp_path):
    (tmp_path / "Invoice.lnk").write_bytes(lnk.build(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                                                     "-w hidden -enc AAAA"))
    (tmp_path / "Game.lnk").write_bytes(lnk.build(r"C:\Games\Game\game.exe"))
    reports, _ = filecheck.check([str(tmp_path)], _scanner(), _signer())
    by = {r.name: r for r in reports}
    assert any(f.id.endswith(".lnk") for f in by["Invoice.lnk"].findings)
    assert by["Game.lnk"].findings == []


def test_antivirus_unavailable_is_said_not_hidden(tmp_path):
    (tmp_path / "mod.dll").write_bytes(pe_bytes(dll=True))
    reports, _ = filecheck.check([str(tmp_path / "mod.dll")],
                                 lambda p, c: {"status": "not_checked", "engine": "", "threats": [],
                                               "detail": "Microsoft Defender does not answer"}, _signer())
    rows = dict(filecheck.summary_rows(reports[0]))
    assert "Not checked" in str(rows["Antivirus"])
    assert reports[0].level == Level.OK                        # no invented verdict


def test_detection_in_skipped_file_still_shows(tmp_path):
    hidden = tmp_path / "data.bin"
    hidden.write_bytes(b"\0" * 10)
    reports, _ = filecheck.check([str(tmp_path)], _scanner([str(hidden)]), _signer())
    assert any(r.name == "data.bin" and r.level == Level.PROBLEM for r in reports)


def test_cancelled_file_check(tmp_path):
    for i in range(20):
        (tmp_path / f"f{i}.exe").write_bytes(pe_bytes())
    ctx = TaskContext()
    ctx.cancel()
    with pytest.raises(Cancelled):
        filecheck.check([str(tmp_path)], _scanner(), _signer(), ctx)


def test_double_extension_rules():
    assert filecheck.double_extension("Rechnung.pdf.exe")
    assert filecheck.double_extension("photo.jpg .scr")
    assert not filecheck.double_extension("setup.exe")
    assert not filecheck.double_extension("my.mod.v2.jar")
    assert not filecheck.double_extension("dxgi.dll")


# -- processes ------------------------------------------------------------------------------------------------
ENV = {"SystemRoot": r"C:\Windows", "TEMP": r"C:\Users\p\AppData\Local\Temp", "USERPROFILE": r"C:\Users\p",
       "LOCALAPPDATA": r"C:\Users\p\AppData\Local", "APPDATA": r"C:\Users\p\AppData\Roaming",
       "ProgramFiles": r"C:\Program Files", "ProgramData": r"C:\ProgramData"}


def P(pid, exe, conns=0):
    return processes.ProcessInfo(pid, exe.rsplit("\\", 1)[-1], exe, connections=conns)


def test_process_rules():
    procs = [P(1, r"C:\Windows\System32\svchost.exe"),
             P(2, r"C:\Users\p\AppData\Roaming\svchost.exe"),
             P(3, r"C:\Users\p\AppData\Local\Temp\scvhost.exe"),
             P(4, r"C:\Users\p\Downloads\tool.exe", conns=3),
             P(5, r"C:\Users\p\Downloads\portable.exe"),
             P(6, r"C:\Program Files\Steam\steam.exe", conns=20),
             P(7, r"C:\Users\p\Documents\cv.pdf.exe"),
             P(8, r"C:\ProgramData\Microsoft\Windows Defender\Platform\4.18.1-0\MsMpEng.exe"),
             P(9, r"C:\Windows\explorer.exe")]
    rep = processes.analyse(procs, lambda paths, ctx: {p: Signature("unsigned") for p in paths}, ENV)
    flagged = {f.data["pid"]: f.id.split(".")[1] for f in rep.findings if f.data}
    assert flagged == {2: "masquerade", 3: "lookalike", 4: "temp_net", 7: "double"}
    assert all(f.level == Level.WARN for f in rep.findings)
    assert all(any(link.target.startswith("file:") for link in f.links) for f in rep.findings)


def test_process_detected_by_defender_and_clean_case():
    exe = r"C:\Games\x\cheat.exe"
    rep = processes.analyse([P(1, exe)], lambda paths, ctx: {}, ENV,
                            detections=[{"unresolved": True, "resources": [exe]}])
    assert rep.findings[0].level == Level.PROBLEM
    rep = processes.analyse([P(1, r"C:\Program Files\App\app.exe")], lambda paths, ctx: {}, ENV)
    assert [f.id for f in rep.findings] == ["proc.none"]


def test_lookalike_needs_one_edit():
    assert processes.lookalike("scvhost.exe") == "svchost.exe"
    assert processes.lookalike("svchost.exe") is None
    assert processes.lookalike("service.exe") is None           # a common name of real programs
    assert processes.lookalike("steam.exe") is None


# -- download protection ----------------------------------------------------------------------------------------
class _Opener:
    def __init__(self, data):
        self.data = data

    def open(self, req, timeout=30):
        data = self.data

        class R(io.BytesIO):
            headers = {"Content-Length": str(len(data))}

            def geturl(self):
                return req.full_url

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R(data)


def test_download_veto_on_threat_and_pass_otherwise(tmp_path):
    import hashlib
    from akeno.security.downloads import DownloadManager, ThreatDetected
    data = b"payload" * 100
    sha = hashlib.sha256(data).hexdigest()
    dm = DownloadManager(["example.com"], cache=tmp_path / "dl", opener=_Opener(data))
    seen = []
    dm.antivirus = lambda path, token: seen.append(path) or {"status": "threats", "engine": "Microsoft Defender",
                                                             "threats": [{"name": "Trojan:Win32/X"}]}
    with pytest.raises(ThreatDetected) as e:
        dm.fetch("https://example.com/a.zip", sha, "a.zip", len(data))
    assert "Trojan:Win32/X" in str(e.value)
    assert dm.cached_path(sha, "a.zip").is_file()               # AKENO deletes nothing; Defender decides
    dm.antivirus = lambda path, token: {"status": "not_checked", "detail": "no antivirus"}
    assert dm.fetch("https://example.com/a.zip", sha, "a.zip", len(data)).from_cache
    dm.antivirus = lambda path, token: seen.append(path) or {"status": "clean"}
    dm.fetch("https://example.com/a.zip", sha, "a.zip", len(data))
    n = len(seen)
    dm.fetch("https://example.com/a.zip", sha, "a.zip", len(data))
    assert len(seen) == n                                       # a clean verdict is remembered for this hash


def test_download_hash_check_runs_before_antivirus(tmp_path):
    from akeno.security.downloads import DownloadManager, VerificationError
    dm = DownloadManager(["example.com"], cache=tmp_path / "dl", opener=_Opener(b"tampered"))
    dm.antivirus = lambda path, token: pytest.fail("the antivirus must not see unverified files")
    with pytest.raises(VerificationError):
        dm.fetch("https://example.com/a.zip", "0" * 64, "a.zip", 8)


def test_stack_explains_threat():
    from akeno.security.downloads import ThreatDetected
    from akeno.stack.errors import explain
    err = explain(ThreatDetected("Microsoft Defender reports X in a.zip. AKENO does not install it.", ["X"]))
    assert err.code == "threat_detected" and "false positive" in err.remedy


def test_check_download_records_history(store, tmp_path):
    s = svc(store, scan_results={"custom": ScanResult("custom", None, "threats",
                                                      threats=[{"name": "Trojan:Win32/X", "resources": []}])})
    v = s.check_download(str(tmp_path), None, "a.zip")
    assert v["status"] == "threats"
    assert s.provider.scans == [("custom", str(tmp_path), True)]  # report-only: Defender changes nothing
    assert store.history("security.download")[0]["threats"] == ["Trojan:Win32/X"]
    s2 = svc(store, defender_status=None)
    assert s2.check_download(str(tmp_path))["status"] == "not_checked"


def test_overview_findings_without_health_but_defender():
    st = {"health": None, "antivirus": None, "firewall": None, "defender": defender_active_status(),
          "history": [], "errors": {"health": "wscapi missing"}}
    assert any(f.id == "sec.av.on" for f in overview_findings(st))
