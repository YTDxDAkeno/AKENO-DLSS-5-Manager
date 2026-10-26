"""The diagnosis model every module shares: readings, evidence, findings, links between modules.

Rules the types encode:

* A :class:`Reading` without a value is *unavailable* and carries the reason. It never holds a default that looks
  like a measurement.
* :class:`Evidence` says what kind of statement it is. A measured fact, a possible cause and a correlation in time
  are different things, and the UI shows them differently.
* A :class:`Finding` is one problem (or one confirmation) with its observation, recommendation and evidence. Its
  ``links`` hand the user over to the module that can act on it (a full disk goes to Smart Cleaner).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from akeno.core.db import now_iso
from akeno.i18n import Msg


def enc(text: Any) -> Any:
    """A text for JSON that keeps a translatable message translatable (template and values, see ``akeno.i18n``)."""
    if isinstance(text, Msg):
        return {"t": text.template, "p": {k: enc(v) for k, v in text.params.items()}}
    return text if isinstance(text, (int, float, bool)) or text is None else str(text)


def dec(obj: Any) -> Any:
    if isinstance(obj, dict) and set(obj) == {"t", "p"}:
        try:
            return Msg(obj["t"], **{k: dec(v) for k, v in obj["p"].items()})
        except (KeyError, IndexError, ValueError):
            return obj["t"]
    return obj


class Level(str, Enum):
    """Severity of a finding; the values are the design system's state names (``theme.STATE_COLORS``)."""

    OK = "ok"
    INFO = "info"
    WARN = "warn"
    PROBLEM = "fail"
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        return {"fail": 4, "warn": 3, "info": 2, "unknown": 1, "ok": 0}[self.value]


class EvidenceKind(str, Enum):
    MEASURED = "measured"              # a value AKENO read or measured on this PC
    POSSIBLE_CAUSE = "possible_cause"  # a plausible explanation the measurements fit, not a proof
    CORRELATION = "correlation"        # happened around the same time; may be unrelated


EVIDENCE_LABELS = {EvidenceKind.MEASURED: "Measured", EvidenceKind.POSSIBLE_CAUSE: "Possible cause",
                   EvidenceKind.CORRELATION: "Same time, not proven related"}


@dataclass
class Evidence:
    kind: EvidenceKind
    text: str
    source: str = ""                   # where it came from ("psutil", "Event Log: System", "Get-MpComputerStatus")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "text": enc(self.text), "source": self.source}


@dataclass
class Link:
    """A hand-over to another module or page: ``module`` is a page key, ``target`` what to open there."""

    module: str
    label: str
    target: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"label": enc(self.label)}


@dataclass
class Finding:
    id: str                            # stable key, e.g. "cpu.idle_load"
    module: str                        # "doctor" | "cleaner" | "network" | "mods" | "security"
    level: Level
    title: str
    observation: str = ""
    recommendation: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def measured(self, text: str, source: str = "") -> "Finding":
        self.evidence.append(Evidence(EvidenceKind.MEASURED, text, source))
        return self

    def cause(self, text: str, source: str = "") -> "Finding":
        self.evidence.append(Evidence(EvidenceKind.POSSIBLE_CAUSE, text, source))
        return self

    def correlation(self, text: str, source: str = "") -> "Finding":
        self.evidence.append(Evidence(EvidenceKind.CORRELATION, text, source))
        return self

    def link(self, module: str, label: str, target: str = "") -> "Finding":
        self.links.append(Link(module, label, target))
        return self

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "module": self.module, "level": self.level.value, "title": enc(self.title),
                "observation": enc(self.observation), "recommendation": enc(self.recommendation),
                "evidence": [e.to_dict() for e in self.evidence], "links": [x.to_dict() for x in self.links],
                "data": self.data}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Finding":
        return cls(d["id"], d["module"], Level(d["level"]), dec(d["title"]), dec(d.get("observation", "")),
                   dec(d.get("recommendation", "")),
                   [Evidence(EvidenceKind(e["kind"]), dec(e["text"]), e.get("source", "")) for e in d.get("evidence", [])],
                   [Link(x["module"], dec(x["label"]), x.get("target", "")) for x in d.get("links", [])],
                   dict(d.get("data") or {}))


@dataclass
class Reading:
    """One value as reported, or why it is not available."""

    value: Any = None
    unit: str = ""
    source: str = ""
    note: str = ""                     # the reason when unavailable, or a remark on how it was read

    @property
    def available(self) -> bool:
        return self.value is not None

    @classmethod
    def missing(cls, note: str, source: str = "") -> "Reading":
        return cls(None, "", source, note)

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "unit": self.unit, "source": self.source, "note": enc(self.note)}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Reading":
        d = d or {}
        return cls(d.get("value"), d.get("unit", ""), d.get("source", ""), dec(d.get("note", "")))


@dataclass
class Report:
    """The result of one analysis run: its findings plus what could not be checked and why."""

    module: str
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)     # probes that failed (shown, never hidden)
    created_at: str = field(default_factory=now_iso)
    duration_s: float | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def add(self, finding: Finding) -> Finding:
        self.findings.append(finding)
        return finding

    @property
    def worst(self) -> Level:
        if not self.findings:
            return Level.UNKNOWN if self.errors else Level.OK
        return max((f.level for f in self.findings), key=lambda lv: lv.rank)

    def problems(self) -> list[Finding]:
        return [f for f in self.findings if f.level in (Level.PROBLEM, Level.WARN)]

    def sorted(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: -f.level.rank)

    def to_dict(self) -> dict[str, Any]:
        return {"module": self.module, "findings": [f.to_dict() for f in self.findings],
                "errors": [enc(e) for e in self.errors], "created_at": self.created_at,
                "duration_s": self.duration_s, "data": self.data}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Report":
        return cls(d["module"], [Finding.from_dict(f) for f in d.get("findings", [])],
                   [dec(e) for e in d.get("errors", [])],
                   d.get("created_at") or now_iso(), d.get("duration_s"), dict(d.get("data") or {}))


def human_bytes(n: float | int | None) -> str:
    """1536 -> "1.5 KB"; binary units, as Explorer shows them."""
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d} d {h} h"
    if h:
        return f"{h} h {m} min"
    if m:
        return f"{m} min {s} s"
    return f"{s} s"
