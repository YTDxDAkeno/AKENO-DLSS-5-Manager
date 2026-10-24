"""The FPS Booster's changes. Every one is a real, documented Windows setting, shown in a preview before it is
applied, recorded with the exact previous value, and restorable with one click.

Two kinds:

* **persistent** (stay until you restore them): the game's GPU preference, Windows Game Mode, background
  recording, fullscreen optimisations for the game, a frame limit file;
* **session** (only while the game runs, undone when it exits, crashes, or AKENO restarts): the power plan,
  the game's process priority, and lowering or pausing the background programs you chose.

Nothing touches system files, drivers, services, anti-cheat software or other users' settings, and nothing needs
administrator rights. What a tweak cannot do on this PC (no High-performance plan, no frame limiter for this game)
is reported in the preview instead of being skipped silently.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from akeno.i18n import Msg, msg
from akeno.performance import system
from akeno.performance.system import REG_DWORD, REG_SZ, RegValue

GPU_PREF_KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"
GAMEBAR_KEY = r"Software\Microsoft\GameBar"
GAMEDVR_KEY = r"Software\Microsoft\Windows\CurrentVersion\GameDVR"
GAMECONFIG_KEY = r"System\GameConfigStore"
LAYERS_KEY = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
FSO_FLAG = "DISABLEDXMAXIMIZEDWINDOWEDMODE"
HAGS_KEY = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers"
DXVK_MARKER = "# AKENO FPS Booster frame limit"
PRIORITY_LABELS = {"idle": "Idle", "below_normal": "Below normal", "normal": "Normal", "above_normal": "Above normal",
                   "high": "High"}


@dataclass
class Change:
    tweak: str
    title: Msg | str
    before: Msg | str
    after: Msg | str
    scope: str                       # persistent | session
    detail: Msg | str = ""           # exactly what is written where
    data: dict = field(default_factory=dict)


@dataclass
class Unavailable:
    tweak: str
    title: Msg | str
    reason: Msg | str


@dataclass
class Context:
    exe: str | None
    game_dir: str | None = None
    pid: int | None = None
    uses_dxvk: bool = False
    refresh_hz: int | None = None
    registry: object = None
    power: object = None

    def __post_init__(self):
        self.registry = self.registry or system.REGISTRY
        self.power = self.power or system.POWER


def _reg_text(value: RegValue | None) -> str:
    return "—" if value is None else str(value.data)


def _parse_pairs(text: str) -> dict[str, str]:
    out = {}
    for part in (text or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _join_pairs(pairs: dict[str, str]) -> str:
    return "".join(f"{k}={v};" for k, v in pairs.items())


def _snapshot(ctx: Context, items: list[tuple[str, str]]) -> dict:
    return {f"{k}|{n}": (None if (v := ctx.registry.get(k, n)) is None else [v.kind, v.data]) for k, n in items}


def _restore_snapshot(ctx: Context, backup: dict) -> None:
    for key_name, value in backup.items():
        key, name = key_name.split("|", 1)
        if value is None:
            ctx.registry.delete(key, name)
        else:
            ctx.registry.set(key, name, RegValue(value[0], value[1]))


# -- persistent ------------------------------------------------------------------------------------------------------
class GpuPreference:
    id = "gpu_preference"
    title = msg("Graphics processor for this game")
    scope = "persistent"
    LABELS = {"0": "Let Windows decide", "1": "Power saving", "2": "High performance"}

    def plan(self, ctx: Context, option: str) -> Change | Unavailable | None:
        if not ctx.exe:
            return Unavailable(self.id, self.title, msg("The game's executable is not known."))
        target = {"high": "2", "power_saving": "1"}[option]
        current = ctx.registry.get(GPU_PREF_KEY, ctx.exe)
        pairs = _parse_pairs(str(current.data) if current else "")
        if pairs.get("GpuPreference") == target:
            return None
        before = msg(self.LABELS.get(pairs.get("GpuPreference", "0"), "Let Windows decide"))
        pairs["GpuPreference"] = target
        return Change(self.id, self.title, before, msg(self.LABELS[target]), self.scope,
                      msg("HKCU\\{k}: {exe} = {v}", k=GPU_PREF_KEY, exe=ctx.exe, v=_join_pairs(pairs)),
                      {"value": _join_pairs(pairs)})

    def apply(self, ctx: Context, change: Change) -> dict:
        backup = _snapshot(ctx, [(GPU_PREF_KEY, ctx.exe)])
        ctx.registry.set(GPU_PREF_KEY, ctx.exe, RegValue(REG_SZ, change.data["value"]))
        return backup

    def restore(self, ctx: Context, backup: dict) -> None:
        _restore_snapshot(ctx, backup)


class GameMode:
    id = "game_mode"
    title = msg("Windows Game Mode")
    scope = "persistent"
    VALUES = [(GAMEBAR_KEY, "AutoGameModeEnabled"), (GAMEBAR_KEY, "AllowAutoGameMode")]

    def plan(self, ctx: Context, option: bool) -> Change | None:
        current = [ctx.registry.get(k, n) for k, n in self.VALUES]
        # Windows treats a missing AutoGameModeEnabled as on (the default since Windows 10 1903)
        enabled = current[0] is None or int(current[0].data) == 1
        if enabled == bool(option) and (current[1] is None or int(current[1].data) == int(bool(option))):
            return None
        return Change(self.id, self.title, msg("On") if enabled else msg("Off"), msg("On") if option else msg("Off"),
                      self.scope, msg("HKCU\\{k}: AutoGameModeEnabled = {v}, AllowAutoGameMode = {v}", k=GAMEBAR_KEY,
                                      v=int(bool(option))), {"value": int(bool(option))})

    def apply(self, ctx: Context, change: Change) -> dict:
        backup = _snapshot(ctx, self.VALUES)
        for key, name in self.VALUES:
            ctx.registry.set(key, name, RegValue(REG_DWORD, change.data["value"]))
        return backup

    def restore(self, ctx: Context, backup: dict) -> None:
        _restore_snapshot(ctx, backup)


class BackgroundRecording:
    """Xbox Game Bar's background recording ("record what happened") encodes video all the time."""

    id = "capture"
    title = msg("Background recording (Game Bar captures)")
    scope = "persistent"
    VALUES = [(GAMEDVR_KEY, "AppCaptureEnabled"), (GAMECONFIG_KEY, "GameDVR_Enabled")]

    def plan(self, ctx: Context, option: str) -> Change | None:
        current = [ctx.registry.get(k, n) for k, n in self.VALUES]
        on = any(v is None or int(v.data) != 0 for v in current)
        want_on = option == "on"
        if on == want_on:
            return None
        return Change(self.id, self.title, msg("On") if on else msg("Off"), msg("On") if want_on else msg("Off"),
                      self.scope, msg("HKCU\\{a}: AppCaptureEnabled = {v}; HKCU\\{b}: GameDVR_Enabled = {v}",
                                      a=GAMEDVR_KEY, b=GAMECONFIG_KEY, v=int(want_on)), {"value": int(want_on)})

    def apply(self, ctx: Context, change: Change) -> dict:
        backup = _snapshot(ctx, self.VALUES)
        for key, name in self.VALUES:
            ctx.registry.set(key, name, RegValue(REG_DWORD, change.data["value"]))
        return backup

    def restore(self, ctx: Context, backup: dict) -> None:
        _restore_snapshot(ctx, backup)


