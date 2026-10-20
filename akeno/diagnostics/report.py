"""Diagnostic package export.

What is exported (also shown to the user before saving):
  * AKENO version, OS version, GPU/driver/display information (no serial numbers, no user name)
  * the game list with install paths, where the user's profile folder is replaced by ``%USERPROFILE%``
  * install transactions with their file lists and logs, benchmark summaries
  * AKENO's own log files
Not exported: settings that may contain personal paths beyond the above, downloaded files, backups,
game files, environment variables, credentials or tokens (AKENO stores none).
"""

from __future__ import annotations

import json
import os
import platform
import re
import zipfile
from pathlib import Path
from typing import Any

from akeno import __version__
from akeno.core.db import now_iso
from akeno.paths import logs_dir

EXPORT_DESCRIPTION = [
    "AKENO version, Windows version and build",
    "Detected GPUs, drivers and displays (no serial numbers)",
    "Your game list with installation folders (your user folder is replaced by %USERPROFILE%)",
    "Compatibility results and recommendation explanations",
    "AKENO installation transactions, file lists and logs",
    "Benchmark summaries (not the raw frame-time files)",
    "AKENO's own log files",
]

_TOKEN = re.compile(r"(?i)(token|password|secret|authorization|api[_-]?key)\s*[=:]\s*\S+")


def scrub(text: str) -> str:
    home = str(Path.home())
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if home and len(home) > 3:
        text = text.replace(home, "%USERPROFILE%").replace(home.replace("\\", "/"), "%USERPROFILE%")
        text = text.replace(home.replace("\\", "\\\\"), "%USERPROFILE%")
    if user and len(user) > 2:
        text = re.sub(rf"(?i)([\\/]){re.escape(user)}([\\/])", r"\1%USERNAME%\2", text)
    return _TOKEN.sub(r"\1=<removed>", text)


def build(path: str, sections: dict[str, Any]) -> str:
    payload = {"generated_at": now_iso(), "akeno_version": __version__, "python": platform.python_version(),
               "platform": platform.platform(), **sections}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", "AKENO diagnostic package\n\nContents:\n- " + "\n- ".join(EXPORT_DESCRIPTION) + "\n")
        z.writestr("diagnostics.json", scrub(json.dumps(payload, indent=2, default=str)))
        for f in sorted(logs_dir().glob("*.log*")):
            try:
                z.writestr(f"logs/{f.name}", scrub(f.read_text(encoding="utf-8", errors="replace")[-2_000_000:]))
            except OSError:
                continue
    return path
