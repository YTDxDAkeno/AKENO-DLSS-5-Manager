"""Where the Security Center's data comes from: Windows itself, or a stand-in with fixed answers for tests and
screenshots (marked as test data there). The stand-in never runs in a normal AKENO session."""

from __future__ import annotations

import time
from typing import Any

from akeno.system import signatures
from akeno.system.commands import CommandUnavailable
from akeno.system.securitycenter import defender, processes, wsc
from akeno.system.securitycenter.defender import ScanResult
from akeno.system.tasks import TaskContext, context


class WindowsSecurity:
    """The real PC: Windows Security Center, Microsoft Defender's own tools, Authenticode, psutil."""

    name = "windows"

    def health(self) -> dict[str, str]:
        return wsc.provider_health()

    def products(self, area: str) -> tuple[list[dict[str, Any]], list[str]]:
        return wsc.all_products(area)

    def firewall_profiles(self) -> list[dict[str, Any]]:
        return wsc.firewall_profiles()

    def defender_status(self, ctx: TaskContext | None = None) -> dict[str, Any]:
        return defender.status(ctx)

    def scan(self, kind: str, target: str | None, report_only: bool, ctx: TaskContext | None = None) -> ScanResult:
        return defender.scan(kind, target, report_only, ctx)

    def update_signatures(self, ctx: TaskContext | None = None) -> str:
        return defender.update_signatures(ctx)

    def history(self, ctx: TaskContext | None = None) -> list[dict[str, Any]]:
        return defender.history(ctx)

    def signatures(self, paths: list[str], ctx: TaskContext | None = None) -> dict:
        return signatures.check(paths, ctx)

    def processes(self, ctx: TaskContext | None = None) -> list[processes.ProcessInfo]:
        return processes.collect(ctx)


class FakeSecurity:
    """Fixed answers. ``None`` for a field means "this interface is missing" (it raises like Windows would)."""

    name = "fake"

    def __init__(self, health: dict | None = None, products: dict | None = None, firewall: list | None = None,
                 defender_status: dict | None = None, scan_results: dict | None = None,
                 history: list | None = None, signatures: dict | None = None, procs: list | None = None,
                 update_error: Exception | None = None, scan_delay: float = 0.0):
        self._health = health
        self._products = products
        self._firewall = firewall
        self._defender = defender_status
        self.scan_results = scan_results or {}
        self._history = history
        self._signatures = signatures or {}
        self._procs = procs or []
        self.update_error = update_error
        self.scan_delay = scan_delay
        self.scans: list[tuple[str, str | None, bool]] = []
        self.updates = 0

    def health(self) -> dict[str, str]:
        if self._health is None:
            raise CommandUnavailable("Windows Security Center needs Windows.")
        return dict(self._health)

    def products(self, area: str) -> tuple[list[dict[str, Any]], list[str]]:
        if self._products is None:
            raise CommandUnavailable("Windows Security Center needs Windows.")
        return list(self._products.get(area, [])), []

    def firewall_profiles(self) -> list[dict[str, Any]]:
        if self._firewall is None:
            raise CommandUnavailable("Get-NetFirewallProfile is not available.")
        return list(self._firewall)

    def defender_status(self, ctx: TaskContext | None = None) -> dict[str, Any]:
        if self._defender is None:
            raise defender.QueryError("0x800106ba: The Microsoft Defender service is not running.")
        return dict(self._defender)

    def scan(self, kind: str, target: str | None, report_only: bool, ctx: TaskContext | None = None) -> ScanResult:
        ctx = context(ctx)
        self.scans.append((kind, target, report_only))
        end = time.monotonic() + self.scan_delay
        while time.monotonic() < end:
            if ctx.cancelled:
                return ScanResult(kind, target, "cancelled", report_only and kind == "custom",
                                  detail="The scan was stopped.")
            time.sleep(0.02)
        res = self.scan_results.get((kind, target)) or self.scan_results.get(kind)
        if callable(res):
            res = res(kind, target, report_only)
        if isinstance(res, Exception):
            raise res
        if res is None:
            res = ScanResult(kind, target, "clean", report_only and kind == "custom")
        res.report_only = report_only and kind == "custom"
        return res

    def update_signatures(self, ctx: TaskContext | None = None) -> str:
        self.updates += 1
        if self.update_error is not None:
            raise self.update_error
        return "ok"

    def history(self, ctx: TaskContext | None = None) -> list[dict[str, Any]]:
        if self._history is None:
            raise defender.QueryError("Get-MpThreatDetection is not available.")
        return list(self._history)

    def signatures(self, paths: list[str], ctx: TaskContext | None = None) -> dict:
        return {p: self._signatures.get(p, signatures.Signature("unsigned")) for p in paths}

    def processes(self, ctx: TaskContext | None = None) -> list:
        return [processes.ProcessInfo(**{k: v for k, v in p.items() if k in ("pid", "name", "exe", "connections",
                                                                              "rss", "cpu_percent")})
                for p in self._procs]


def defender_active_status(**over) -> dict[str, Any]:
    """A healthy, active Microsoft Defender (base for stand-ins)."""
    st = {"service": True, "antivirus": True, "antispyware": True, "realtime": True, "behavior": True, "ioav": True,
          "on_access": True, "network": True, "tamper": True, "mode": "normal", "product_version": "4.18.25090.3009",
          "engine_version": "1.1.25090.3001", "signature_version": "1.439.120.0", "signature_age_days": 0,
          "signatures_out_of_date": False, "signature_updated": "2026-10-26T05:12:00+01:00",
          "quick_scan_age_days": 1, "full_scan_age_days": None, "quick_scan_end": "2026-10-25T03:10:00+01:00",
          "full_scan_end": None, "computer_state": []}
    st.update(over)
    return st
