"""Universal Mod Scanner service: scans the library's games, FiveM and Minecraft instances (no Qt, read-only)."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from akeno.i18n import msg
from akeno.system.mods import games as game_mods
from akeno.system.mods import minecraft
from akeno.system.mods.model import Dependency, ModGroup, ModItem
from akeno.system.model import Finding
from akeno.system.tasks import Cancelled, TaskContext, TimedOut, context

LAST_KEY = "mods.last"
FOLDERS_KEY = "mods.minecraft_folders"


class ModScanner:
    def __init__(self, services, store, env: dict | None = None):
        self.s = services
        self.store = store
        self.env = env
        self.groups: list[ModGroup] = self._load()

    # -- Minecraft folders the user adds ------------------------------------------------------------------------
    def minecraft_folders(self) -> list[str]:
        return list(self.store.get(FOLDERS_KEY, []) or [])

    def add_minecraft_folder(self, folder: str) -> None:
        if not os.path.isdir(os.path.join(folder, "mods")):
            raise ValueError(msg("This folder has no mods folder."))
        folders = self.minecraft_folders()
        if folder not in folders:
            folders.append(folder)
            self.store.set(FOLDERS_KEY, folders)

    # -- scanning -------------------------------------------------------------------------------------------------
    def scan(self, ctx: TaskContext | None = None) -> list[ModGroup]:
        ctx = context(ctx)
        groups: list[ModGroup] = []
        library = self.s.library.games()
        for i, game in enumerate(library):
            ctx.check()
            ctx.report(msg("Scanning {name}", name=game.name), i, len(library) + 1)
            try:
                g = self._scan_game(game, ctx)
            except (Cancelled, TimedOut):
                raise
            except Exception as exc:                    # one broken game folder never ends the scan
                g = ModGroup("game:" + game.id, game.name, "game", game.install_dir, game_id=game.id, status="error",
                             errors=[str(exc)])
            if g is not None and (g.mods or g.errors):
                groups.append(g)
        ctx.report(msg("Looking for Minecraft instances"), len(library), len(library) + 1)
        for inst in minecraft.discover(self.env, self.minecraft_folders()):
            ctx.check()
            groups.append(minecraft.scan_instance(inst, ctx))
        self.groups = groups
        self._save()
        self.store.add_history("mods.scan", {"groups": [g.summary() for g in groups]}, keep=30)
        return groups

    def _scan_game(self, game, ctx) -> ModGroup | None:
        if game.launcher == "fivem":
            from akeno.fivem.discovery import inspect_installation
            inst = self.s.library.fivem_installations.get(game.id) or inspect_installation(game.install_dir)
            if inst is None:
                return None
            managed = self.s.installer.managed_files(game.id, str(inst.plugin_dir))
            return game_mods.scan_fivem(inst, game.id, managed, ctx)
        managed = self.s.installer.managed_files(game.id, game.target_dir) if game.target_dir else set()
        return game_mods.scan_game(game, managed, ctx)

    def rescan(self, key: str, ctx: TaskContext | None = None) -> ModGroup | None:
        ctx = context(ctx)
        new = None
        if key.startswith("game:"):
            game = self.s.library.get(key[5:])
            if game is not None:
                new = self._scan_game(game, ctx)
        else:
            for inst in minecraft.discover(self.env, self.minecraft_folders()):
                if inst.key == key:
                    new = minecraft.scan_instance(inst, ctx)
        self.groups = [g for g in self.groups if g.key != key] + ([new] if new is not None and (new.mods or new.errors)
                                                                  else [])
        self._save()
        return new

    # -- persistence (this PC only) -------------------------------------------------------------------------------
    def _save(self) -> None:
        self.store.set(LAST_KEY, [_group_dict(g) for g in self.groups])

    def _load(self) -> list[ModGroup]:
        try:
            return [_group_from(d) for d in self.store.get(LAST_KEY, []) or []]
        except Exception:                       # noqa: BLE001 (saved by another version: replaced by the next scan)
            logging.getLogger(__name__).warning("the saved mod library could not be read; the next scan replaces it",
                                                exc_info=True)
            return []

    def last_scan(self) -> float | None:
        return max((g.scanned_at for g in self.groups), default=None)


def _group_dict(g: ModGroup) -> dict[str, Any]:
    d = g.summary()
    d["title"] = _enc(g.title)
    d["mods"] = [m.to_dict() for m in g.mods]
    d["findings"] = [f.to_dict() for f in g.findings]
    d["errors"] = [str(e) for e in g.errors]
    d["notes"] = [str(n) for n in g.notes]
    return json.loads(json.dumps(d, default=str))


def _enc(text):
    from akeno.system.model import enc
    return enc(text)


def _group_from(d: dict[str, Any]) -> ModGroup:
    from akeno.system.model import dec
    g = ModGroup(d["key"], dec(d["title"]), d["platform"], d["root"], d.get("game_id"), d.get("launcher", ""),
                 d.get("loader"), d.get("loader_version"), d.get("game_version"))
    for m in d.get("mods", []):
        deps = [Dependency(**x) for x in m.pop("depends", [])]
        g.mods.append(ModItem(**{**m, "depends": deps}))
    g.findings = [Finding.from_dict(f) for f in d.get("findings", [])]
    g.errors, g.notes = list(d.get("errors", [])), list(d.get("notes", []))
    g.scanned_at, g.status = d.get("scanned_at") or time.time(), d.get("status", "ok")
    return g
