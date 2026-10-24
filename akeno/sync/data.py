"""What Cloud Sync reads from and writes to this PC: fixed lists of fields, nothing else.

* settings: preferences such as language and benchmark length (never the GitHub token, paths or debug options)
* games:    favourite, category, notes, launch options, playtime and launches per PC
* dlss:     the chosen DLSS 5 method, preset and custom values per game (installing stays a separate step)
* booster:  the chosen FPS Booster profile per game; another PC only gets it as a suggestion, its Windows
            settings change only when the user applies it there
* benchmarks: measured results (statistics and the system they ran on, never the capture files)
* desktop:  widgets, pause rules, dock style (dock pins and wallpaper files are this PC's paths)
"""

from __future__ import annotations

import copy

from akeno.desktop import config as desktop_config

SETTINGS = ("language", "theme_accent", "animations", "ui_scale", "check_app_updates", "auto_update_compat_db",
            "check_mod_updates", "cache_limit_mb", "backup_keep_per_game", "verify_backups_after_create",
            "benchmark_duration_s", "benchmark_delay_s", "target_fps", "notifications", "rescan_on_start",
            "block_unknown_conflicts", "intelligent_game_mode")
GAME_FIELDS = ("favorite", "category", "notes", "launch_args")
BENCH_FIELDS = ("label", "game_id", "created_at", "stats", "gpu", "driver", "resolution", "backend_id", "source",
                "process_name", "started_at")
DOCK_STYLE = ("edge", "floating", "autohide", "opacity", "blur", "icon_size", "screen")
BOOST_KEY = "boost_choice"


