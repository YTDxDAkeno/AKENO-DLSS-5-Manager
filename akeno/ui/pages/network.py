"""Network Lab: connection dashboard, ping & stability, bufferbloat, bandwidth, network gaming mode, DNS benchmark and
connection repair. Every value is measured on this PC; no improvement is claimed before it was measured."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from akeno.i18n import t
from akeno.system import commands
from akeno.system.model import human_bytes
from akeno.system.network import bufferbloat, repair
from akeno.ui import theme
from akeno.ui.system_ui import (FindingList, Sparkline, TabbedPage, TaskBar, export_button, fill_table, make_table,
                                open_link, selected_data)
from akeno.ui.widgets import (Card, EmptyAware, EmptyState, FlowLayout, KeyValueGrid, ResponsiveRow, StatusPill,
                              StatTile, button, confirm, form_row, info, label)

TABS = (("overview", "Overview"), ("stability", "Ping & stability"), ("bufferbloat", "Bufferbloat"),
        ("bandwidth", "Bandwidth"), ("gaming", "Gaming mode"), ("dns", "DNS"), ("repair", "Repair"))
KIND_TEXT = {"ethernet": "Ethernet (cable)", "wifi": "Wi-Fi", "vpn": "VPN", "virtual": "Virtual adapter",
             "loopback": "Loopback", "unknown": "Unknown"}
DURATIONS = ((15, "15 seconds"), (30, "30 seconds"), (60, "1 minute"))


def _flow(*widgets) -> QWidget:
    row = FlowLayout(spacing=8)
    for w in widgets:
        row.addWidget(w)
    holder = QWidget()
    holder.setLayout(row)
    return holder


def _rate(bps: float | None) -> str:
    if bps is None:
        return "—"
    return f"{bps * 8 / 1e6:.2f} Mbit/s"


class NetworkPage(TabbedPage):
    def __init__(self, ctl, window):
        super().__init__(t("Network Lab"), t("Measures your connection the way games feel it: latency, jitter, loss "
                                             "and latency under load. AKENO changes no Windows network settings by "
                                             "itself and claims no improvement it has not measured."))
        self.ctl, self.s, self.win = ctl, ctl.s, window
        self.svc = self.s.system_tools.network
        self.active_adapter = None                      # the last adapter read on a worker thread
        self.tabs_w = {"overview": OverviewTab(self), "stability": StabilityTab(self),
                       "bufferbloat": BufferbloatTab(self), "bandwidth": BandwidthTab(self), "gaming": GamingTab(self),
                       "dns": DnsTab(self), "repair": RepairTab(self)}
        for key, title in TABS:
            self.add_tab(key, t(title), self.tabs_w[key])

    def link(self, module: str, target: str = "") -> None:
        if module == "network":
            self.show_tab(target or "overview")
        else:
            open_link(self.win, module, target)

    def on_show(self) -> None:
        self.tab_shown(self.current or "overview")

    def tab_shown(self, key: str) -> None:
        for k, w in self.tabs_w.items():
            if k != key and hasattr(w, "on_hide"):
                w.on_hide()
        w = self.tabs_w[key]
        if hasattr(w, "on_show"):
            w.on_show()

    def hideEvent(self, e):
        for w in self.tabs_w.values():
            if hasattr(w, "on_hide"):
                w.on_hide()
        super().hideEvent(e)


# -- overview ------------------------------------------------------------------------------------------------------
class OverviewTab(QWidget):
    def __init__(self, page: NetworkPage):
        super().__init__()
        self.page = page
        self.loaded_at = 0.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        top = QHBoxLayout()
        top.addWidget(button(t("Refresh"), "refresh", on_click=self.refresh))
        top.addWidget(export_button(self, page.svc.store, "network"))
        self.task = TaskBar(page.ctl, "network:overview")
        top.addWidget(self.task, 1)
        top.addStretch(0)
        lay.addLayout(top)
        row = ResponsiveRow(spacing=14)
        c = Card(t("Active connection"), "wifi")
        self.state = StatusPill("", "pending")
        c.body.addWidget(self.state, 0, Qt.AlignmentFlag.AlignLeft)
        self.grid = KeyValueGrid(150)
        c.body.addWidget(self.grid)
        row.add(c, 3)
        r = Card(t("Right now"), "activity")
        tiles = QHBoxLayout()
        self.t_down, self.t_up = StatTile(t("Download")), StatTile(t("Upload"))
        tiles.addWidget(self.t_down)
        tiles.addWidget(self.t_up)
        r.body.addLayout(tiles)
        self.errors = label("", "muted", wrap=True)
        r.body.addWidget(self.errors)
        self.pub = label(t("Public IP address: not looked up."), "dim", wrap=True)
        r.body.addWidget(self.pub)
        r.body.addWidget(button(t("Show public IP address…"), "globe", "ghost", on_click=self._public_ip), 0,
                         Qt.AlignmentFlag.AlignLeft)
        row.add(r, 2)
        lay.addWidget(row)
        vpn = Card(t("VPN"), "lock")
        self.vpn = label("", "dim", wrap=True)
        vpn.body.addWidget(self.vpn)
        lay.addWidget(vpn)
        others = Card(t("All adapters"), "layers")
        self.table = make_table([t("Adapter"), t("Type"), t("State"), t("Address"), t("Speed")], 160)
        others.body.addWidget(self.table)
        lay.addWidget(others)
        d = Card(t("Diagnosis"), "eye")
        self.findings = FindingList(page.link)
        d.body.addWidget(self.findings)
        lay.addWidget(d)
        lay.addStretch(1)

    def on_show(self) -> None:
        if time.time() - self.loaded_at > 20 and not self.task.running:
            self.refresh()

    def refresh(self) -> None:
        self.task.start(self.page.svc.overview, self._show, text=t("Reading network adapters…"), timeout=90)

    def _show(self, out) -> None:
        self.loaded_at = time.time()
        a = out["active"]
        self.page.active_adapter = a
        if a is None:
            self.state.set(t("No active connection"), "fail")
            self.grid.set_rows([])
        else:
            self.state.set(t("Connected via {kind}", kind=t(KIND_TEXT.get(a.kind, a.kind))), "ok")
            rows = [(t("Adapter"), a.description or a.name), (t("Type"), t(KIND_TEXT.get(a.kind, a.kind))),
                    (t("IPv4 address"), ", ".join(a.ipv4) or "—"),
                    (t("IPv6 address"), ", ".join(x for x in a.ipv6 if not x.startswith("fe80")) or "—"),
                    (t("Gateway"), ", ".join(a.gateway) or "—"), (t("DNS servers"), ", ".join(a.dns) or "—"),
                    (t("Link speed"), f"{a.speed_mbps} Mbit/s" if a.speed_mbps else "—")]
            if a.kind == "wifi":
                w = a.wifi or {}
                rows.append((t("Wi-Fi signal"), f"{w['signal_percent']} %" if w.get("signal_percent") is not None
                             else t(w.get("note") or "Windows did not report the signal.")))
                if w.get("rx_mbps"):
                    rows.append((t("Wi-Fi rate"), f"{w['rx_mbps']} / {w.get('tx_mbps')} Mbit/s"))
            self.grid.set_rows(rows)
            rate = out["rates"].get(a.name) or {}
            self.t_down.set(_rate(rate.get("down")))
            self.t_up.set(_rate(rate.get("up")))
            self.errors.setText(t("Errors since start: {e}, dropped packets: {d}",
                                  e=(a.errin or 0) + (a.errout or 0), d=(a.dropin or 0) + (a.dropout or 0)))
        vpn = out["vpn"]
        self.vpn.setText(t("Active VPN adapters: {names}. Latency measurements go through the VPN.",
                           names=", ".join(v.description or v.name for v in vpn)) if vpn else
                         t("No VPN adapter is active. AKENO does not include a VPN; a separate WireGuard-based "
                           "service may follow later."))
        fill_table(self.table, [[x.description or x.name, t(KIND_TEXT.get(x.kind, x.kind)),
                                 (t("Up"), "ok") if x.up else (t("Down"), "pending"),
                                 ", ".join(x.ipv4) or "—", f"{x.speed_mbps} Mbit/s" if x.speed_mbps else "—"]
                                for x in out["adapters"]])
        self.findings.set_findings(out["diagnosis"].findings, out["errors"])

    def _public_ip(self) -> None:
        if not confirm(self, t("Public IP address"), t("Look up your public IP address?"),
                       t("AKENO sends one HTTPS request to api.ipify.org, which answers with the address it sees. No "
                         "location is looked up and nothing is stored."), t("Look up")):
            return
        self.task.start(lambda ctx: self.page.svc.public_ip(),
                        lambda ip: self.pub.setText(t("Public IP address: {ip}", ip=ip)),
                        lambda e: self.pub.setText(t("The address could not be looked up: {e}", e=str(e))),
                        text=t("Looking up…"), timeout=20)


# -- stability ---------------------------------------------------------------------------------------------------
class StabilityTab(QWidget):
    def __init__(self, page: NetworkPage):
        super().__init__()
        self.page = page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        c = Card(t("Ping and stability test"), "activity")
        c.body.addWidget(label(t("Measures your router and three internet targets at the same time, by ICMP ping and by "
                                 "TCP connection. A target that does not answer ping is not an outage: many servers "
                                 "drop ICMP. Optionally add a game server address."), "dim", wrap=True))
        self.host = QLineEdit()
        self.host.setPlaceholderText(t("Optional: game server (host or host:port)"))
        self.host.setAccessibleName(t("Game server"))
        c.body.addWidget(form_row(t("Additional target"), self.host))
        self.duration = QComboBox()
        self.duration.setAccessibleName(t("Duration"))
        for s, text in DURATIONS:
            self.duration.addItem(t(text), s)
        self.duration.setCurrentIndex(1)
        c.body.addWidget(form_row(t("Duration"), self.duration))
        self.btn = button(t("Start test"), "play", "primary", on_click=self.run)
        c.body.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.task = TaskBar(page.ctl, "network:stability")
        self.task.finished.connect(lambda _ok: self.btn.setEnabled(True))
        c.body.addWidget(self.task)
        lay.addWidget(c)
        r = Card(t("Results"), "chart")
        self.chart = Sparkline(theme.ACCENT, None, 80)
        r.body.addWidget(self.chart)
        self.chart_label = label("", "muted")
        r.body.addWidget(self.chart_label)
        self.table = make_table([t("Target"), t("Method"), t("Loss"), t("Median"), t("Min / max"), t("Jitter"),
                                 t("Spikes")], 200)
        r.body.addWidget(EmptyAware(self.table, EmptyState("activity", t("No test yet"),
                                                           t("Start the test to measure latency, jitter and loss.")),
                                    200))
        self.findings = FindingList(page.link)
        r.body.addWidget(self.findings)
        lay.addWidget(r)
        lay.addStretch(1)
        last = page.svc.last("stability")
        if last is not None and hasattr(last, "findings"):
            self._show(last)

    def run(self) -> None:
        seconds = int(self.duration.currentData())
        extra = self.host.text().strip() or None
        self.btn.setEnabled(False)
        if not self.task.start(lambda ctx: self.page.svc.stability(extra, max(4, int(seconds / 0.5)), ctx),
                               self._show, text=t("Measuring…"), timeout=seconds + 60):
            self.btn.setEnabled(True)

    def _show(self, rep) -> None:
        rows = []
        for s in rep.data.get("series") or []:
            st = s["stats"]
            label_text = t("Router") if s["label"] == "gateway" else (t("Your target") if s["label"] == "custom"
                                                                      else s["label"])
            rows.append([f"{label_text} ({s['target']})", s["method"].upper(),
                         "—" if st["loss_percent"] is None else (f"{st['loss_percent']} %",
                                                                "ok" if not st["loss_percent"] else "warn"),
                         "—" if st["median"] is None else f"{st['median']} ms",
                         "—" if st["min"] is None else f"{st['min']} / {st['max']} ms",
                         "—" if st["jitter"] is None else f"{st['jitter']} ms", str(st["spikes"])])
        fill_table(self.table, rows)
        best = rep.data.get("best")
        if best:
            series = next((s for s in rep.data["series"] if s["target"] == best["target"]
                           and s["method"] == best["method"]), None)
            if series:
                self.chart.set_values(series["rtts"])
                self.chart_label.setText(t("Latency of {t} over the test (gaps: no answer)", t=best["target"]))
        self.findings.set_findings(rep.findings, rep.errors)


# -- bufferbloat ---------------------------------------------------------------------------------------------------
class BufferbloatTab(QWidget):
    def __init__(self, page: NetworkPage):
        super().__init__()
        self.page = page
        lim = bufferbloat.Limits()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        c = Card(t("Latency under load (bufferbloat)"), "gauge")
        c.body.addWidget(label(t("Games lag when a download or upload fills your connection and the router queues "
                                 "packets. This test measures latency idle, then while downloading and while "
                                 "uploading."), "dim", wrap=True))
        c.body.addWidget(label(t("It contacts speed.cloudflare.com and transfers at most {down} down and {up} up, for "
                                 "at most {s} seconds per direction. Metered connections are charged for it.",
                                 down=human_bytes(lim.download_bytes), up=human_bytes(lim.upload_bytes),
                                 s=int(lim.seconds)), wrap=True))
        self.consent = QCheckBox(t("I agree to this test's data transfer"))
        self.consent.toggled.connect(lambda v: self.btn.setEnabled(bool(v) and not self.task.running))
        c.body.addWidget(self.consent)
        self.btn = button(t("Start bufferbloat test"), "play", "primary", on_click=self.run)
        self.btn.setEnabled(False)
        c.body.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.task = TaskBar(page.ctl, "network:bufferbloat")
        self.task.finished.connect(lambda _ok: self.btn.setEnabled(self.consent.isChecked()))
        c.body.addWidget(self.task)
        lay.addWidget(c)
        r = Card(t("Results"), "chart")
        tiles = QHBoxLayout()
        self.tiles = {k: StatTile(t(n)) for k, n in (("idle", "Idle"), ("download", "While downloading"),
                                                    ("upload", "While uploading"), ("grade", "Grade"))}
        for w in self.tiles.values():
            tiles.addWidget(w)
        r.body.addLayout(tiles)
        self.findings = FindingList(page.link, t("No test yet."))
        r.body.addWidget(self.findings)
        lay.addWidget(r)
        lay.addStretch(1)
        last = page.svc.last("bufferbloat")
        if last is not None and hasattr(last, "findings"):
            self._show(last)

    def run(self) -> None:
        if not self.consent.isChecked():
            return
        self.btn.setEnabled(False)
        self.task.start(lambda ctx: self.page.svc.bufferbloat(True, ctx), self._show, text=t("Measuring…"),
                        timeout=90)

    def _show(self, rep) -> None:
        idle = rep.data.get("idle") or {}
        self.tiles["idle"].set(f"{idle['median']} ms" if idle.get("median") is not None else "—")
        for k in ("download", "upload"):
            d = rep.data.get(k) or {}
            st = d.get("stats") or {}
            self.tiles[k].set(f"{st['median']} ms (+{d['added_ms']})" if st.get("median") is not None else "—")
        self.tiles["grade"].set(rep.data.get("grade") or "—")
        self.findings.set_findings(rep.findings, rep.errors)


# -- bandwidth -----------------------------------------------------------------------------------------------------
class BandwidthTab(QWidget):
    def __init__(self, page: NetworkPage):
        super().__init__()
        self.page = page
        self.history_down: list[float] = []
        self.history_up: list[float] = []
        self.live = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        c = Card(t("Bandwidth monitor"), "activity")
        c.body.addWidget(label(t("Windows counts network bytes per adapter, not per program. Per program AKENO shows "
                                 "its open internet connections and its total I/O rate, which Windows counts for "
                                 "files and network together: a hint, not a network measurement."), "dim", wrap=True))
        self.b_live = button(t("Start monitoring"), "play", "primary", on_click=self.toggle)
        c.body.addWidget(self.b_live, 0, Qt.AlignmentFlag.AlignLeft)
        tiles = QHBoxLayout()
        self.t_down, self.t_up = StatTile(t("Download")), StatTile(t("Upload"))
        tiles.addWidget(self.t_down)
        tiles.addWidget(self.t_up)
        c.body.addLayout(tiles)
        self.spark_down = Sparkline(theme.INFO, None)
        self.spark_up = Sparkline(theme.ACCENT, None)
        c.body.addWidget(self.spark_down)
        c.body.addWidget(self.spark_up)
        lay.addWidget(c)
        p = Card(t("Programs using the network"), "layers")
        self.table = make_table([t("Program"), t("Connections"), t("Remote addresses"), t("Total I/O (files and network)"),
                                 t("Background downloader")], 240)
        p.body.addWidget(EmptyAware(self.table, EmptyState("activity", t("Not monitoring"),
                                                           t("Start monitoring to see programs with internet "
                                                             "connections.")), 240))
        lay.addWidget(p)
        lay.addStretch(1)
        self.timer = QTimer(self)
        self.timer.setInterval(2500)
        self.timer.timeout.connect(self._tick)

    def toggle(self) -> None:
        self.live = not self.live
        self.b_live.setText(t("Stop monitoring") if self.live else t("Start monitoring"))
        if self.live:
            self._tick()
            self.timer.start()
        else:
            self.timer.stop()

    def on_hide(self) -> None:
        if self.live:
            self.toggle()                       # no load while nobody looks

    def _tick(self) -> None:
        if self.page.ctl.is_busy("network:bandwidth"):
            return
        self.page.ctl.run("network:bandwidth", self.page.svc.bandwidth, self._show, lambda e: None, silent=True)

    def _show(self, s) -> None:
        self.history_down = (self.history_down + [s["down_bps"] * 8 / 1e6])[-120:]
        self.history_up = (self.history_up + [s["up_bps"] * 8 / 1e6])[-120:]
        self.t_down.set(_rate(s["down_bps"]))
        self.t_up.set(_rate(s["up_bps"]))
        self.spark_down.set_values(self.history_down)
        self.spark_up.set_values(self.history_up)
        fill_table(self.table, [[p["title"] if p["title"] != p["name"] else p["name"], str(p["tcp"] + p["udp"]),
                                 str(len(p["remotes"])), (human_bytes(p["io_bps"]) + "/s") if p.get("io_bps") else "—",
                                 (t("Yes"), "info") if p["known"] else ""]
                                for p in s["processes"]],
                   tips=[[p["name"], None, "\n".join(p["remotes"]), None, ", ".join(p.get("services") or [])]
                         for p in s["processes"]])


# -- gaming mode -----------------------------------------------------------------------------------------------------
class GamingTab(QWidget):
    def __init__(self, page: NetworkPage):
        super().__init__()
        self.page = page
        self.cands = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        c = Card(t("Network gaming mode"), "gamepad")
        c.body.addWidget(label(t("Finds programs that use the connection in the background, pauses the ones you choose "
                                 "while you play (the same mechanism as FPS Booster's background programs), measures "
                                 "before and after, and resumes them with Restore or when AKENO closes. Windows, "
                                 "security software, anti-cheat and the launcher of the running game are never "
                                 "paused. No registry or TCP \"tweaks\"."), "dim", wrap=True))
        self.state = StatusPill("", "pending")
        c.body.addWidget(self.state, 0, Qt.AlignmentFlag.AlignLeft)
        self.b_find = button(t("Find background traffic"), "search", on_click=self.find)
        self.b_pause = button(t("Measure, pause selected, measure again…"), "pause", "primary", on_click=self.pause)
        self.b_restore = button(t("Restore"), "restore", on_click=self.restore)
        c.body.addWidget(_flow(self.b_find, self.b_pause, self.b_restore))
        self.task = TaskBar(page.ctl, "network:gaming")
        c.body.addWidget(self.task)
        self.table = make_table([t("Program"), t("Connections"), t("Total I/O"), t("Background downloader")], 200)
        self.table.setSelectionMode(self.table.SelectionMode.MultiSelection)
        c.body.addWidget(EmptyAware(self.table, EmptyState("gamepad", t("Nothing found yet"),
                                                           t("Find background traffic lists the programs with internet "
                                                             "connections; select the ones to pause.")), 200))
        lay.addWidget(c)
        res = Card(t("Before and after"), "chart")
        self.compare = KeyValueGrid(200)
        res.body.addWidget(self.compare)
        res.body.addWidget(label(t("Both measurements use the same targets. A difference is measured, not promised; "
                                   "if there is none, pausing did not help."), "muted", wrap=True))
        lay.addWidget(res)
        lay.addStretch(1)
        self._state()

    def on_show(self) -> None:
        self._state()

    def _state(self) -> None:
        paused = self.page.svc.gaming.paused()
        if paused:
            self.state.set(t("Active: {n} programs paused ({names})", n=len(paused),
                             names=", ".join(p.get("name") or "?" for p in paused[:4])), "warn")
        else:
            self.state.set(t("Off: nothing is paused"), "pending")
        self.b_restore.setEnabled(bool(paused))
        game = None
        try:
            game = self.page.s.system_tools.running_game()
        except Exception:                               # noqa: BLE001
            game = None
        if game is not None:
            self.state.setToolTip(t("Running game: {name} ({launcher})", name=game.name, launcher=game.launcher))

    def find(self) -> None:
        self.task.start(self.page.svc.gaming_candidates, self._found, text=t("Looking for background traffic…"),
                        timeout=60)

    def _found(self, cands) -> None:
        self.cands = cands
        fill_table(self.table, [[c["title"] if c["title"] != c["name"] else c["name"], str(c["tcp"] + c["udp"]),
                                 (human_bytes(c["io_bps"]) + "/s") if c.get("io_bps") else "—",
                                 (t("Yes"), "info") if c.get("known") else ""] for c in cands],
                   list(range(len(cands))))
        self.task.show_message(t("{n} programs with internet connections.", n=len(cands)) if cands else
                               t("No program has internet connections right now."))

    def _selected(self) -> list[dict]:
        rows = {i.row() for i in self.table.selectionModel().selectedRows()}
        return [self.cands[r] for r in sorted(rows) if r < len(self.cands)]

    def pause(self) -> None:
        sel = self._selected()
        if not sel:
            info(self, t("Network gaming mode"), t("Select the programs to pause first."))
            return
        if not confirm(self, t("Network gaming mode"), t("Pause {n} programs while you play?", n=len(sel)),
                       ", ".join(p["name"] for p in sel) + "\n\n" +
                       t("They stop completely until you press Restore or close AKENO; their downloads continue "
                         "afterwards. AKENO measures latency before and after."), t("Pause")):
            return

        def work(ctx):
            before = self.page.svc.quick_measure(ctx)
            out = self.page.svc.gaming.pause(sel, before)
            ctx.sleep(2)
            after = self.page.svc.quick_measure(ctx)
            return before, after, out
        self.task.start(work, self._paused, text=t("Measuring, pausing, measuring again…"), timeout=120)

    def _paused(self, res) -> None:
        before, after, out = res
        self._state()
        rows = []
        for key, name in (("median", "Median latency"), ("jitter", "Jitter"), ("loss_percent", "Loss")):
            b = (before.get("stats") or {}).get(key)
            a = (after.get("stats") or {}).get(key)
            unit = " %" if key == "loss_percent" else " ms"
            rows.append((t(name), f"{b if b is not None else '—'}{unit} → {a if a is not None else '—'}{unit}"))
        for r in out.get("refused") or []:
            rows.append((t("Not paused"), f"{r['name']}: {t(r['why'])}"))
        self.compare.set_rows(rows)

    def restore(self) -> None:
        self.task.start(lambda ctx: self.page.svc.gaming.restore(),
                        lambda res: (self._state(), self.task.show_message(
                            t("{n} programs resumed.", n=len(res["resumed"])))), text=t("Resuming…"),
                        cancellable=False)


# -- DNS -------------------------------------------------------------------------------------------------------------
class DnsTab(QWidget):
    def __init__(self, page: NetworkPage):
        super().__init__()
        self.page = page
        self.results = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        c = Card(t("DNS benchmark"), "globe")
        c.body.addWidget(label(t("Compares your current DNS server with public ones: cached names (what you feel when a "
                                 "launcher opens), uncached names and how often each server answered. AKENO never "
                                 "changes your DNS setting by itself."), "dim", wrap=True))
        self.btn = button(t("Start benchmark"), "play", "primary", on_click=self.run)
        c.body.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.task = TaskBar(page.ctl, "network:dns")
        c.body.addWidget(self.task)
        self.table = make_table([t("Server"), t("Address"), t("Cached"), t("Uncached"), t("Answered"), t("Errors")],
                                220)
        c.body.addWidget(EmptyAware(self.table, EmptyState("globe", t("No benchmark yet"),
                                                           t("Start the benchmark to compare DNS servers.")), 220))
        self.b_use = button(t("Use selected server…"), "check", on_click=self.use)
        c.body.addWidget(self.b_use, 0, Qt.AlignmentFlag.AlignLeft)
        c.body.addWidget(label(t("Changing the DNS server needs administrator rights. AKENO records the current "
                                 "setting first and can restore it."), "muted", wrap=True))
        lay.addWidget(c)
        h = Card(t("DNS changes"), "restore")
        self.hist = make_table([t("When"), t("Servers"), t("State")], 120)
        h.body.addWidget(self.hist)
        h.body.addWidget(button(t("Restore selected change…"), "restore", on_click=self.restore), 0,
                         Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(h)
        lay.addStretch(1)
        self._history()

    def run(self) -> None:
        self.task.start(self.page.svc.dns_benchmark, self._show, text=t("Querying DNS servers…"), timeout=180)

    def _show(self, res) -> None:
        self.results = res
        fill_table(self.table, [[r["name"], r["server"], "—" if r["cached_ms"] is None else f"{r['cached_ms']} ms",
                                 "—" if r["uncached_ms"] is None else f"{r['uncached_ms']} ms",
                                 "—" if r["answered_percent"] is None else (f"{r['answered_percent']} %",
                                                                            "ok" if r["answered_percent"] >= 99 else
                                                                            "warn"),
                                 ", ".join(f"{k}: {v}" for k, v in r["errors"].items()) or "—"] for r in res],
                   list(range(len(res))))

    def use(self) -> None:
        i = selected_data(self.table)
        if not isinstance(i, int) or i >= len(self.results):
            info(self, t("DNS"), t("Select a server in the table first."))
            return
        r = self.results[i]
        if r.get("system"):
            info(self, t("DNS"), t("This is already your current server."))
            return
        act = self.page.active_adapter
        if act is None:
            # the adapter is read on a worker thread first (PowerShell), then this runs again
            self.task.start(self.page.svc.overview, lambda out: (setattr(self.page, "active_adapter", out["active"]),
                                                                 self.use() if out["active"] else None),
                            text=t("Reading network adapters…"), timeout=90)
            return
        from akeno.system.network import dns
        if act.interface_index is None:
            info(self, t("DNS"), t("No active adapter AKENO could change."), error=True)
            return
        if not confirm(self, t("DNS"), t("Use {server} for {adapter}?", server=r["server"],
                                         adapter=act.description or act.name),
                       t("Windows asks for administrator rights. AKENO records the current setting ({current}) first; "
                         "Restore puts it back.", current=", ".join(act.dns) or "DHCP"), t("Change")):
            return

        def work(ctx):
            before = dns.current_setting(act.interface_index)
            return dns.apply_servers(act.interface_index, [r["server"]], self.page.svc.store, before)
        self.task.start(work, lambda code: (self._history(), self.task.show_message(
            t("DNS server changed.") if code == 0 else t("Nothing was changed."), error=code != 0)),
            text=t("Waiting for Windows…"), cancellable=False)

    def _history(self) -> None:
        hist = [a for a in self.page.svc.store.actions("network") if a["kind"] == "dns.set"]
        fill_table(self.hist, [[a["created_at"][:16].replace("T", " "), a["detail"],
                                t({"done": "Done", "failed": "Failed", "undone": "Restored"}.get(a["state"], a["state"]))]
                               for a in hist], [a["id"] for a in hist])

    def restore(self) -> None:
        aid = selected_data(self.hist)
        if aid is None:
            return
        if not confirm(self, t("DNS"), t("Restore the DNS setting from before this change?"),
                       t("Windows asks for administrator rights."), t("Restore")):
            return
        from akeno.system.network import dns
        self.task.start(lambda ctx: dns.restore(aid, self.page.svc.store),
                        lambda code: (self._history(), self.task.show_message(
                            t("Restored.") if code == 0 else t("Nothing was changed."), error=code != 0)),
                        lambda e: info(self, t("DNS"), t(str(e)), error=True), text=t("Waiting for Windows…"),
                        cancellable=False)


# -- repair ------------------------------------------------------------------------------------------------------------
class RepairTab(QWidget):
    def __init__(self, page: NetworkPage):
        super().__init__()
        self.page = page
        self.active = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        c = Card(t("Connection repair"), "restore")
        c.body.addWidget(label(t("A diagnosis of the active adapter and safe fixes, each explained first. AKENO never "
                                 "resets an adapter that has a working connection."), "dim", wrap=True))
        self.b_diag = button(t("Diagnose"), "search", "primary", on_click=self.diagnose)
        self.b_flush = button(t("Clear DNS cache"), "refresh", on_click=self.flush)
        self.b_renew = button(t("Request a new address…"), "download", on_click=self.renew)
        self.b_renew.setEnabled(False)
        c.body.addWidget(_flow(self.b_diag, self.b_flush, self.b_renew))
        self.task = TaskBar(page.ctl, "network:repair")
        c.body.addWidget(self.task)
        self.findings = FindingList(page.link)
        c.body.addWidget(self.findings)
        lay.addWidget(c)
        w = Card(t("Windows' own tools"), "monitor")
        w.body.addWidget(label(t("Windows' network troubleshooter and Network reset run from Settings. A network reset "
                                 "reinstalls every adapter and removes VPN and virtual switches; it needs a restart."),
                               "dim", wrap=True))
        bs = [button(t("Network status"), "external", on_click=lambda: commands.open_uri(repair.SETTINGS_PAGES["status"])),
              button(t("Troubleshooters"), "external",
                     on_click=lambda: commands.open_uri(repair.SETTINGS_PAGES["troubleshoot"])),
              button(t("Advanced network settings"), "external",
                     on_click=lambda: commands.open_uri(repair.SETTINGS_PAGES["advanced"]))]
        import os
        for b in bs:
            b.setEnabled(os.name == "nt")
        w.body.addWidget(_flow(*bs))
        lay.addWidget(w)
        lay.addStretch(1)

    def diagnose(self) -> None:
        self.task.start(self.page.svc.overview, self._diag, text=t("Diagnosing…"), timeout=90)

    def _diag(self, out) -> None:
        self.active = out["active"]
        self.page.active_adapter = out["active"]
        self.findings.set_findings(out["diagnosis"].findings, out["errors"])
        self.b_renew.setEnabled(bool(self.active and self.active.apipa))

    def flush(self) -> None:
        if not confirm(self, t("Clear DNS cache"), t("Clear Windows' DNS cache?"),
                       t("Windows forgets the name answers it remembered and asks the DNS server again. The connection "
                         "stays as it is."), t("Clear")):
            return
        self.task.start(lambda ctx: repair.flush_dns(),
                        lambda r: self.task.show_message(t("DNS cache cleared.") if r["ok"] else r["text"],
                                                         error=not r["ok"]),
                        lambda e: self.task.show_message(t(str(e)), error=True), text=t("Clearing…"),
                        cancellable=False)

    def renew(self) -> None:
        a = self.active
        if a is None or not a.apipa:
            return
        if not confirm(self, t("Request a new address"), t("Ask the router for a new address for {name}?",
                                                           name=a.name),
                       t("The adapter has no working address now, so nothing is interrupted."), t("Request")):
            return
        self.task.start(lambda ctx: repair.renew(a),
                        lambda r: (self.task.show_message(t("Done.") if r["ok"] else r["text"], error=not r["ok"]),
                                   self.diagnose()),
                        lambda e: self.task.show_message(t(str(e)), error=True), text=t("Requesting…"),
                        cancellable=False)
