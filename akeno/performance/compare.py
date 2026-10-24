"""Benchmark comparison of the four ways to play a game: baseline, Boost, DLSS 5, and DLSS 5 + Boost.

Each mode uses its latest measured session (PresentMon). A difference to the baseline is shown only as what was
measured, with the reasons a comparison may not be fair (another resolution, GPU or driver, an unvalidated frame
stream). Frame generation is kept apart: rendered FPS, generated-frame share and displayed FPS are separate rows.
Nothing is estimated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from akeno.i18n import msg

MODES = [("baseline", msg("Baseline")), ("boost", msg("Boost")), ("dlss5", msg("DLSS 5")),
         ("dlss5_boost", msg("DLSS 5 + Boost"))]
MODE_IDS = [m for m, _ in MODES]
METRICS = [("avg_fps", msg("Average FPS"), True), ("low_1pct_fps", msg("1 % low"), True),
           ("low_01pct_fps", msg("0.1 % low"), True), ("median_frametime_ms", msg("Median frame time (ms)"), False),
           ("p95_frametime_ms", msg("P95 frame time (ms)"), False), ("p99_frametime_ms", msg("P99 frame time (ms)"), False),
           ("displayed_fps", msg("Displayed FPS (with generated frames)"), True),
           ("generated_frame_share", msg("Share of generated frames"), None)]
LOAD = [("gpu_usage_percent", msg("GPU load (%)")), ("vram_used_mib", msg("VRAM (MiB)")),
        ("cpu_percent", msg("CPU load (%)")), ("ram_used_gb", msg("RAM (GB)")),
        ("temperature_c", msg("GPU temperature (°C)")), ("power_w", msg("GPU power (W)"))]


@dataclass
class ModeResult:
    mode: str
    session: dict | None
    values: dict = field(default_factory=dict)
    load: dict = field(default_factory=dict)
    delta_pct: dict = field(default_factory=dict)          # vs baseline, measured values only
    warnings: list = field(default_factory=list)
    reliable: bool = False


def load_averages(session: dict) -> dict:
    samples = (session.get("neural_config") or {}).get("sensor_samples") or []
    out = {}
    for key, _ in LOAD:
        values = [s[key] for s in samples if isinstance(s.get(key), (int, float))]
        if values:
            out[key] = round(mean(values), 1)
    return out


def compare(sessions: list[dict]) -> list[ModeResult]:
    from akeno.benchmark.service import BenchmarkService
    latest: dict[str, dict] = {}
    for s in sorted(sessions, key=lambda x: (x.get("created_at") or x.get("started_at") or "", x.get("id") or 0)):
        if s.get("label") in MODE_IDS:
            latest[s["label"]] = s
    base = latest.get("baseline")
    out = []
    for mode in MODE_IDS:
        session = latest.get(mode)
        result = ModeResult(mode, session)
        if session is not None:
            stats = session.get("stats") or {}
            result.values = {k: stats.get(k) for k, _, _ in METRICS}
            result.load = load_averages(session)
            measurement = (session.get("neural_config") or {}).get("measurement") or {}
            result.reliable = bool(measurement.get("reliable"))
            if not result.reliable:
                result.warnings.append(msg("The frame stream of this capture is not validated."))
            if base is not None and mode != "baseline":
                for reason in BenchmarkService.comparable(base, session):
                    result.warnings.append(reason)
                for key, _, higher_better in METRICS:
                    a, b = (base.get("stats") or {}).get(key), stats.get(key)
                    if higher_better is not None and isinstance(a, (int, float)) and isinstance(b, (int, float)) and a:
                        result.delta_pct[key] = round((b - a) / a * 100, 1)
        out.append(result)
    return out
