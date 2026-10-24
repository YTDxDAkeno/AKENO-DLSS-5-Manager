"""What limits the frame rate, from a PresentMon 2 capture (its GPU-busy and CPU-busy times per frame).

Per frame: if the GPU was busy for at least 90 % of the frame time, the frame was GPU-limited; at most 75 %, it was
waiting for something else: the CPU (game logic, draw calls) or a frame cap / V-Sync. This follows Intel's
"GPU Busy" method. It needs the MsGPUBusy column (PresentMon 2.x); without it AKENO says it cannot tell.
"""

from __future__ import annotations

import csv
import io
import statistics
from dataclasses import dataclass

from akeno.i18n import Msg, msg


@dataclass
class Bottleneck:
    frames: int
    gpu_bound_share: float
    cpu_bound_share: float
    median_frame_ms: float
    median_gpu_busy_ms: float
    median_cpu_busy_ms: float | None
    limited_by_sync: bool
    verdict: str                 # gpu | cpu | limiter | mixed | unknown
    explanation: Msg | str


def _col(fields, *names):
    return next((n for n in names if n in fields), None)


def analyse_csv(text: str, process_name: str | None = None) -> Bottleneck:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = reader.fieldnames or []
    frame_col = _col(fields, "MsBetweenAppStart", "FrameTime", "MsBetweenPresents")
    gpu_col = _col(fields, "MsGPUBusy", "GPUBusy")
    cpu_col = _col(fields, "MsCPUBusy", "CPUBusy")
    sync_col = _col(fields, "SyncInterval")
    if not frame_col or not gpu_col:
        return Bottleneck(0, 0, 0, 0, 0, None, False, "unknown",
                          msg("This capture has no GPU-busy times (PresentMon 2 records them); the limit cannot be told."))
    frames, gpu, cpu, syncs = [], [], [], 0
    for row in reader:
        if process_name and (row.get("Application") or "").casefold() != process_name.casefold():
            continue
        kind = (row.get("FrameType") or "").casefold()
        if kind and kind not in ("application", "na", "n/a", "notset"):
            continue                                     # generated frames are not rendered by the game
        try:
            ft, gb = float(row[frame_col]), float(row[gpu_col])
        except (TypeError, ValueError):
            continue
        if not (0 < ft <= 5000) or gb < 0:
            continue
        frames.append(ft)
        gpu.append(min(gb, ft))
        if cpu_col:
            try:
                cpu.append(float(row[cpu_col]))
            except (TypeError, ValueError):
                pass
        if sync_col and (row.get(sync_col) or "0").strip() not in ("0", ""):
            syncs += 1
    n = len(frames)
    if n < 30:
        return Bottleneck(n, 0, 0, 0, 0, None, False, "unknown", msg("Too few frames for an analysis (at least 30)."))
    ratios = [g / f for g, f in zip(gpu, frames)]
    gpu_share = sum(r >= 0.9 for r in ratios) / n
    cpu_share = sum(r <= 0.75 for r in ratios) / n
    synced = syncs / n > 0.5
    med_ft, med_gpu = statistics.median(frames), statistics.median(gpu)
    med_cpu = statistics.median(cpu) if cpu else None
    if gpu_share >= 0.6:
        verdict, text = "gpu", msg("GPU-limited in {p} % of frames: the graphics card sets the frame rate. Lower "
                                   "resolution or quality, or use DLSS, to gain FPS.", p=round(gpu_share * 100))
    elif cpu_share >= 0.6 and synced:
        verdict, text = "limiter", msg("The GPU waits in {p} % of frames while V-Sync is on: the refresh rate or a "
                                       "frame cap sets the frame rate, not the hardware.", p=round(cpu_share * 100))
    elif cpu_share >= 0.6:
        verdict, text = "cpu", msg("CPU-limited in {p} % of frames: the GPU waits for the game. Graphics settings "
                                   "change little; fewer background programs, high priority or lower CPU-heavy "
                                   "settings (crowds, view distance) help more.", p=round(cpu_share * 100))
    else:
        verdict, text = "mixed", msg("Mixed: GPU-limited in {g} % and waiting in {c} % of frames (changes with the "
                                     "scene).", g=round(gpu_share * 100), c=round(cpu_share * 100))
    return Bottleneck(n, round(gpu_share, 3), round(cpu_share, 3), round(med_ft, 3), round(med_gpu, 3),
                      round(med_cpu, 3) if med_cpu is not None else None, synced, verdict, text)