class FullscreenOptimizations:
    """Windows' compatibility flag; helps some older exclusive-fullscreen games and hurts others (opt-in only)."""

    id = "fullscreen_optimizations"
    title = msg("Fullscreen optimisations for this game")
    scope = "persistent"

    def plan(self, ctx: Context, option: str) -> Change | Unavailable | None:
        if not ctx.exe:
            return Unavailable(self.id, self.title, msg("The game's executable is not known."))
        current = ctx.registry.get(LAYERS_KEY, ctx.exe)
        flags = [f for f in str(current.data if current else "").split() if f != "~"]
        disabled = FSO_FLAG in flags
        want_disabled = option == "off"
        if disabled == want_disabled:
            return None
        flags = [f for f in flags if f != FSO_FLAG] + ([FSO_FLAG] if want_disabled else [])
        value = ("~ " + " ".join(flags)) if flags else ""
        return Change(self.id, self.title, msg("Off") if disabled else msg("On"),
                      msg("Off") if want_disabled else msg("On"), self.scope,
                      msg("HKCU\\{k}: {exe} = {v}", k=LAYERS_KEY, exe=ctx.exe, v=value or "—"), {"value": value})

    def apply(self, ctx: Context, change: Change) -> dict:
        backup = _snapshot(ctx, [(LAYERS_KEY, ctx.exe)])
        if change.data["value"]:
            ctx.registry.set(LAYERS_KEY, ctx.exe, RegValue(REG_SZ, change.data["value"]))
        else:
            ctx.registry.delete(LAYERS_KEY, ctx.exe)
        return backup

    def restore(self, ctx: Context, backup: dict) -> None:
        _restore_snapshot(ctx, backup)


