"""What the Universal Mod Scanner reports: mods, their dependencies, and the groups they belong to."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from akeno.system.model import Finding

# where a mod came from
SOURCE_AKENO = "akeno"          # installed and journalled by AKENO (restorable)
SOURCE_EXTERNAL = "external"    # installed by the user or another tool: read-only for AKENO
SOURCE_CLIENT = "client"        # part of the game client itself (FiveM's own components)
SOURCE_SERVER = "server"        # cached from a game server (FiveM server cache): not a local mod
SOURCE_UNKNOWN = "unknown"
SOURCE_TEXT = {SOURCE_AKENO: "AKENO", SOURCE_EXTERNAL: "External", SOURCE_CLIENT: "Game client",
               SOURCE_SERVER: "Server cache", SOURCE_UNKNOWN: "Unknown"}


@dataclass
class Dependency:
    id: str
    versions: str = "*"            # the mod's own notation (Fabric predicate or Maven range)
    kind: str = "required"         # required | optional | incompatible | conflicts
    notation: str = "semver"       # semver (Fabric/Quilt) | maven (Forge/NeoForge)


@dataclass
class ModItem:
    name: str
    path: str
    kind: str                      # minecraft_mod | asi_plugin | reshade | optiscaler | proxy_dll | bepinex_plugin |
                                   # melonloader_mod | ue_pak | ue4ss_mod | rpf | unknown ...
    source: str = SOURCE_EXTERNAL
    detection: str = ""            # what identified it (fabric.mod.json, PE version info, folder name...)
    loader: str | None = None
    loaders: list[str] = field(default_factory=list)      # every loader the archive declares
    mod_id: str | None = None
    version: str | None = None
    minecraft: str | None = None   # the Minecraft versions it declares
    depends: list[Dependency] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    nested: list[str] = field(default_factory=list)       # ids of bundled (jar-in-jar) mods
    size: int = 0
    mtime: float = 0.0
    enabled: bool = True
    error: str = ""                # why its metadata could not be read
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["depends"] = [x.__dict__ for x in self.depends]
        return d


@dataclass
class ModGroup:
    key: str                       # "game:<id>" | "minecraft:<hash>"
    title: str
    platform: str                  # minecraft | fivem | game
    root: str
    game_id: str | None = None
    launcher: str = ""
    loader: str | None = None
    loader_version: str | None = None
    game_version: str | None = None
    mods: list[ModItem] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    scanned_at: float = field(default_factory=time.time)
    status: str = "ok"             # ok | partial | error

    @property
    def warnings(self) -> int:
        from akeno.system.model import Level
        return sum(1 for f in self.findings if f.level in (Level.WARN, Level.PROBLEM)) + \
            sum(1 for m in self.mods if m.error)

    def sources(self) -> set[str]:
        return {m.source for m in self.mods}

    def summary(self) -> dict[str, Any]:
        return {"key": self.key, "title": str(self.title), "platform": self.platform, "root": self.root,
                "game_id": self.game_id, "launcher": self.launcher, "loader": self.loader,
                "loader_version": self.loader_version, "game_version": self.game_version, "count": len(self.mods),
                "akeno": sum(1 for m in self.mods if m.source == SOURCE_AKENO), "warnings": self.warnings,
                "scanned_at": self.scanned_at, "status": self.status}