class LocalData:
    def __init__(self, services, desktop_path=None):
        self.s = services
        self.desktop_path = desktop_path

    # -- reading -----------------------------------------------------------------------------------------------------
    def collect(self, categories: set[str], engine) -> dict:
        out = {}
        db = self.s.db
        if "settings" in categories:
            all_settings = self.s.settings.as_dict()
            out["settings"] = {k: all_settings.get(k) for k in SETTINGS}
        if "games" in categories:
            others = engine.state["others"]
            for game_id, meta in db.all_game_meta().items():
                theirs = others.get(game_id, {})
                own_time = max(0, int(meta["playtime_s"]) - int(sum(theirs.get("playtime", {}).values())))
                own_runs = max(0, int(meta["launches"]) - int(sum(theirs.get("launches", {}).values())))
                item = {k: meta[k] for k in GAME_FIELDS}
                item["favorite"] = bool(item["favorite"])
                item.update(playtime={**theirs.get("playtime", {}), engine.device_id: own_time},
                            launches={**theirs.get("launches", {}), engine.device_id: own_runs},
                            last_played=meta["last_played"])
                if any(item[k] for k in GAME_FIELDS) or own_time or theirs:
                    out[f"game/{game_id}"] = item
        if "dlss" in categories or "booster" in categories:
            for data, _manual, overrides in db.games():
                game_id = data.get("id")
                sel = overrides.get("stack") or {}
                if "dlss" in categories and sel.get("recipe"):
                    out[f"dlss/{game_id}"] = {"recipe": sel.get("recipe"), "preset": sel.get("preset"),
                                              "custom": sel.get("custom")}
                if "booster" in categories:
                    entry = self.s.boost.configured(game_id) if getattr(self.s, "boost", None) else None
                    if entry and entry.get("profile"):
                        out[f"booster/{game_id}"] = {"profile": entry["profile"],
                                                     "options": copy.deepcopy(entry.get("options") or {})}
                    elif overrides.get(BOOST_KEY):
                        choice = overrides[BOOST_KEY]
                        out[f"booster/{game_id}"] = {"profile": choice.get("profile"),
                                                     "options": choice.get("options") or {}}
        if "benchmarks" in categories:
            for bench in db.benchmarks():
                synced = bench.get("synced")
                if synced:
                    out[synced["key"]] = synced["payload"]
                else:
                    out[f"bench/{engine.device_id}/{bench['id']}"] = dict(
                        {k: bench.get(k) for k in BENCH_FIELDS}, device=engine.device_name)
        if "desktop" in categories:
            cfg = desktop_config.load(self.desktop_path)
            out["desktop"] = {"pause": cfg["pause"], "widgets": cfg["widgets"],
                              "widgets_locked": cfg.get("widgets_locked", False),
                              "dock_style": {k: cfg["dock"].get(k) for k in DOCK_STYLE}}
        return out

    def baseline(self, key: str) -> dict | None:
        """An item as a fresh installation has it: what a PC joining the sync compares against."""
        from akeno.core.settings import DEFAULTS
        if key == "settings":
            return {k: DEFAULTS.get(k) for k in SETTINGS}
        if key.startswith("game/"):
            return {"favorite": False, "category": "", "notes": "", "launch_args": "", "playtime": {},
                    "launches": {}, "last_played": None}
        if key == "desktop":
            return {"pause": dict(desktop_config.PAUSE_DEFAULTS), "widgets": [], "widgets_locked": False,
                    "dock_style": {k: desktop_config.DOCK_DEFAULTS.get(k) for k in DOCK_STYLE}}
        return None

    # -- writing -----------------------------------------------------------------------------------------------------
    def _has_game(self, game_id: str) -> bool:
        return bool(self.s.db.query("SELECT 1 FROM games WHERE id=?", (game_id,)))

    def apply(self, key: str, payload, engine) -> bool:
        """Write one item here. False: it cannot be applied yet (the game is not in this PC's library)."""
        kind, _, rest = key.partition("/")
        db = self.s.db
        if key == "settings":
            for k in SETTINGS:
                if k in payload and payload[k] != self.s.settings.get(k):
                    self.s.settings.set(k, payload[k])
            return True
        if kind == "game":
            others_time = {d: int(v) for d, v in (payload.get("playtime") or {}).items() if d != engine.device_id}
            others_runs = {d: int(v) for d, v in (payload.get("launches") or {}).items() if d != engine.device_id}
            meta = db.game_meta(rest)
            old = engine.state["others"].get(rest, {})
            own_time = max(0, int(meta["playtime_s"]) - int(sum(old.get("playtime", {}).values())))
            own_runs = max(0, int(meta["launches"]) - int(sum(old.get("launches", {}).values())))
            fields = {k: payload[k] for k in GAME_FIELDS if k in payload}
            if "favorite" in fields:
                fields["favorite"] = 1 if fields["favorite"] else 0
            last = max([x for x in (meta["last_played"], payload.get("last_played")) if x], default=None)
            db.set_game_meta(rest, playtime_s=own_time + sum(others_time.values()),
                             launches=own_runs + sum(others_runs.values()), last_played=last, **fields)
            engine.state["others"][rest] = {"playtime": others_time, "launches": others_runs}
            return True
        if kind == "dlss":
            if not self._has_game(rest):
                return False
            overrides = db.game_overrides(rest)
            sel = dict(overrides.get("stack") or {})
            for k in ("recipe", "preset", "custom"):
                if payload.get(k) is None:
                    sel.pop(k, None)
                else:
                    sel[k] = payload[k]
            overrides["stack"] = sel
            db.set_game_overrides(rest, overrides)
            return True
        if kind == "booster":
            if not self._has_game(rest):
                return False
            entry = self.s.boost.configured(rest) if getattr(self.s, "boost", None) else None
            if entry and entry.get("profile") == payload.get("profile") and \
                    (entry.get("options") or {}) == (payload.get("options") or {}):
                return True
            overrides = db.game_overrides(rest)
            overrides[BOOST_KEY] = {"profile": payload.get("profile"), "options": payload.get("options") or {}}
            db.set_game_overrides(rest, overrides)
            return True
        if kind == "bench":
            for bench in db.benchmarks():
                if (bench.get("synced") or {}).get("key") == key:
                    db.delete_benchmark(bench["id"])
            data = {k: payload.get(k) for k in BENCH_FIELDS if k not in ("label", "game_id")}
            data["synced"] = {"key": key, "payload": payload}
            db.add_benchmark(payload.get("game_id") or "", payload.get("label") or "", data)
            return True
        if key == "desktop":
            cfg = desktop_config.load(self.desktop_path)
            cfg["pause"] = dict(cfg["pause"], **(payload.get("pause") or {}))
            cfg["widgets"] = payload.get("widgets") or []
            cfg["widgets_locked"] = bool(payload.get("widgets_locked"))
            cfg["dock"].update({k: v for k, v in (payload.get("dock_style") or {}).items() if k in DOCK_STYLE})
            desktop_config.save(cfg, self.desktop_path)
            return True
        return False

    def remove(self, key: str, engine) -> None:
        kind, _, rest = key.partition("/")
        db = self.s.db
        if kind == "bench":
            for bench in db.benchmarks():
                if (bench.get("synced") or {}).get("key") == key:
                    db.delete_benchmark(bench["id"])
                elif key == f"bench/{engine.device_id}/{bench['id']}":
                    db.delete_benchmark(bench["id"])
        elif kind == "game":
            db.set_game_meta(rest, **{k: ("" if k != "favorite" else 0) for k in GAME_FIELDS})
        elif kind in ("dlss", "booster") and self._has_game(rest):
            overrides = db.game_overrides(rest)
            if kind == "dlss":
                overrides["stack"] = {k: v for k, v in (overrides.get("stack") or {}).items()
                                      if k not in ("recipe", "preset", "custom")}
            else:
                overrides.pop(BOOST_KEY, None)
            db.set_game_overrides(rest, overrides)
