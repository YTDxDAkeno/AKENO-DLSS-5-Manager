"""Game library service: discovery, analysis, persistence, user overrides."""

from __future__ import annotations

import logging
import os

from akeno.core.db import Database
from akeno.core.models import Game
from akeno.games import analyzer, launchers
from akeno.games.launchers import ManualGameError, RawGame

log = logging.getLogger(__name__)


class GameLibrary:
    def __init__(self, db: Database):
        self.db = db
        self.last_errors: list[str] = []
        self.fivem_installations: dict = {}

    def _raw_from_game(self, g: Game) -> RawGame:
        return RawGame(id=g.id, name=g.name, install_dir=g.install_dir, launcher=g.launcher,
                       launcher_id=g.launcher_id, exe_hint=g.exe_path)

    def games(self) -> list[Game]:
        out = []
        for data, _manual, _ov in self.db.games():
            try:
                out.append(Game.from_dict(data))
            except (TypeError, ValueError) as exc:
                log.warning("dropping unreadable game record %s: %s", data.get("id"), exc)
        return sorted(out, key=lambda g: g.name.lower())

    def get(self, game_id: str | None) -> Game | None:
        if not game_id:
            return None
        return next((g for g in self.games() if g.id == game_id), None)

    def manual_dirs(self) -> list[str]:
        return [d["install_dir"] if not d.get("exe_path") else d["exe_path"]
                for d, manual, _ in self.db.games() if manual and d.get("launcher") != "fivem"]

    def scan(self, extra_manual: list[str] | None = None, progress=None) -> list[Game]:
        """Discover installed games and analyse each. Previously known manual games are kept."""
        manual = list(dict.fromkeys((extra_manual or []) + self.manual_dirs()))
        from akeno.fivem.discovery import discover
        portable = [d["install_dir"] for d, _, _ in self.db.games() if d.get("launcher") == "fivem"]
        installations = discover([*portable, *(extra_manual or [])])
        self.fivem_installations = {i.game.id: i for i in installations}
        roots = {str(i.root) for i in installations} | {str(i.app_dir) for i in installations}
        manual = [p for p in manual if os.path.dirname(p) not in roots and p not in roots]
        raws, errors = launchers.scan_all(manual)
        self.last_errors = errors
        from akeno.core import processes
        try:
            procs = processes.list_processes()
        except Exception:
            procs = []
        result = []
        found_ids = set()
        for installation in installations:
            game = installation.game
            self.db.upsert_game(game.to_dict(), manual=False)
            result.append(game)
            found_ids.add(game.id)
        for i, raw in enumerate(raws):
            if raw.install_dir in roots:
                continue
            if progress:
                progress(i, len(raws), raw.name)
            ov = self.db.game_overrides(raw.id)
            try:
                game = analyzer.analyze(raw, ov.get("exe_path"), ov.get("api"), procs)
            except Exception as exc:
                log.exception("analysis failed for %s", raw.name)
                errors.append(f"{raw.name}: analysis failed ({exc})")
                continue
            game.artwork_path = analyzer.find_artwork(game)
            self.db.upsert_game(game.to_dict(), manual=raw.launcher == "manual")
            result.append(game)
            found_ids.add(game.id)
        # Launcher games that disappeared (uninstalled) are dropped unless AKENO manages files there.
        managed = {t["game_id"] for t in self.db.transactions() if t["state"] == "installed"}
        for data, manual, _ in self.db.games():
            if data["id"] not in found_ids and not manual and data["id"] not in managed:
                self.db.delete_game(data["id"])
        return sorted(result, key=lambda g: g.name.lower())

    def add_manual(self, path: str, name: str | None = None) -> Game:
        from akeno.fivem.discovery import inspect_installation
        installation = inspect_installation(path)
        if installation:
            self.fivem_installations[installation.game.id] = installation
            self.db.upsert_game(installation.game.to_dict(), manual=False)
            return installation.game
        raw = launchers.manual_game(path, name)
        game = analyzer.analyze(raw)
        if not game.exe_path:
            raise ManualGameError("No usable game executable was found.")
        self.db.upsert_game(game.to_dict(), manual=True)
        return game

    def reanalyze(self, game_id: str) -> Game | None:
        g = self.get(game_id)
        if not g:
            return None
        if g.launcher == "fivem":
            from akeno.fivem.discovery import inspect_installation
            installation = inspect_installation(g.install_dir)
            if installation:
                self.fivem_installations[g.id] = installation
                self.db.upsert_game(installation.game.to_dict(), manual=False)
                return installation.game
            self.fivem_installations.pop(g.id, None)
            return None
        ov = self.db.game_overrides(game_id)
        raw = self._raw_from_game(g)
        if g.launcher != "manual":
            raw.exe_hint = None
        new = analyzer.analyze(raw, ov.get("exe_path") or (g.exe_path if g.launcher == "manual" else None),
                               ov.get("api"))
        new.artwork_path = analyzer.find_artwork(new) or g.artwork_path
        self.db.upsert_game(new.to_dict(), manual=g.launcher == "manual")
        return new

    def set_override(self, game_id: str, key: str, value) -> Game | None:
        ov = self.db.game_overrides(game_id)
        if value in (None, ""):
            ov.pop(key, None)
        else:
            ov[key] = value
        self.db.set_game_overrides(game_id, ov)
        return self.reanalyze(game_id)

    def remove(self, game_id: str) -> None:
        self.db.delete_game(game_id)

    @staticmethod
    def exists(game: Game) -> bool:
        return bool(game.exe_path and os.path.isfile(game.exe_path))
