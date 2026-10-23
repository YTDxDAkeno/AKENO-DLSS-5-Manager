"""Schema 3 component catalog: components and their versions, install recipes and game profiles.

``akeno/data/stack/components.json`` lists every component version AKENO can obtain. A version from a
GitHub release names the original asset URL, the archive SHA-256 and the SHA-256 of every file AKENO
extracts from it, all computed from the original bytes. ``recipes.json`` says which roles a complete
installation needs, ``games.json`` carries per-game facts (executable, renderer, preferred recipes).
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from akeno.paths import resource_dir

ROLES = {
    "reshade": "ReShade",
    "feeder": "DLSS5-Feeder",
    "motion": "Motion vectors",
    "consumer_optiscaler": "Neural Rendering consumer",
    "consumer_renodx": "Neural Rendering consumer",
    "sr_runtime": "DLSS Super Resolution runtime",
    "nr_runtime": "DLSS 5 Neural Rendering model",
}
SOURCES = ("download", "bundled", "reshade_setup", "local")
CHANNELS = ("stable", "prerelease", "community", "local", "unreviewed")
GENERATIONS = ("RTX 20", "RTX 30", "RTX 40", "RTX 50")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


@dataclass(frozen=True)
class ComponentVersion:
    component: str
    version: str
    source: str = "download"
    label: str = ""
    channel: str = "stable"
    url: str | None = None
    sha256: str | None = None
    size: int | None = None
    tag: str | None = None
    asset: str | None = None
    root: str = ""
    files: dict[str, str | None] = field(default_factory=dict)
    published: str | None = None
    gpus: tuple[str, ...] = ()
    recommended_for: tuple[str, ...] = ()
    recommended: bool = False
    signed: str = "none"                 # nvidia | modified | none
    settings: tuple[str, ...] = ()
    config_version: int | None = None
    notes: tuple[str, ...] = ()
    source_commit: str | None = None
    provider_id: int | None = None
    technique: str | None = None
    shader: str | None = None
    bundle_role: str | None = None
    reviewed: bool = True
    local_path: str | None = None

    @property
    def key(self) -> str:
        """Stable selection key; the version alone is ambiguous when one version has several sources."""
        if self.source == "local":
            return f"{self.component}:local:{(self.sha256 or '')[:16]}"
        if self.source == "download":
            return f"{self.component}:{self.version}" + ("" if self.reviewed else ":unreviewed")
        return f"{self.component}:{self.version}:{self.source}"

    @property
    def display(self) -> str:
        return self.label or self.version

    def supports_gpu(self, generation: str | None) -> bool | None:
        """True/False when the version lists GPU generations, None when it does not restrict them."""
        if not self.gpus:
            return None
        if generation is None:
            return None
        return generation in self.gpus


@dataclass(frozen=True)
class Component:
    id: str
    role: str
    name: str
    repository: str | None
    license: str
    homepage: str
    description: str
    features: frozenset[str]
    versions: tuple[ComponentVersion, ...]
    release: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    target: str                          # game | fivem
    apis: tuple[str, ...]
    architectures: tuple[str, ...]
    needs_upscaler: str | None           # None | any | dlss
    roles: dict[str, tuple[str, ...]]    # role -> allowed component ids, first is the default
    presets: str
    summary: str
    details: tuple[str, ...] = ()

    @property
    def consumer_role(self) -> str:
        return "consumer_renodx" if "consumer_renodx" in self.roles else "consumer_optiscaler"

    @property
    def uses_reshade(self) -> bool:
        return "reshade" in self.roles

    @property
    def uses_feeder(self) -> bool:
        return "feeder" in self.roles


@dataclass(frozen=True)
class GameProfile:
    id: str
    name: str
    steam_app_ids: tuple[str, ...] = ()
    executables: tuple[str, ...] = ()
    launcher: str | None = None
    api: str | None = None
    upscalers: tuple[str, ...] = ()
    recipes: tuple[str, ...] = ()
    renodx: dict[str, str] = field(default_factory=dict)
    optiscaler: dict[str, str] = field(default_factory=dict)
    feeder: dict[str, str] = field(default_factory=dict)
    reshade_definitions: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def matches(self, game) -> bool:
        if self.launcher:
            return game.launcher == self.launcher
        if not game.exe_path:
            return False
        exe = PurePosixPath(str(game.exe_path).replace("\\", "/"))
        try:
            rel = PurePosixPath(Path(game.exe_path).relative_to(Path(game.install_dir)).as_posix())
        except (TypeError, ValueError):
            rel = PurePosixPath(exe.name)
        rel_cf = rel.as_posix().casefold()
        for wanted in self.executables:
            w = wanted.casefold()
            # Steam/GOG/Epic copies share the layout below the install root; a bare name must equal the file.
            if rel_cf == w or rel_cf.endswith("/" + w):
                return True
        return False


def _version_from(data: dict[str, Any], component: str) -> ComponentVersion:
    known = set(ComponentVersion.__dataclass_fields__)
    kw = {k: v for k, v in data.items() if k in known}
    for key in ("gpus", "recommended_for", "settings", "notes"):
        if key in kw:
            kw[key] = tuple(kw[key])
    if "asset_label" in data and not kw.get("label"):
        kw["label"] = data["asset_label"]
    return ComponentVersion(component=component, **kw)


def _check_asset_url(repo: str, url: str, tag: str | None = None, asset: str | None = None) -> None:
    parts = urllib.parse.urlsplit(url)
    prefix = f"/{repo}/releases/download/"
    if parts.scheme != "https" or parts.netloc != "github.com" or parts.query or parts.fragment \
            or not parts.path.startswith(prefix) or ".." in urllib.parse.unquote(parts.path).split("/"):
        raise ValueError(f"Release asset outside its original repository: {url}")
    # the URL must be exactly the release's own asset link, so a renamed pin can never point at nothing
    if tag and asset and urllib.parse.unquote(parts.path) != f"{prefix}{tag}/{asset}":
        raise ValueError(f"Release asset URL does not match its tag and asset name: {url}")


def _safe_member(name: str) -> str:
    from akeno.security.archive import normalize_member
    norm = normalize_member(name)
    if norm != name:
        raise ValueError(f"Unsafe or non-normalised member path: {name}")
    return norm


class StackCatalog:
    def __init__(self, components: list[Component], recipes: list[Recipe], games: list[GameProfile],
                 version: str = "", reviewed_at: str = ""):
        self.catalog_version = version
        self.reviewed_at = reviewed_at
        self.components = {c.id: c for c in components}
        self.recipes = {r.id: r for r in recipes}
        self.games = list(games)
        if len(self.components) != len(components) or len(self.recipes) != len(recipes):
            raise ValueError("Duplicate component or recipe id.")
        self._validate()

    # -- loading -------------------------------------------------------------------------------
    @classmethod
    def load(cls, folder: str | Path | None = None) -> "StackCatalog":
        folder = Path(folder) if folder else resource_dir() / "stack"
        comp = json.loads((folder / "components.json").read_text(encoding="utf-8"))
        rec = json.loads((folder / "recipes.json").read_text(encoding="utf-8"))
        gam = json.loads((folder / "games.json").read_text(encoding="utf-8"))
        if comp.get("schema") != 3 or rec.get("schema") != 3 or gam.get("schema") != 3:
            raise ValueError("Stack catalog schema 3 is required.")
        components = []
        for c in comp["components"]:
            versions = tuple(_version_from(v, c["id"]) for v in c["versions"])
            components.append(Component(c["id"], c["role"], c["name"], c.get("repository"), c.get("license", ""),
                                        c.get("homepage", ""), c.get("description", ""),
                                        frozenset(c.get("features", [])), versions, dict(c.get("release") or {})))
        recipes = [Recipe(r["id"], r["name"], r["target"], tuple(r["apis"]), tuple(r["architectures"]),
                          r.get("needs_upscaler"), {k: tuple(v) for k, v in r["roles"].items()}, r["presets"],
                          r.get("summary", ""), tuple(r.get("details", []))) for r in rec["recipes"]]
        games = []
        known = set(GameProfile.__dataclass_fields__)
        for g in gam["games"]:
            kw = {k: v for k, v in g.items() if k in known}
            for key in ("steam_app_ids", "executables", "upscalers", "recipes", "notes"):
                if key in kw:
                    kw[key] = tuple(kw[key])
            games.append(GameProfile(**kw))
        return cls(components, recipes, games, comp.get("version", ""), comp.get("reviewed_at", ""))

    # -- validation ----------------------------------------------------------------------------
    def _validate(self) -> None:
        for c in self.components.values():
            if c.role not in ROLES:
                raise ValueError(f"Unknown role {c.role!r} for {c.id}.")
            if c.repository and not _REPO.fullmatch(c.repository):
                raise ValueError(f"Invalid repository for {c.id}.")
            keys = set()
            for v in c.versions:
                if v.source not in SOURCES or v.channel not in CHANNELS:
                    raise ValueError(f"{c.id} {v.version}: invalid source or channel.")
                if v.key in keys:
                    raise ValueError(f"{c.id}: duplicate version {v.key}.")
                keys.add(v.key)
                if v.source == "download":
                    if not c.repository or not v.url or not v.sha256 or not _SHA.fullmatch(v.sha256):
                        raise ValueError(f"{c.id} {v.version}: download needs a repository, URL and SHA-256.")
                    _check_asset_url(c.repository, v.url, v.tag, v.asset)
                    if not v.files or any(not s or not _SHA.fullmatch(s) for s in v.files.values()):
                        raise ValueError(f"{c.id} {v.version}: every extracted file needs its SHA-256.")
                    for name in v.files:
                        _safe_member(name)
                if any(g not in GENERATIONS for g in (*v.gpus, *v.recommended_for)):
                    raise ValueError(f"{c.id} {v.version}: unknown GPU generation.")
                if v.signed not in ("nvidia", "modified", "none"):
                    raise ValueError(f"{c.id} {v.version}: invalid signature state.")
        for r in self.recipes.values():
            if r.target not in ("game", "fivem"):
                raise ValueError(f"{r.id}: invalid target.")
            if r.needs_upscaler not in (None, "any", "dlss"):
                raise ValueError(f"{r.id}: invalid upscaler requirement.")
            consumers = [role for role in r.roles if role.startswith("consumer_")]
            if len(consumers) != 1:
                raise ValueError(f"{r.id}: exactly one neural consumer is required.")
            if "nr_runtime" not in r.roles:
                raise ValueError(f"{r.id}: the Neural Rendering model is required.")
            for role, ids in r.roles.items():
                if role not in ROLES or not ids:
                    raise ValueError(f"{r.id}: invalid role {role}.")
                for cid in ids:
                    if cid not in self.components or self.components[cid].role != role:
                        raise ValueError(f"{r.id}: {cid} cannot fill {role}.")
            if r.uses_feeder and not {"reshade", "motion", "sr_runtime"} <= set(r.roles):
                raise ValueError(f"{r.id}: the Feeder needs ReShade, a motion provider and the SR runtime.")
        for g in self.games:
            for rid in g.recipes:
                if rid not in self.recipes:
                    raise ValueError(f"Game profile {g.id} names unknown recipe {rid}.")

    # -- queries -------------------------------------------------------------------------------
    def component(self, cid: str) -> Component:
        return self.components[cid]

    def recipe(self, rid: str) -> Recipe:
        return self.recipes[rid]

    def version(self, key: str, extra: list[ComponentVersion] = ()) -> ComponentVersion | None:
        for c in self.components.values():
            for v in c.versions:
                if v.key == key:
                    return v
        return next((v for v in extra if v.key == key), None)

    def version_by_hash(self, cid: str, file_name: str, sha256: str) -> ComponentVersion | None:
        """The reviewed version whose extracted ``file_name`` has this SHA-256 (identifies local copies)."""
        c = self.components.get(cid)
        if not c:
            return None
        for v in c.versions:
            if v.files.get(file_name) == sha256:
                return v
        return None

    def profile_for(self, game) -> GameProfile | None:
        if game is None:
            return None
        for p in self.games:
            if p.launcher and p.matches(game):
                return p
        ids = str(game.launcher_id or "")
        exact = [p for p in self.games if not p.launcher and p.matches(game)]
        # Prefer the profile whose store id also matches (two profiles can share a bare exe name).
        for p in exact:
            if game.launcher == "steam" and ids in p.steam_app_ids:
                return p
        return exact[0] if exact else None


def with_label(v: ComponentVersion, label: str) -> ComponentVersion:
    return replace(v, label=label)
