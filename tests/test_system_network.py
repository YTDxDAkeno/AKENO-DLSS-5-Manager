"""Network Lab with stand-in probes: the interpretation of real-world patterns (no internet, DNS down, ICMP
blocked, Wi-Fi trouble, loss, jitter, timeouts) and the safety of every change. Real Windows ICMP, WLAN and
adapter behaviour is exercised on the Windows CI runner (tools/system_check.py), not here."""

import socket
import struct
import threading
import time

import pytest

from akeno.system.model import EvidenceKind, Level
from akeno.system.network import bufferbloat, dns, gaming, info, repair, stability
from akeno.system.network.probe import compute, tcp_ping
from akeno.system.store import SystemStore


def test_jitter_and_stats():
    st = compute([10.0, 12.0, None, 11.0, 30.0, 10.0])
    assert st.sent == 6 and st.received == 5 and st.loss_percent == pytest.approx(16.7)
    assert st.min == 10.0 and st.max == 30.0 and st.median == 11.0
    # |12-10| + |11-12| + |30-11| + |10-30| = 2 + 1 + 19 + 20 = 42 over 4 differences
    assert st.jitter == pytest.approx(10.5)
    assert st.spikes == 0                      # 30 is not more than twice the median and 20 ms above it
    assert compute([10, 10, 10, 50]).spikes == 1
    assert compute([]).loss_percent is None and compute([None, None]).loss_percent == 100.0


def _prober(table):
    """table: (method, target-prefix) -> function(i) -> rtt|None or an exception."""
    counters = {}
    lock = threading.Lock()

    def probe(method, target):
        key = next((k for k in table if k[0] == method and target.startswith(k[1])), None)
        with lock:
            i = counters[(method, target)] = counters.get((method, target), -1) + 1
        if key is None:
            return None
        v = table[key]
        if isinstance(v, Exception):
            raise v
        return v(i)
    return probe


def _plan(**kw):
    return stability.Plan(kw.pop("gateway", "192.168.1.1"), count=kw.pop("count", 12), interval=0, **kw)


def test_stable_connection():
    p = _prober({("icmp", "192.168"): lambda i: 1.0, ("icmp", ""): lambda i: 15.0 + (i % 2),
                 ("tcp", ""): lambda i: 18.0})
    rep = stability.run(_plan(), p, resolve=lambda n: True)
    assert [f.id for f in rep.findings] == ["net.stable"]


def test_no_internet():
    p = _prober({("icmp", "192.168"): lambda i: 1.0})
    rep = stability.run(_plan(), p, resolve=lambda n: False)
    f = rep.findings[0]
    assert f.id == "net.offline" and "router answers" in str(f.evidence[0].text)


def test_icmp_blocked_is_not_an_outage():
    p = _prober({("icmp", "192.168"): lambda i: 1.0, ("tcp", ""): lambda i: 20.0})
    rep = stability.run(_plan(), p, resolve=lambda n: True)
    ids = [f.id for f in rep.findings]
    assert "net.icmp_blocked" in ids and "net.offline" not in ids
    assert next(f for f in rep.findings if f.id == "net.icmp_blocked").level == Level.INFO


def test_dns_problem_detected():
    p = _prober({("icmp", ""): lambda i: 10.0, ("tcp", ""): lambda i: 12.0})
    rep = stability.run(_plan(), p, resolve=lambda n: False)
    assert "net.dns" in [f.id for f in rep.findings]