class FrameLimit:
    """A real frame cap: RivaTuner Statistics Server's per-game profile if RTSS is installed, else ``dxvk.conf``
    for games that render through DXVK. Other games: use the game's own limiter or the NVIDIA app."""

    id = "frame_limit"
    title = msg("Frame limit")
    scope = "persistent"

    def _target(self, ctx: Context, option) -> int:
        if option == "refresh-3":
            return max(30, (ctx.refresh_hz or 60) - 3)
        return int(option)

    def _where(self, ctx: Context) -> tuple[str, Path] | None:
        rtss = system.rtss_dir()
        if rtss and ctx.exe:
            return "rtss", Path(rtss) / "Profiles" / (os.path.basename(ctx.exe) + ".cfg")
        if ctx.uses_dxvk and ctx.game_dir:
            return "dxvk", Path(ctx.game_dir) / "dxvk.conf"
        return None

    def plan(self, ctx: Context, option) -> Change | Unavailable | None:
        where = self._where(ctx)
        if where is None:
            return Unavailable(self.id, self.title, msg(
                "No frame limiter AKENO can configure for this game: set the limit in the game or in the NVIDIA app "
                "(Max Frame Rate), or install RivaTuner Statistics Server."))
        fps = self._target(ctx, option)
        kind, path = where
        old = path.read_text(encoding="utf-8", errors="replace") if path.exists() else None
        new = self._render(kind, old, fps)
        if new == old:
            return None
        current = self._current(kind, old)
        return Change(self.id, self.title, msg("{n} FPS", n=current) if current else msg("No limit"),
                      msg("{n} FPS", n=fps), self.scope,
                      msg("{tool}: {path}", tool="RivaTuner" if kind == "rtss" else "DXVK", path=str(path)),
                      {"path": str(path), "content": new, "fps": fps})

    @staticmethod
    def _current(kind: str, text: str | None) -> int | None:
        for line in (text or "").splitlines():
            key, _, value = line.partition("=")
            key = key.strip().lower()
            if (kind == "rtss" and key == "limit") or (kind == "dxvk" and key in ("dxgi.maxframerate",
                                                                                  "d3d9.maxframerate")):
                try:
                    return int(float(value.strip())) or None
                except ValueError:
                    return None
        return None

    @staticmethod
    def _render(kind: str, old: str | None, fps: int) -> str:
        if kind == "rtss":
            lines = (old or "").splitlines()
            out, in_section, written = [], False, False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("["):
                    if in_section and not written:
                        out.append(f"Limit={fps}")
                        written = True
                    in_section = stripped.lower() == "[framerate]"
                elif in_section and stripped.lower().startswith("limit="):
                    line, written = f"Limit={fps}", True
                out.append(line)
            if not written:
                if not in_section:
                    out.append("[Framerate]")
                out.append(f"Limit={fps}")
            return "\n".join(out) + "\n"
        keep = [ln for ln in (old or "").splitlines() if not ln.strip().lower().startswith(
            ("dxgi.maxframerate", "d3d9.maxframerate")) and ln.strip() != DXVK_MARKER]
        return "\n".join([*keep, DXVK_MARKER, f"dxgi.maxFrameRate = {fps}", f"d3d9.maxFrameRate = {fps}"]) + "\n"

    def apply(self, ctx: Context, change: Change) -> dict:
        path = Path(change.data["path"])
        backup = {"path": str(path), "content": path.read_text(encoding="utf-8") if path.exists() else None}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".akeno-tmp")
        tmp.write_text(change.data["content"], encoding="utf-8")
        os.replace(tmp, path)
        return backup

    def restore(self, ctx: Context, backup: dict) -> None:
        path = Path(backup["path"])
        if backup.get("content") is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(backup["content"], encoding="utf-8")


