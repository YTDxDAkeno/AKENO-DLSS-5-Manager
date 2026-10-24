"""Live system load for the FPS Booster page: CPU, RAM, and (NVIDIA) GPU load, VRAM, power and temperature.

Frame rates are not guessed here: they come from benchmark captures (PresentMon).
"""

from __future__ import annotations

from dataclasses import dataclass

import psutil


@dataclass
class Snapshot:
    cpu_percent: float
    ram_used_gb: float
    ram_total_gb: float
    gpu_percent: float | None = None
    vram_used_gb: float | None = None
    vram_total_gb: float | None = None
    gpu_temp_c: float | None = None
    gpu_power_w: float | None = None
    game_ram_gb: float | None = None


def snapshot(gpu=None, game_pid: int | None = None) -> Snapshot:
    vm = psutil.virtual_memory()
    snap = Snapshot(psutil.cpu_percent(interval=None), round(vm.used / 2 ** 30, 2), round(vm.total / 2 ** 30, 2))
    if gpu is not None:
        from akeno.hardware import sensors
        values = sensors.sample(gpu)
        snap.gpu_percent = values.get("gpu_usage_percent")
        if values.get("vram_used_mib") is not None:
            snap.vram_used_gb = round(values["vram_used_mib"] / 1024, 2)
        snap.vram_total_gb = getattr(gpu, "vram_gb", None)
        snap.gpu_temp_c = values.get("temperature_c")
        snap.gpu_power_w = values.get("power_w")
    if game_pid:
        try:
            snap.game_ram_gb = round(psutil.Process(game_pid).memory_info().rss / 2 ** 30, 2)
        except psutil.Error:
            pass
    return snap