def test_local_wifi_problem_vs_external():
    lossy = lambda i: None if i % 4 == 0 else 3.0 + (i % 3) * 25      # loss and jitter at the router
    p = _prober({("icmp", "192.168"): lossy, ("icmp", ""): lambda i: None if i % 4 == 0 else 20.0 + (i % 3) * 25,
                 ("tcp", ""): lambda i: 22.0})
    rep = stability.run(_plan(), p, resolve=lambda n: True)
    local = next(f for f in rep.findings if f.id == "net.local")
    assert local.level == Level.PROBLEM and "router" in str(local.observation)
    # external: router steady, internet with loss and spikes
    p = _prober({("icmp", "192.168"): lambda i: 1.0, ("icmp", ""): lambda i: None if i % 3 == 0 else 20.0 + (i % 2) * 60,
                 ("tcp", ""): lambda i: None if i % 3 == 0 else 22.0 + (i % 2) * 60})
    rep = stability.run(_plan(), p, resolve=lambda n: True)
    ext = next(f for f in rep.findings if f.id == "net.internet")
    assert any(e.kind == EvidenceKind.POSSIBLE_CAUSE and "beyond" in str(e.text) for e in ext.evidence)
    assert "net.local" not in [f.id for f in rep.findings]


def test_timeouts_and_icmp_unavailable_are_handled():
    from akeno.system.network.probe import IcmpUnavailable
    p = _prober({("icmp", ""): IcmpUnavailable("ICMP needs Windows"), ("tcp", ""): lambda i: 25.0})
    rep = stability.run(_plan(gateway=None), p, resolve=lambda n: True)
    assert rep.findings[0].id == "net.stable", "TCP alone still measures the connection"
    p = _prober({("tcp", ""): OSError("timed out")})
    rep = stability.run(_plan(gateway=None), p, resolve=lambda n: True)
    assert rep.findings[0].id == "net.offline"


def test_custom_game_server_dropping_icmp():
    p = _prober({("icmp", "192.168"): lambda i: 1.0, ("icmp", "1."): lambda i: 10.0, ("icmp", "8."): lambda i: 10.0,
                 ("icmp", "9."): lambda i: 10.0, ("tcp", ""): lambda i: 12.0})
    rep = stability.run(_plan(extra="203.0.113.7"), p, resolve=lambda n: True)
    assert "net.custom_icmp" in [f.id for f in rep.findings]


def test_tcp_ping_ipv4_and_ipv6_loopback():
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            srv = socket.socket(family, socket.SOCK_STREAM)
            srv.bind((host, 0))
        except OSError:
            continue
        srv.listen(1)
        port = srv.getsockname()[1]
        assert tcp_ping(host, port, 2) is not None
        srv.close()
        assert tcp_ping(host, port, 0.5) is None


# -- DNS --------------------------------------------------------------------------------------------------------
def test_dns_packet_roundtrip():
    q = dns.build_query("store.steampowered.com", 0x1234)
    assert q[:2] == b"\x12\x34" and b"\x05store\x0csteampowered\x03com\x00" in q
    reply = struct.pack(">HHHHHH", 0x1234, 0x8180, 1, 1, 0, 0)
    assert dns.parse_reply(reply, 0x1234) == (True, 0, 1)
    assert dns.parse_reply(reply, 0x9999)[0] is False
    with pytest.raises(ValueError):
        dns.parse_reply(b"\x00", 1)


def test_dns_query_against_local_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]

    def serve():
        data, addr = srv.recvfrom(512)
        srv.sendto(b"\xff\xff" + data[2:4] + b"\x00\x00" * 4, addr)          # wrong id: ignored
        srv.sendto(data[:2] + struct.pack(">HHHHH", 0x8183, 1, 0, 0, 0), addr)  # NXDOMAIN is a valid answer
    th = threading.Thread(target=serve, daemon=True)
    th.start()

    class Sock(socket.socket):
        def sendto(self, data, addr):
            return super().sendto(data, ("127.0.0.1", port))
    ms, why = dns.query("127.0.0.1", "x.example.com", 2, lambda *a: Sock(*a))
    assert why == "ok" and ms is not None
    srv.close()


