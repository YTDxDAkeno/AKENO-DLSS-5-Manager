"""System Doctor service: what the page calls (on worker threads). No Qt."""

from __future__ import annotations

import time
from typing import Any

from akeno.system.doctor import crashes, hardware, health, repair, slowpc
from akeno.system.doctor.providers import LocalProbe, system_drive
from akeno.system.doctor.startup import StartupAnalyzer
from akeno.system.model import Report
from akeno.system.tasks import TaskContext, context

SAMPLE_EVERY_S = 60          # the resource history keeps at most one sample a minute


class DoctorService:
    def __init__(self, store, system_info=None, probe=None, startup: StartupAnalyzer | None = None,
                 game_pids=None, game_dirs=None):
        self.store = store
        self.probe = probe or LocalProbe()
        self.startup = startup or StartupAnalyzer(store=store)
        self._system = system_info or (lambda: None)       # callable: AKENO's last hardware scan
        self._game_pids = game_pids or (lambda: set())     # callable: processes of running library games
        self._game_dirs = game_dirs or (lambda: {})
        self._last_sample = 0.0

    # -- dashboard -----------------------------------------------------------------------------------------------
    def snapshot(self, ctx: TaskContext | None = None) -> dict[str, Any]:
        snap = health.collect(self.probe, self._system(), ctx)
        found = health.findings(snap, system_drive())
        latest = [Report.from_dict(r) for r in (self.store.latest("doctor.crashes"), self.store.latest("doctor.slowpc"),
                                                 self.store.latest("doctor.hardware")) if r]
        combined = found + [f for r in latest for f in r.findings if f.id not in {x.id for x in found}]
        sc = health.score(combined)
        if time.time() - self._last_sample >= SAMPLE_EVERY_S:
            self.store.add_history("doctor.sample", health.history_sample(snap), keep=1440)
            self._last_sample = time.time()
        return {"snapshot": snap, "findings": found, "score": sc, "based_on": [r.created_at for r in latest],
                "history": list(reversed(self.store.history("doctor.sample", 120)))}

    # -- analyses ------------------------------------------------------------------------------------------------
    def slow_pc(self, ctx: TaskContext | None = None) -> Report:
        ctx = context(ctx)
        try:
            entries = self.startup.entries(ctx)
        except Exception as exc:                        # noqa: BLE001 (the detective still runs)
            entries = None
            err = str(exc)
        else:
            err = None
        services = slowpc.svchost_services()
        rep = slowpc.run(self.probe, self._system(), entries, slowpc.previous_data(self.store), self._game_pids(),
                         ctx, services=services)
        if err:
            rep.errors.append(err)
        self.store.add_history("doctor.slowpc", rep.to_dict(), keep=50)
        return rep

    def startup_entries(self, ctx: TaskContext | None = None):
        return self.startup.entries(ctx)

    def crashes(self, days: int = 14, ctx: TaskContext | None = None) -> Report:
        rep = crashes.analyse(self.probe, days, ctx, game_dirs=self._game_dirs())
        self.store.add_history("doctor.crashes", rep.to_dict(), keep=50)
        return rep

    def hardware(self, ctx: TaskContext | None = None, elevated: dict | None = None) -> Report:
        rep = hardware.analyse(self.probe, ctx, elevated)
        mem = self.probe.memory()
        try:
            perf = self.probe.perf(ctx)
        except Exception:                               # noqa: BLE001
            perf = None
        try:
            procs = self.probe.processes(1.0, ctx)
        except Exception:                               # noqa: BLE001
            procs = []
        rep.data["memory"] = hardware.memory_breakdown(mem, perf, procs)
        snap = health.collect(self.probe, self._system(), ctx, sample_s=0.5)
        rep.data["temperatures"] = snap.temperatures
        rep.data["gpus"] = [{"name": g["name"], "readings": {k: v.to_dict() for k, v in g["readings"].items()}}
                            for g in snap.gpus]
        rep.data["cpu_temperature"] = snap.cpu.get("temperature").to_dict() if snap.cpu.get("temperature") else None
        self.store.add_history("doctor.hardware", rep.to_dict(), keep=50)
        return rep

    def read_drives_as_admin(self, ctx: TaskContext | None = None) -> Report | None:
        from akeno.paths import logs_dir
        data = hardware.read_elevated(str(logs_dir()), ctx)
        if data is None:
            return None
        return self.hardware(ctx, elevated=data)

    # -- repair --------------------------------------------------------------------------------------------------
    def repair_recommendations(self) -> dict[str, str]:
        return repair.recommendations(self.store.latest("doctor.crashes"), self.store.latest("doctor.slowpc"))

    def run_repair(self, step_id: str, ctx: TaskContext | None = None) -> dict[str, Any]:
        return repair.run_step(step_id, self.store, ctx)

    def broken_shortcuts(self, ctx: TaskContext | None = None) -> list[dict]:
        return repair.broken_shortcuts(ctx=ctx)

    def recycle_shortcuts(self, items: list[dict]) -> dict[str, list[str]]:
        return repair.recycle_shortcuts(items, self.store)

    def last(self, kind: str) -> Report | None:
        d = self.store.latest("doctor." + kind)
        return Report.from_dict(d) if d else None