# -- session (while the game runs) -----------------------------------------------------------------------------------
class PowerPlan:
    id = "power_plan"
    title = msg("Power plan while playing")
    scope = "session"
    TARGETS = {"high": (system.ULTIMATE, system.HIGH_PERFORMANCE), "saver": (system.POWER_SAVER,),
               "balanced": (system.BALANCED,)}

    def plan(self, ctx: Context, option: str) -> Change | Unavailable | None:
        try:
            schemes = {s.guid: s.name for s in ctx.power.list()}
            active = ctx.power.active()
        except (OSError, ValueError) as exc:
            return Unavailable(self.id, self.title, msg("Power plans cannot be read: {e}", e=str(exc)))
        target = next((g for g in self.TARGETS[option] if g in schemes), None)
        if target is None:
            return Unavailable(self.id, self.title, msg(
                "This PC has no {plan} plan (many laptops with Modern Standby only offer Balanced). Nothing is "
                "created or changed.", plan=msg("High performance") if option == "high" else msg("Power saver")))
        if target == active:
            return None
        return Change(self.id, self.title, schemes.get(active, active), schemes[target], self.scope,
                      msg("powercfg /setactive {g} (the previous plan returns when the game exits)", g=target),
                      {"guid": target})

    def apply(self, ctx: Context, change: Change) -> dict:
        backup = {"guid": ctx.power.active()}
        ctx.power.set_active(change.data["guid"])
        return backup

    def restore(self, ctx: Context, backup: dict) -> None:
        ctx.power.set_active(backup["guid"])


class GamePriority:
    id = "priority"
    title = msg("Priority of the game process")
    scope = "session"

    def plan(self, ctx: Context, option: str) -> Change:
        current = system.priority_of(ctx.pid) if ctx.pid else None
        return Change(self.id, self.title, msg(PRIORITY_LABELS[current or "normal"]), msg(PRIORITY_LABELS[option]),
                      self.scope,
                      msg("Process priority class {p} (never realtime); ends with the game", p=option),
                      {"priority": option})

    def apply(self, ctx: Context, change: Change) -> dict:
        if not ctx.pid:
            return {}
        before = system.priority_of(ctx.pid) or "normal"
        system.set_priority(ctx.pid, change.data["priority"])
        return {"pid": ctx.pid, "priority": before}

    def restore(self, ctx: Context, backup: dict) -> None:
        if backup.get("pid"):
            system.set_priority(backup["pid"], backup["priority"])


class BackgroundPrograms:
    """Only programs you selected: lowered to below-normal priority, or paused (opt-in) while the game runs."""

    id = "background"
    title = msg("Background programs while playing")
    scope = "session"

    def plan(self, ctx: Context, option: dict) -> Change | None:
        names = sorted(option.get("names") or [])
        if not names:
            return None
        action = option.get("action", "lower")
        return Change(self.id, self.title, msg("Untouched"),
                      msg("Paused: {n}", n=", ".join(names)) if action == "pause"
                      else msg("Lower priority: {n}", n=", ".join(names)), self.scope,
                      msg("Resumed and set back when the game exits, crashes or AKENO restarts"),
                      {"names": names, "action": action})

    def apply(self, ctx: Context, change: Change) -> dict:
        from akeno.performance.background import matching_processes
        touched = []
        for proc in matching_processes(change.data["names"], exclude_pid=ctx.pid):
            if change.data["action"] == "pause":
                if system.suspend(proc.pid):
                    touched.append({"pid": proc.pid, "name": proc.name, "paused": True})
            else:
                before = system.priority_of(proc.pid) or "normal"
                if system.set_priority(proc.pid, "below_normal"):
                    touched.append({"pid": proc.pid, "name": proc.name, "priority": before})
        return {"processes": touched}

    def restore(self, ctx: Context, backup: dict) -> None:
        for item in backup.get("processes", []):
            if item.get("paused"):
                system.resume(item["pid"])
            elif item.get("priority"):
                system.set_priority(item["pid"], item["priority"])


TWEAKS = {t.id: t for t in (GpuPreference(), GameMode(), BackgroundRecording(), FullscreenOptimizations(),
                            FrameLimit(), PowerPlan(), GamePriority(), BackgroundPrograms())}


def hags_state(registry=None) -> str:
    """Hardware-accelerated GPU scheduling (read only: changing it needs administrator rights and a restart)."""
    value = (registry or system.REGISTRY).read_machine_dword(HAGS_KEY, "HwSchMode")
    return {2: "on", 1: "off"}.get(value, "unknown")
