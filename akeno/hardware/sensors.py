"""Optional read-only NVIDIA sensors; ambiguous adapters and unsupported values stay unavailable."""
import csv
import io
import math
import os
from pathlib import Path
import subprocess
from akeno.paths import is_windows


def sample(gpu):
    empty = {key: None for key in ('gpu_usage_percent', 'vram_used_mib', 'power_w', 'temperature_c')}
    if not gpu or not is_windows():
        return empty
    exe = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'nvidia-smi.exe'
    if not exe.is_file():
        return empty
    try:
        result = subprocess.run([str(exe), '--query-gpu=name,utilization.gpu,memory.used,power.draw,temperature.gpu',
            '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=3, creationflags=0x08000000)
        if result.returncode:
            return empty
        rows = [r for r in csv.reader(io.StringIO(result.stdout)) if len(r) == 5 and r[0].strip() == gpu.name]
        if len(rows) != 1:
            return empty
        for key, raw in zip(empty, rows[0][1:]):
            try:
                value = float(raw.strip())
                if math.isfinite(value) and value >= 0:
                    empty[key] = value
            except ValueError:
                pass
    except (OSError, subprocess.TimeoutExpired):
        pass
    return empty
