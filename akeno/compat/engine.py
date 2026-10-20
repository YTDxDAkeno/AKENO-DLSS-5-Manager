"""Compatibility database and evaluation engine."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from akeno.backends.base import BackendAdapter, BackendContext
from akeno.core.models import (AntiCheatRisk, CompatibilityResult, CompatStatus, Finding, Game, GraphicsApi,
                               HardwareSupport, Severity)
from akeno.hardware.gpu_db import nr_hardware_support
from akeno.paths import compat_override_path, resource_dir

log = logging.getLogger(__name__)

STATUSES = {s.value for s in CompatStatus}


class CompatDbError(ValueError):
    pass


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


@dataclass
class CompatDatabase:
    data: dict[str, Any]
    origin: str

    @property
    def version(self) -> str:
        return str(self.data.get("version", "?"))

    @staticmethod
    def validate(data: Any) -> None:
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise CompatDbError("unsupported compatibility database schema")
        if not isinstance(data.get("version"), str):
            raise CompatDbError("missing version")
        for g in data.get("games", []):
            if g.get("status") not in STATUSES:
                raise CompatDbError(f"invalid status for {g.get('name')}: {g.get('status')}")
            ev = g.get("evidence") or {}
            if not ev.get("source") or not ev.get("date_verified"):
                raise CompatDbError(f"entry {g.get('name')} has no evidence source/date")
            if not g.get("backend") or not isinstance(g.get("match"), dict):
                raise CompatDbError(f"entry {g.get('name')} has no backend/match")
        for h in data.get("hardware", []):
            if h.get("support") not in {s.value for s in HardwareSupport}:
                raise CompatDbError(f"invalid hardware support level {h.get('support')}")

    @classmethod
    def load(cls) -> "CompatDatabase":
        bundled = json.loads(Path(resource_dir(), "compatibility.json").read_text(encoding="utf-8"))
        cls.validate(bundled)
        override = compat_override_path()
        if override.is_file():
            try:
                data = json.loads(override.read_text(encoding="utf-8"))
                cls.validate(data)
                if str(data.get("version")) >= str(bundled.get("version")):
                    return cls(data, "downloaded")
            except (ValueError, CompatDbError) as exc:
                log.warning("ignoring invalid downloaded compatibility database: %s", exc)
        return cls(bundled, "bundled")

    def entries_for(self, game: Game, backend_id: str | None = None) -> list[dict[str, Any]]:
        out = []
        n = _norm(game.name)
        for g in self.data.get("games", []):
            if backend_id and g.get("backend") != backend_id:
                continue
            m = g.get("match", {})
            hit = (game.launcher == "steam" and game.launcher_id in m.get("steam_appids", [])) or \
                  (game.launcher == "epic" and game.launcher_id in m.get("epic_ids", [])) or \
                  n in {_norm(x) for x in m.get("names", [])}
            if hit:
                out.append(g)
        return out

    def anti_cheat_entry(self, game: Game) -> dict[str, Any] | None:
        n = _norm(game.name)
        for a in self.data.get("anti_cheat", []):
            if game.launcher == "steam" and game.launcher_id in a.get("steam_appids", []):
                return a
            if game.launcher == "epic" and game.launcher_id in a.get("epic_ids", []):
                return a
            if n in {_norm(x) for x in a.get("names", [])} or n == _norm(a.get("name", "")):
                return a
        return None

    def hardware_evidence(self, generation: str | None) -> dict[str, Any] | None:
        return next((h for h in self.data.get("hardware", []) if h.get("generation") == generation), None)


def evaluate(db: CompatDatabase, game: Game, adapter: BackendAdapter, ctx: BackendContext) -> CompatibilityResult:
    findings: list[Finding] = []
    evidence: list[dict[str, Any]] = []

    # 1. Anti-cheat and online risk: hard block, never bypassed.
    ac = db.anti_cheat_entry(game)
    if game.anti_cheat == AntiCheatRisk.DETECTED or ac:
        details = list(game.anti_cheat_details)
        if ac:
            details.append(f"{ac.get('system')} (AKENO database)")
        findings.append(Finding(Severity.BLOCKER, "anti_cheat",
                                "Installation blocked for safety: anti-cheat protection detected (" +
                                "; ".join(details) + "). Modifying this game can lead to bans."))
        return CompatibilityResult(CompatStatus.BLOCKED, adapter.id, findings, evidence, "high")

    # 2. Hardware.
    hw = nr_hardware_support(ctx.gpu)
    hw_ev = db.hardware_evidence(ctx.gpu.generation if ctx.gpu else None)
    if hw_ev:
        evidence.extend({"kind": "hardware", **e} for e in hw_ev.get("evidence", []))
    adapter_findings = adapter.check(game, ctx)
    findings.extend(adapter_findings)
    codes = {f.code for f in adapter_findings if f.severity == Severity.BLOCKER}

    if hw.level == HardwareSupport.UNSUPPORTED or codes & {"gpu_unsupported", "api_unsupported", "arch"}:
        if hw.level == HardwareSupport.UNSUPPORTED and "gpu_unsupported" not in codes:
            findings.insert(0, Finding(Severity.BLOCKER, "hw", hw.reason))
        return CompatibilityResult(CompatStatus.INCOMPATIBLE, adapter.id, findings, evidence, "high")

    # 3. Game evidence.
    entries = db.entries_for(game, adapter.id)
    status = CompatStatus.INSUFFICIENT
    confidence = "low"
    for e in entries:
        ev = dict(e.get("evidence", {}))
        ev.update(kind="game", status=e["status"], known_issues=e.get("known_issues", []), api=e.get("api"))
        evidence.append(ev)
        for issue in e.get("known_issues", []):
            findings.append(Finding(Severity.WARNING, "known_issue", issue, ev.get("source")))
        if e.get("api") and e["api"] != "unknown" and game.api not in (GraphicsApi.UNKNOWN,) and e["api"] != game.api.value:
            findings.append(Finding(Severity.WARNING, "api_mismatch",
                                    f"The evidence was recorded with {GraphicsApi(e['api']).label}, but this game is "
                                    f"set to {game.api.label}."))
        st = CompatStatus(e["status"])
        if st in (CompatStatus.INCOMPATIBLE, CompatStatus.BLOCKED):
            return CompatibilityResult(st, adapter.id, findings, evidence, "high")
        gens = ev.get("generations") or []
        same_gen = bool(ctx.gpu and ctx.gpu.generation in gens)
        if st == CompatStatus.CONFIRMED and (same_gen or not gens):
            status, confidence = CompatStatus.CONFIRMED, "high" if same_gen else "medium"
        elif status != CompatStatus.CONFIRMED:
            status, confidence = CompatStatus.EXPERIMENTAL, "medium"
            if st == CompatStatus.CONFIRMED:
                findings.append(Finding(Severity.INFO, "other_gpu",
                                        f"Confirmed upstream on {ev.get('gpu_tested')}, not on your GPU generation."))
    if status == CompatStatus.INSUFFICIENT:
        findings.append(Finding(Severity.INFO, "no_evidence",
                                f"Compatibility unknown: no documented test of {adapter.short} in this game. "
                                "Installation is possible as an experiment; AKENO keeps a full backup."))
    if hw.level in (HardwareSupport.EXPERIMENTAL, HardwareSupport.UNKNOWN) and status == CompatStatus.CONFIRMED \
            and confidence != "high":
        status, confidence = CompatStatus.EXPERIMENTAL, "medium"
    if game.api == GraphicsApi.UNKNOWN:
        confidence = "low"
    return CompatibilityResult(status, adapter.id, findings, evidence, confidence)
