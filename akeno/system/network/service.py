"""Network Lab service (no Qt)."""

from __future__ import annotations

from typing import Any

from akeno.system.network import bandwidth, bufferbloat, dns, gaming, info, repair, stability
from akeno.system.network.probe import Prober, default_prober
from akeno.system.tasks import TaskContext


class NetworkService:
    def __init__(self, store, prober: Prober | None = None, running_launcher=None):
        self.store = store
        self._prober = prober
        self.gaming = gaming.GamingMode(store)
        self._launcher = running_launcher or (lambda: None)

    @property
    def prober(self) -> Prober:
        if self._prober is None:
            self._prober = default_prober()
        return self._prober

    def overview(self, ctx: TaskContext | None = None) -> dict[str, Any]:
        adapters, errors = info.adapters(ctx)
        act = info.active(adapters)
        rates = info.throughput(1.0, ctx)
        vpn = [a for a in adapters if a.kind == "vpn" and a.up]
        return {"adapters": adapters, "active": act, "errors": errors, "rates": rates, "vpn": vpn,
                "diagnosis": repair.diagnose(adapters, act)}

    def stability(self, extra: str | None = None, count: int = 30, ctx: TaskContext | None = None):
        adapters, _ = info.adapters(ctx)
        act = info.active(adapters)
        gw = next((g for g in (act.gateway if act else []) if "." in g), None)
        rep = stability.run(stability.Plan(gw, extra=extra or None, count=count), self.prober, ctx)
        self.store.add_history("network.stability", rep.to_dict(), keep=60)
        return rep

    def bufferbloat(self, consent: bool, ctx: TaskContext | None = None,
                    limits: bufferbloat.Limits = bufferbloat.Limits()):
        rep = bufferbloat.run(self.prober, "1.1.1.1:443", consent, limits, ctx)
        self.store.add_history("network.bufferbloat", rep.to_dict(), keep=30)
        return rep

    def bandwidth(self, ctx: TaskContext | None = None) -> dict[str, Any]:
        return bandwidth.sample(1.5, ctx, bandwidth.service_names())

    def dns_benchmark(self, ctx: TaskContext | None = None) -> list[dict[str, Any]]:
        adapters, _ = info.adapters(ctx)
        act = info.active(adapters)
        res = dns.benchmark(dns.server_list(act.dns if act else []), ctx)
        self.store.add_history("network.dns", {"results": res}, keep=30)
        return res

    def gaming_candidates(self, ctx: TaskContext | None = None) -> list[dict[str, Any]]:
        return gaming.candidates(self.bandwidth(ctx), self._launcher())

    def quick_measure(self, ctx: TaskContext | None = None) -> dict[str, Any]:
        adapters, _ = info.adapters(ctx)
        act = info.active(adapters)
        gw = next((g for g in (act.gateway if act else []) if "." in g), None)
        return stability.quick(self.prober, gw, ctx)

    def public_ip(self) -> str:
        return info.public_ip()

    def last(self, kind: str):
        from akeno.system.model import Report
        d = self.store.latest("network." + kind)
        return Report.from_dict(d) if d and "findings" in d else d