def test_dns_benchmark_ranks_by_reliability_then_speed():
    def lookup(server, name):
        if server == "9.9.9.9":
            return None, "timeout"
        return (5.0 if server == "1.1.1.1" else 20.0), "ok"
    res = dns.benchmark([("A", "1.1.1.1", False), ("B", "8.8.8.8", True), ("C", "9.9.9.9", False)], rounds=2,
                        uncached=2, lookup=lookup)
    assert [r["server"] for r in res] == ["1.1.1.1", "8.8.8.8", "9.9.9.9"]
    assert res[2]["answered_percent"] == 0 and res[2]["errors"]["timeout"] > 0
    assert res[1]["system"] is True


def test_dns_change_is_journalled_and_restorable(tmp_path):
    store = SystemStore(tmp_path / "s.sqlite3")
    calls = []
    runner = lambda cmd: calls.append(cmd) or 0
    before = dns.current_setting(7, reader=lambda i: "")
    assert before == {"dhcp": True, "servers": []}
    assert dns.apply_servers(7, ["1.1.1.1", "1.0.0.1"], store, before, runner) == 0
    assert "-ServerAddresses '1.1.1.1','1.0.0.1'" in calls[0]
    aid = store.actions("network")[0]["id"]
    dns.restore(aid, store, runner)
    assert calls[1].endswith("-ResetServerAddresses")
    assert store.action(aid)["state"] == "undone"
    with pytest.raises(ValueError):
        dns.apply_servers(7, ["1.1.1.1; Remove-Item C:\\"], store, before, runner)
    static = dns.current_setting(7, reader=lambda i: "192.168.1.1,9.9.9.9")
    assert static == {"dhcp": False, "servers": ["192.168.1.1", "9.9.9.9"]}
    store.close()


def test_dns_change_declined_uac_is_not_done(tmp_path):
    store = SystemStore(tmp_path / "s.sqlite3")
    assert dns.apply_servers(3, ["8.8.8.8"], store, {"dhcp": True}, lambda cmd: None) is None
    assert store.actions("network")[0]["state"] == "failed"
    store.close()


# -- bufferbloat ------------------------------------------------------------------------------------------------
class FakeLoad:
    def __init__(self):
        self.directions = []

    def run(self, direction, limits, stop):
        self.directions.append(direction)
        stop.wait(5)                           # like a real load: runs until the phase's time limit
        return {"bytes": 1e6, "seconds": 0.3, "mbps": 26.7, "errors": 0.0, "error": ""}


def test_bufferbloat_needs_consent_and_reports_added_latency():
    with pytest.raises(PermissionError):
        bufferbloat.run(lambda m, t: 10.0, "1.1.1.1:443", False)
    state = {"load": False}
    load = FakeLoad()
    orig = load.run

    def run(direction, limits, stop):
        state["load"] = True
        try:
            return orig(direction, limits, stop)
        finally:
            state["load"] = False
    load.run = run
    prober = lambda m, t: 150.0 if state["load"] else 20.0
    limits = bufferbloat.Limits(seconds=0.6, idle_seconds=0.5)
    rep = bufferbloat.run(prober, "1.1.1.1:443", True, limits, loader=load)
    assert load.directions == ["down", "up"]
    f = rep.findings[0]
    assert f.id == "net.bufferbloat" and rep.data["grade"] in ("C", "D")
    assert f.level == Level.WARN and any(link.target == "gaming" for link in f.links)
    assert bufferbloat.grade(5) == "A+" and bufferbloat.grade(45) == "B" and bufferbloat.grade(None) == "?"


def test_bufferbloat_limits_are_bounded():
    lim = bufferbloat.Limits()
    assert lim.seconds <= 15 and lim.total_bytes <= 300 * 2 ** 20


def test_upload_body_stops_at_limit_and_on_stop():
    stop = threading.Event()
    counted = []
    body = bufferbloat._Body(100_000, stop, counted.append)
    total = 0
    while True:
        chunk = body.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
    assert total == 100_000 and sum(counted) == 100_000
    body = bufferbloat._Body(10 ** 9, stop, counted.append)
    stop.set()
    assert body.read() == b""


