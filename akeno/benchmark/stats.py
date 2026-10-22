"""Frame-time statistics and PresentMon CSV parsing."""

from __future__ import annotations

import csv
import io
import math
import statistics
from dataclasses import dataclass, field

from akeno.core.models import FrameStats

FRAME_COLUMNS = ("MsBetweenAppStart", "MsBetweenPresents", "msBetweenPresents", "MsBetweenDisplayChange", "DisplayedTime")


class CsvError(ValueError):
    pass


@dataclass
class ParsedCsv:
    frametimes_ms: list[float]
    process_names: list[str]
    column: str
    runtimes: list[str]
    quality: dict = field(default_factory=dict)
    displayed_frametimes_ms: list[float] = field(default_factory=list)


def parse_presentmon_csv(text, process_name=None, process_id=None, swapchain=None):
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if not reader.fieldnames:
        raise CsvError('The CSV file is empty.')
    fields = reader.fieldnames
    col = next((c for c in FRAME_COLUMNS if c in fields), None)
    if col is None:
        raise CsvError('No supported PresentMon frame-time column found.')
    app_col = next((c for c in ('Application','ProcessName') if c in fields), None)
    pid_col = next((c for c in ('ProcessID','ProcessId') if c in fields), None)
    if process_id is not None and not pid_col:
        raise CsvError('PID-specific attribution requires a ProcessID column.')
    if process_name and not app_col:
        raise CsvError('Process-specific selection requires an Application column.')
    rt_col = 'PresentRuntime' if 'PresentRuntime' in fields else 'Runtime'
    groups = {}
    def positive(value):
        try: value = float(value)
        except (ValueError, TypeError): return None
        return value if math.isfinite(value) and 0 < value <= 5000 else None
    for index, row in enumerate(reader):
        if index >= 1000000:
            raise CsvError('CSV sample count exceeds the bounded one-million-row limit.')
        app = (row.get(app_col) or '') if app_col else ''
        pid = row.get(pid_col, '') if pid_col else ''
        chain = row.get('SwapChainAddress', '')
        runtime = row.get(rt_col, '')
        if app.casefold() in ('dwm.exe','presentmon.exe'):
            continue
        if process_name and app.casefold() != process_name.casefold(): continue
        if process_id is not None and str(pid) != str(process_id): continue
        if swapchain is not None and chain != swapchain: continue
        key = (app, pid, chain, runtime)
        group = groups.setdefault(key, {'times':[], 'display':[], 'generated':0, 'application':0, 'unknown':0, 'rows':0})
        group['rows'] += 1
        kind = (row.get('FrameType') or '').strip().casefold()
        generated = any(x in kind for x in ('generated','interpolat','dlss','afmf','repeated'))
        if generated: group['generated'] += 1
        elif kind == 'application': group['application'] += 1
        else: group['unknown'] += 1
        display = positive(row.get('DisplayedTime') or row.get('MsBetweenDisplayChange'))
        if display is not None: group['display'].append(display)
        if generated: continue
        if kind not in ('', 'application', 'na', 'n/a', 'notset', 'unspecified'): continue
        value = positive(row.get(col))
        if value is not None: group['times'].append(value)
    usable = {k:v for k,v in groups.items() if v['times']}
    if not usable:
        return ParsedCsv([], [], col, [], {'reliable':False, 'warnings':['No matching application frame stream.']})
    # Never merge swapchains/PIDs/runtimes. Prefer coverage rather than the most frequent tiny presents.
    selected = max(usable, key=lambda k: (sum(usable[k]['times']), len(usable[k]['times']), k))
    group = usable[selected]
    warnings = []
    ambiguous = len(usable) > 1 and swapchain is None
    if ambiguous: warnings.append('Multiple present streams: selected the stream with greatest frame-time coverage; select a swapchain to resolve ambiguity.')
    if not selected[1]: warnings.append('Process ID unavailable; process attribution is unconfirmed.')
    if not selected[2]: warnings.append('Swapchain identity unavailable; stream homogeneity cannot be confirmed.')
    if group['unknown']: warnings.append('Frame type is unknown for some samples; generated-frame separation is incomplete.')
    if col in ('MsBetweenDisplayChange','DisplayedTime'): warnings.append('Display timing only: application-rendered FPS is unavailable.')
    if group['generated'] and col != 'MsBetweenAppStart': warnings.append('Generated frames exist but application-start timing is unavailable; present intervals cannot establish rendered FPS.')
    values = sorted(group['times'])
    heterogeneous = statistics.median(values) < 2 and percentile(values,95) > 8 * statistics.median(values)
    if heterogeneous: warnings.append('Highly heterogeneous short/long intervals: menu, loading or secondary presents may dominate. No trustworthy gameplay median is reported.')
    reliable = not ambiguous and not heterogeneous and bool(selected[1] and selected[2]) and not group['unknown'] and (not group['generated'] or col == 'MsBetweenAppStart')
    rendered = col == 'MsBetweenAppStart' and group['application'] > 0 and not group['unknown'] and reliable
    if not warnings and not rendered: warnings.append('Present-call timing is not necessarily application-rendered FPS.')
    quality = {'reliable': reliable, 'warnings':warnings, 'selected_stream':dict(zip(('application','pid','swapchain','runtime'),selected)),
        'selection_policy':'explicit_swapchain' if swapchain is not None else 'greatest_frame_time_coverage',
        'streams':[dict(zip(('application','pid','swapchain','runtime'),k), samples=len(v['times'])) for k,v in usable.items()],
        'metric_kind':'application_rendered' if rendered else ('displayed' if col in ('MsBetweenDisplayChange','DisplayedTime') else 'present_intervals'),
        'rendered_fps_available':rendered, 'frame_type_known':not group['unknown'],
        'generated_frame_share':group['generated']/group['rows'] if not group['unknown'] and group['rows'] else None}
    return ParsedCsv(group['times'], [selected[0]] if selected[0] else [], col, [selected[3]] if selected[3] else [], quality, group['display'])


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        raise ValueError("empty")
    k = (len(sorted_vals) - 1) * p / 100.0
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def compute(frametimes_ms: list[float], drop_first_s: float = 0.0) -> FrameStats:
    """Statistics over rendered frames.

    Lows are 1000 divided by the mean of the slowest 1% / 0.1% frame times.
    0.1% low requires 1000 samples. Source/stream validation is the parser's responsibility.
    Spike threshold is max(2x median, median+8ms, 33.33ms); adjacent spike frames form one episode.
    """
    ft = list(frametimes_ms)
    if drop_first_s > 0:
        acc = 0.0
        i = 0
        while i < len(ft) and acc < drop_first_s * 1000:
            acc += ft[i]
            i += 1
        ft = ft[i:]
    if len(ft) < 30:
        raise ValueError("Not enough frames for meaningful statistics (need at least 30).")
    total_ms = sum(ft)
    s = sorted(ft)
    n = len(s)
    worst1 = s[-max(1, n // 100):]
    low1 = 1000.0 / (sum(worst1) / len(worst1))
    low01 = None
    if n >= 1000:
        worst01 = s[-max(1, n // 1000):]
        low01 = 1000.0 / (sum(worst01) / len(worst01))
    med = statistics.median(s)
    # Absolute floor avoids counting sub-ms present alternation as gameplay stutter.
    threshold = max(2 * med, med + 8.0, 1000.0 / 30)
    spikes = [v > threshold for v in ft]
    stutters = sum(spikes)
    episodes = sum(flag and (i == 0 or not spikes[i-1]) for i,flag in enumerate(spikes))
    return FrameStats(
        frames=n, duration_s=round(total_ms / 1000.0, 3), avg_fps=round(n * 1000.0 / total_ms, 2),
        avg_frametime_ms=round(total_ms / n, 3), median_frametime_ms=round(med, 3),
        p95_frametime_ms=round(percentile(s, 95), 3), p99_frametime_ms=round(percentile(s, 99), 3),
        low_1pct_fps=round(low1, 2), low_01pct_fps=round(low01, 2) if low01 else None,
        stdev_frametime_ms=round(statistics.pstdev(ft), 3), stutter_ratio=round(stutters / n, 4),
        median_fps=round(1000.0 / med, 2),
        stutter_events_per_minute=round(episodes * 60000.0 / total_ms, 2), frame_spikes=stutters,
    )


def consistency_label(stats: FrameStats) -> str:
    """Frame-time consistency from the measured 1%-low/average ratio and stutter share (documented in README)."""
    ratio = stats.low_1pct_fps / stats.avg_fps if stats.avg_fps else 0
    if ratio >= 0.75 and stats.stutter_ratio <= 0.01:
        return "good"
    if ratio >= 0.5 and stats.stutter_ratio <= 0.05:
        return "fair"
    return "poor"
