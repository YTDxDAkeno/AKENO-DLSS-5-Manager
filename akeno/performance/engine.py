"""The FPS Booster: preview, apply and restore a profile per game, and run its session changes while the game runs.

The journal (``%LOCALAPPDATA%\\AKENO\\performance\\boost.json``) records every original value *before* the change
is made, so a restore always returns to the state before AKENO, even after several profile changes, a crash of
the game or of AKENO, or a reboot:

* persistent changes keep the first original value per game; system-wide ones (Game Mode, background recording)
  are shared and go back only when no boosted game needs them any more;
* session changes (power plan, priorities, paused programs) are journalled while the game runs; if AKENO starts
  and finds an unfinished session whose game is no longer running, it restores it at once.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from akeno.core.db import now_iso
from akeno.i18n import msg
from akeno.performance import tweaks as tw
from akeno.performance.profiles import BY_ID
from akeno.performance.tweaks import Change, Context, Unavailable

log = logging.getLogger(__name__)

SYSTEM_WIDE = {"game_mode", "capture"}
SESSION = {"power_plan", "priority", "background"}
ORDER = ["gpu_preference", "game_mode", "capture", "fullscreen_optimizations", "frame_limit", "power_plan", "priority",
         "background"]


@dataclass
class Preview:
    changes: list[Change] = field(default_factory=list)
    unavailable: list[Unavailable] = field(default_factory=list)
    options: dict = field(default_factory=dict)

    @property
    def persistent(self) -> list[Change]:
        return [c for c in self.changes if c.scope == "persistent"]

    @property
    def session(self) -> list[Change]:
        return [c for c in self.changes if c.scope == "session"]


def _default_path() -> Path:
    from akeno.paths import sub
    return sub("performance") / "boost.json"


class BoostEngine:
    def __init__(self, path: Path | None = None, registry=None, power=None):
        self.path = Path(path) if path else _default_path()
        self.registry = registry
        self.power = power
        self._lock = threading.RLock()
        self.state = self._load()

    # -- journal -----------------------------------------------------------------------------------------------------
    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("games", {})
                data.setdefault("system", {})
                data.setdefault("sessions", {})
                data.setdefault("background", {"names": []})
                return data
        except (OSError, ValueError):
            pass
        return {"games": {}, "system": {}, "sessions": {}, "background": {"names": []}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- context -----------------------------------------------------------------------------------------------------
    def context(self, game, pid: int | None = None, refresh_hz: int | None = None) -> Context:
        exe = getattr(game, "exe_path", None)
        game_dir = os.path.dirname(exe) if exe else getattr(game, "install_dir", None)
        uses_dxvk = False
        if game_dir:
            try:
                from akeno.games.analyzer import dxvk_in
                uses_dxvk = bool(dxvk_in(game_dir))
            except (ImportError, OSError, TypeError):
                uses_dxvk = False
        return Context(exe, game_dir, pid, uses_dxvk, refresh_hz, self.registry, self.power)

    def background_names(self) -> list[str]:
        return list(self.state["background"].get("names", []))

    def set_background(self, names: list[str], action: str = "lower") -> None:
        from akeno.performance.background import protected
        with self._lock:
            self.state["background"] = {"names": sorted({n.lower() for n in names if not protected(n)}),
                                        "action": "pause" if action == "pause" else "lower"}
            self._save()

    def options_for(self, profile_id: str, custom: dict | None = None) -> dict:
        from akeno.performance.background import STREAMING
        base = dict(custom or {}) if profile_id == "custom" else dict(BY_ID[profile_id].options)
        bg = base.get("background")
        if isinstance(bg, dict):
            names = self.background_names()
            if bg.get("keep_streaming"):
                names = [n for n in names if n not in STREAMING]
            action = self.state["background"].get("action", "lower") if bg.get("action") != "pause" else "pause"
            base["background"] = {"names": names, "action": action}
        return base

    # -- preview / apply / restore -----------------------------------------------------------------------------------
    def preview(self, game, profile_id: str, custom: dict | None = None, pid: int | None = None,
                refresh_hz: int | None = None) -> Preview:
        options = self.options_for(profile_id, custom)
        ctx = self.context(game, pid, refresh_hz)
        out = Preview(options=options)
        for tweak_id in ORDER:
            if tweak_id not in options or options[tweak_id] in (None, False, "") and tweak_id != "game_mode":
                continue
            result = tw.TWEAKS[tweak_id].plan(ctx, options[tweak_id])
            if isinstance(result, Unavailable):
                out.unavailable.append(result)
            elif result is not None:
                out.changes.append(result)
        return out

    def apply(self, game, profile_id: str, custom: dict | None = None, refresh_hz: int | None = None) -> Preview:
        """Apply the persistent changes now; the session ones run whenever the game runs (start_session)."""
        with self._lock:
            preview = self.preview(game, profile_id, custom, refresh_hz=refresh_hz)
            ctx = self.context(game, refresh_hz=refresh_hz)
            entry = self.state["games"].setdefault(game.id, {"persistent": {}, "name": getattr(game, "name", "")})
            self._drop_unused(game, ctx, entry, preview.options)
            for change in preview.persistent:
                tweak = tw.TWEAKS[change.tweak]
                if change.tweak in SYSTEM_WIDE:
                    shared = self.state["system"].setdefault(change.tweak, {"games": []})
                    if "backup" not in shared:
                        shared["backup"] = None             # intent first: a crash mid-write is still restorable
                        self._save()
                        shared["backup"] = tweak.apply(ctx, change)
                    else:
                        tweak.apply(ctx, change)
                    if game.id not in shared["games"]:
                        shared["games"].append(game.id)
                else:
                    first = change.tweak not in entry["persistent"]
                    backup = tweak.apply(ctx, change)
                    if first:
                        entry["persistent"][change.tweak] = backup
                self._save()
            for tweak_id in SYSTEM_WIDE & set(preview.options):
                # already in the wanted state because another boosted game set it: this game needs it too
                shared = self.state["system"].get(tweak_id)
                if shared is not None and game.id not in shared["games"]:
                    shared["games"].append(game.id)
            entry.update(profile=profile_id, options=preview.options, applied_at=now_iso(),
                         name=getattr(game, "name", ""))
            self._save()
            return preview

    def _drop_unused(self, game, ctx: Context, entry: dict, options: dict) -> None:
        """A new profile without a tweak the previous one used: that tweak goes back to its original value."""
        for tweak_id in reversed(ORDER):
            if tweak_id in entry["persistent"] and tweak_id not in options:
                tw.TWEAKS[tweak_id].restore(ctx, entry["persistent"].pop(tweak_id))
        for tweak_id, shared in list(self.state["system"].items()):
            if tweak_id not in options and game.id in shared.get("games", []):
                shared["games"].remove(game.id)
                if not shared["games"]:
                    if shared.get("backup") is not None:
                        tw.TWEAKS[tweak_id].restore(ctx, shared["backup"])
                    del self.state["system"][tweak_id]
        self._save()

    def restore(self, game) -> list[str]:
        """Everything AKENO changed for this game goes back to its value before AKENO."""
        with self._lock:
            ctx = self.context(game)
            done = []
            if game.id in self.state["sessions"]:
                self.end_session(game)
            entry = self.state["games"].pop(game.id, None) or {"persistent": {}}
            for tweak_id in reversed(ORDER):
                backup = entry["persistent"].get(tweak_id)
                if backup is not None:
                    tw.TWEAKS[tweak_id].restore(ctx, backup)
                    done.append(tweak_id)
            for tweak_id, shared in list(self.state["system"].items()):
                if game.id in shared.get("games", []):
                    shared["games"].remove(game.id)
                    if not shared["games"]:
                        if shared.get("backup") is not None:
                            tw.TWEAKS[tweak_id].restore(ctx, shared["backup"])
                        del self.state["system"][tweak_id]
                        done.append(tweak_id)
            self._save()
            return done

    def configured(self, game_id: str) -> dict | None:
        return self.state["games"].get(game_id)

    # -- game sessions -----------------------------------------------------------------------------------------------
    def start_session(self, game, pid: int, refresh_hz: int | None = None) -> list[Change]:
        with self._lock:
            entry = self.configured(game.id)
            if not entry or game.id in self.state["sessions"]:
                return []
            options = {k: v for k, v in entry.get("options", {}).items() if k in SESSION}
            if "background" in options:                          # the current selection, not the one at apply time
                options["background"] = self.options_for("custom", {"background": {"action": options["background"]
                                                                                   .get("action", "lower")}})[
                    "background"]
            ctx = self.context(game, pid, refresh_hz)
            session = {"pid": pid, "started_at": now_iso(), "changes": [], "name": getattr(game, "name", "")}
            self.state["sessions"][game.id] = session
            self._save()
            applied = []
            for tweak_id in ORDER:
                if tweak_id not in options:
                    continue
                change = tw.TWEAKS[tweak_id].plan(ctx, options[tweak_id])
                if not isinstance(change, Change):
                    continue
                try:
                    backup = tw.TWEAKS[tweak_id].apply(ctx, change)
                except (OSError, ValueError) as exc:
                    log.warning("session change %s failed: %s", tweak_id, exc)
                    continue
                session["changes"].append({"tweak": tweak_id, "backup": backup})
                self._save()
                applied.append(change)
            return applied

    def end_session(self, game) -> list[str]:
        with self._lock:
            session = self.state["sessions"].pop(game.id, None)
            done = []
            if session:
                ctx = self.context(game)
                for item in reversed(session["changes"]):
                    try:
                        tw.TWEAKS[item["tweak"]].restore(ctx, item["backup"])
                        done.append(item["tweak"])
                    except (OSError, ValueError) as exc:
                        log.warning("could not restore %s: %s", item["tweak"], exc)
            self._save()
            return done

    def recover(self, running_pids: set[int]) -> list[str]:
        """On start: sessions left by a crash whose game no longer runs are restored now."""
        restored = []
        with self._lock:
            for game_id, session in list(self.state["sessions"].items()):
                if session.get("pid") in running_pids:
                    continue
                stub = type("RecoveredGame", (), {"id": game_id, "exe_path": None, "install_dir": None,
                                                  "name": session.get("name", "")})()
                self.end_session(stub)
                restored.append(game_id)
        return restored

    def summary(self, game_id: str) -> dict:
        entry = self.configured(game_id) or {}
        return {"profile": entry.get("profile"), "applied_at": entry.get("applied_at"),
                "persistent": sorted(entry.get("persistent", {})) + sorted(
                    t for t, s in self.state["system"].items() if game_id in s.get("games", [])),
                "session_active": game_id in self.state["sessions"]}


def describe_unavailable(items: list[Unavailable]) -> list[str]:
    return [msg("{t}: {r}", t=i.title, r=i.reason) for i in items]