# -- gaming mode -----------------------------------------------------------------------------------------------------
def test_gaming_mode_never_pauses_protected_or_the_games_launcher():
    sample = {"processes": [{"pid": 1, "name": "steam.exe", "known": True, "io_bps": 9e6},
                            {"pid": 2, "name": "OneDrive.exe", "known": True, "io_bps": 1e6},
                            {"pid": 3, "name": "MsMpEng.exe", "known": False, "io_bps": 5e6},
                            {"pid": 4, "name": "svchost.exe", "known": True, "io_bps": 5e6}]}
    names = [c["name"] for c in gaming.candidates(sample, running_game_launcher="steam")]
    assert names == ["OneDrive.exe"]
    names = [c["name"] for c in gaming.candidates(sample, running_game_launcher=None)]
    assert names == ["steam.exe", "OneDrive.exe"]


def test_gaming_mode_pause_and_restore_same_process_only(tmp_path):
    import os
    import subprocess
    import sys
    store = SystemStore(tmp_path / "s.sqlite3")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        suspended, resumed = [], []
        gm = gaming.GamingMode(store, lambda pid: suspended.append(pid) or True, lambda pid: resumed.append(pid) or True)
        out = gm.pause([{"pid": proc.pid, "name": "helper.exe"}, {"pid": 4, "name": "csrss.exe"}],
                       before={"stats": {"median": 30}})
        assert [e["pid"] for e in out["paused"]] == [proc.pid] and out["refused"][0]["name"] == "csrss.exe"
        assert gm.active() and store.actions("network")[0]["kind"] == "gaming.pause"
        res = gm.restore()
        assert resumed == [proc.pid] and not gm.active() and res["gone"] == []
        assert store.actions("network")[0]["state"] == "undone"
    finally:
        proc.kill()
        proc.wait()
    gm.pause([{"pid": proc.pid, "name": "helper.exe"}])          # already ended: refused, nothing recorded
    assert not gm.active()
    store.close()


# -- repair -------------------------------------------------------------------------------------------------------
def test_repair_diagnosis_patterns():
    ok = info.Adapter("Ethernet", up=True, kind="ethernet", speed_mbps=1000, ipv4=["192.168.1.20"],
                      gateway=["192.168.1.1"], dns=["192.168.1.1"], errin=0, errout=0, dropin=0, dropout=0)
    assert [f.id for f in repair.diagnose([ok], ok).findings] == ["repair.ok"]
    bad = info.Adapter("Ethernet", up=True, kind="ethernet", speed_mbps=100, ipv4=["169.254.3.4"], gateway=[],
                       dns=[], errin=40, errout=0, dropin=0, dropout=0)
    ids = {f.id for f in repair.diagnose([bad], bad).findings}
    assert {"repair.apipa", "repair.no_gateway", "repair.errors", "repair.slow_link"} <= ids
    assert repair.diagnose([], None).findings[0].id == "repair.no_active"
    with pytest.raises(PermissionError):
        repair.renew(ok)


def test_adapter_classification_and_active():
    assert info.classify("WLAN", "Intel(R) Wi-Fi 6 AX201", "Native 802.11") == "wifi"
    assert info.classify("Ethernet", "Realtek PCIe GbE", "802.3") == "ethernet"
    assert info.classify("NordLynx", "NordLynx Tunnel") == "vpn"
    assert info.classify("vEthernet (WSL)", "Hyper-V Virtual Ethernet Adapter") == "virtual"
    a = info.Adapter("WLAN", up=True, kind="wifi", ipv4=["10.0.0.5"], gateway=["10.0.0.1"])
    v = info.Adapter("vEthernet", up=True, kind="virtual", ipv4=["172.20.0.1"])
    assert info.active([v, a]) is a
    assert info.is_private("192.168.1.1") and not info.is_private("1.1.1.1")


def test_local_adapters_are_listed():
    adapters, errors = info.adapters(windows=False)
    assert adapters, "psutil always reports at least the loopback adapter"
