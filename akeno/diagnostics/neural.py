"""Read-only, append-aware neural evidence. No module loading, injection or process modification."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import time
import re

from akeno.security.downloads import sha256_file

LOGS = ("OptiScaler.log", "ReShade.log", "dlss5-feed.log", "host64/ReShade.log", "host64/dlss5-feed-host.log")
LIMIT = 8 * 1024 * 1024


@dataclass(frozen=True)
class LogCursor:
    size: int
    prefix_sha256: str


@dataclass(frozen=True)
class Observation:
    game_id: str
    target: str
    executable: str
    artifacts: tuple[tuple[str, str], ...]
    cursors: tuple[tuple[str, LogCursor], ...]
    started_at: float
    executable_sha256: str


@dataclass
class NeuralEvidence:
    state: str = "none"  # none, installed, loaded, initialized, running, verified, failed, stale
    lines: list[dict] = field(default_factory=list)
    detail: str = ""


def _prefix(path: Path, size: int) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        remaining = size
        while remaining:
            chunk = f.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def begin(game_id: str, target: str | Path, executable: str | Path, artifacts: list[str]) -> Observation:
    target = Path(target).resolve()
    pins = []
    for name in artifacts:
        p = target / name
        if p.is_symlink() or not p.resolve().is_relative_to(target) or not p.is_file():
            raise ValueError("Diagnostic artifacts must be regular files inside the selected target")
        pins.append((name, sha256_file(p)))
    cursors = []
    for name in LOGS:
        p = target / name
        if p.is_file() and not p.is_symlink():
            size = p.stat().st_size
            if size > LIMIT:
                raise ValueError("Rotate oversized logs before starting a bounded observation")
            cursors.append((name, LogCursor(size, _prefix(p, size))))
    exe = Path(executable)
    if exe.is_symlink() or not exe.is_file():
        raise ValueError("The selected executable must be an existing regular file")
    return Observation(game_id, str(target), str(exe.resolve()), tuple(pins), tuple(cursors), time.time(), sha256_file(exe))


def classify(line: str) -> str | None:
    low = line.casefold()
    if re.search(r"dlss-nr: pass \d+ failed to build", low):
        return "failed"
    if any(marker in low for marker in ("feature 18 create failed", "neural model not created", "0xbad00001",
                                         "dlss-nr evaluate returned 0x", "dlss-nr (proxy): evaluate returned 0x")):
        return "failed"
    if "inline feature 18 evaluation succeeded" in low:
        return "running"
    # Real OptiScaler error logs are recognized; vague "loaded" or SR/FG messages are not NR success.
    if re.search(r"dlss-nr: pass \d+ built at", low) or any(marker in low for marker in ("neural model (feature 18) loaded", "feature 18 created")):
        return "initialized"
    if any(marker in low for marker in ("dlss-nr forwarder loaded from", "dlss5-feeder initialized")):
        return "loaded"
    return None


def inspect(target: str | Path, observation: Observation | None = None, game_id: str | None = None,
            process_paths=()) -> NeuralEvidence:
    root = Path(target).resolve()
    result = NeuralEvidence()
    names = {p.name.lower() for p in root.iterdir() if p.is_file()} if root.is_dir() else set()
    if "nvngx_dlssnr.dll" in names and (any(n.endswith(".addon64") for n in names) or "optiscaler.ini" in names):
        result.state = "installed"
    if observation:
        if str(root) != observation.target or game_id != observation.game_id:
            return NeuralEvidence("stale", detail="The selected game or target changed.")
        executable = Path(observation.executable)
        if executable.is_symlink() or not executable.is_file() or sha256_file(executable) != observation.executable_sha256:
            return NeuralEvidence("stale", detail="The selected executable changed.")
        for name, pin in observation.artifacts:
            p = root / name
            if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(root) or sha256_file(p) != pin:
                return NeuralEvidence("stale", detail="An observed installation artifact changed.")
    cursors = dict(observation.cursors) if observation else {}
    streams = []
    for name in LOGS:
        path = root / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        stat = path.stat()
        if stat.st_size > LIMIT:
            continue
        cursor = cursors.get(name)
        if cursor and (stat.st_size < cursor.size or _prefix(path, cursor.size) != cursor.prefix_sha256):
            return NeuralEvidence("stale", detail="A log was replaced or truncated; start a new observation.")
        if observation and not cursor and stat.st_mtime < observation.started_at:
            continue
        streams.append((stat.st_mtime_ns, name, path, cursor.size if cursor else 0))
    for _, name, path, offset in sorted(streams):
        with path.open("rb") as f:
            f.seek(offset)
            content = f.read(LIMIT).decode("utf-8", errors="replace")
        # Each logfile is one evidence stream. Never combine init from one and evaluation from another.
        initialized, running, failure = False, False, False
        local = "none"
        for line in content.splitlines():
            stage = classify(line)
            if not stage:
                continue
            result.lines.append({"file": name, "line": line[:500], "stage": stage})
            if stage == "failed":
                failure = True
                initialized = running = False
                local = "failed"
            elif stage == "initialized":
                initialized, running, failure, local = True, False, False, stage
            elif stage == "running":
                running, failure, local = True, False, stage
            elif not failure:
                local = stage
        if failure:
            result.state = "failed"
        elif local != "none":
            result.state = local
            live = observation and observation.executable in {str(Path(p).resolve()) for p in process_paths if p}
            if observation and observation.artifacts and initialized and running and live:
                result.state = "verified"
    result.detail = "Backend log evidence; installed, initialized and evaluated are separate states."
    if result.state == "running" and not observation:
        result.detail = "Unattributed historical evaluation report; start observation for the selected installation."
    return result
