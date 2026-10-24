"""Community packages (``.akenopack``): one JSON document, data only.

    {"format": "akeno-pack", "version": 1, "kind": "dlss_preset", "name": "...", "author": "...",
     "description": "...", "license": "CC0-1.0", "created": "...",
     "game": {"name": "Cyberpunk 2077", "ids": ["steam:1091500"]},
     "content": {...}}

Kinds and what they may contain (anything else is refused):

* ``dlss_preset``     an installation method of the stack catalog, a preset and custom values. Using it only
                      selects them for a game; installing is the usual previewed step on the game's page.
* ``booster_profile`` an FPS Booster profile and its options. Using it pre-selects the profile; Windows
                      settings change only when the user applies it on the FPS Booster page.
* ``widget_layout``   desktop widgets (positions, sizes, options; never notes or to-dos).

A package never contains files, paths to run, scripts, URLs to open or anything executable; the validation
rebuilds it from known fields only. Packages from the community index are checked against the index's SHA-256
and refused when the index blocks them.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from akeno.paths import sub

FORMAT = "akeno-pack"
VERSION = 1
SUFFIX = ".akenopack"
MAX_BYTES = 256 * 1024
KINDS = {"dlss_preset": "DLSS 5 preset", "booster_profile": "FPS Booster profile", "widget_layout": "Widget layout"}
TEXT = {"name": 60, "author": 60, "description": 1000, "license": 60, "created": 30}
GAME_ID = re.compile(r"^(steam|epic|gog|ubisoft|ea|xbox|manual):[A-Za-z0-9._-]{1,80}$")
SETTING_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,79}$")
BOOST_VALUES = {"gpu_preference": {"high", "power_saving"}, "capture": {"off", "on"},
                "fullscreen_optimizations": {"off", "on"}, "power_plan": {"high", "balanced", "saver"},
                "priority": {"above_normal", "high"}}


class PackError(ValueError):
    pass


@dataclass
class Pack:
    data: dict
    sha256: str
    source: str = "file"            # file | index
    reviewed: bool = False
    path: Path | None = None

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def kind(self) -> str:
        return self.data["kind"]

    @property
    def name(self) -> str:
        return self.data["name"]


def _text(raw: dict, key: str) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise PackError(f"{key} must be text")
    return value.strip()[:TEXT[key]]


def _dlss(content: dict) -> dict:
    from akeno.stack import configs
    from akeno.stack.catalog import StackCatalog
    recipe = content.get("recipe")
    if not isinstance(recipe, str) or recipe not in StackCatalog.load().recipes:
        raise PackError("unknown installation method")
    preset = content.get("preset", "balanced")
    if preset not in configs.PRESETS:
        raise PackError("unknown preset")
    custom = content.get("custom") or {}
    if not isinstance(custom, dict) or len(custom) > 64:
        raise PackError("custom values must be a small object")
    clean = {}
    for key, value in custom.items():
        if not isinstance(key, str) or not SETTING_KEY.match(key):
            raise PackError(f"refused setting name {key!r}")
        if isinstance(value, bool) or (isinstance(value, (int, float)) and abs(value) < 1e6):
            clean[key] = value
        elif isinstance(value, str) and len(value) <= 40 and re.fullmatch(r"[A-Za-z0-9_. -]*", value):
            clean[key] = value
        else:
            raise PackError(f"refused value for {key}")
    return {"recipe": recipe, "preset": preset, "custom": clean if preset == "custom" else None}


def _booster(content: dict) -> dict:
    from akeno.performance.profiles import BY_ID
    profile = content.get("profile")
    if profile not in BY_ID:
        raise PackError("unknown FPS Booster profile")
    options = content.get("options") or {}
    if not isinstance(options, dict):
        raise PackError("options must be an object")
    clean = {}
    for key, value in options.items():
        if key in BOOST_VALUES:
            if value not in BOOST_VALUES[key]:
                raise PackError(f"refused value for {key}")
            clean[key] = value
        elif key == "game_mode" and isinstance(value, bool):
            clean[key] = value
        elif key == "frame_limit" and (isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 500
                                       or isinstance(value, str) and re.fullmatch(r"refresh-\d{1,2}", value)):
            clean[key] = value
        elif key == "background" and isinstance(value, dict) and value.get("action") in ("lower", "pause"):
            # the list of programs is this PC's, never part of a package
            clean[key] = {"action": value["action"], **({"keep_streaming": True} if value.get("keep_streaming")
                                                        else {})}
        else:
            raise PackError(f"refused option {key}")
    return {"profile": profile, "options": clean if profile == "custom" else {}}


def _widgets(content: dict) -> dict:
    from akeno.desktop.widgets import clean_layout
    widgets = clean_layout(content.get("widgets"))
    for w in widgets:
        if w["type"] in ("notes", "todo", "shortcuts", "downloads"):
            w["options"] = {}
    if not widgets:
        raise PackError("the layout has no widgets")
    return {"widgets": widgets}


CLEANERS = {"dlss_preset": _dlss, "booster_profile": _booster, "widget_layout": _widgets}


def validate(raw) -> dict:
    """The package rebuilt from known fields, or PackError."""
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise PackError("not an AKENO community package")
    if not isinstance(raw.get("version"), int) or raw["version"] > VERSION:
        raise PackError("this package needs a newer AKENO")
    kind = raw.get("kind")
    if kind not in CLEANERS:
        raise PackError("unknown package kind")
    clean = {"format": FORMAT, "version": VERSION, "kind": kind, **{k: _text(raw, k) for k in TEXT}}
    if not clean["name"]:
        raise PackError("the package has no name")
    game = raw.get("game") or {}
    if not isinstance(game, dict):
        raise PackError("game must be an object")
    ids = [i for i in (game.get("ids") or []) if isinstance(i, str) and GAME_ID.match(i)][:10]
    name = game.get("name") if isinstance(game.get("name"), str) else ""
    clean["game"] = {"name": name.strip()[:80], "ids": ids}
    if kind != "widget_layout" and not (ids or clean["game"]["name"]):
        raise PackError("a game package names its game")
    content = raw.get("content")
    if not isinstance(content, dict):
        raise PackError("the package has no content")
    clean["content"] = CLEANERS[kind](content)
    clean["id"] = re.sub(r"[^a-z0-9]+", "-", clean["name"].lower()).strip("-")[:40] + "-" + \
        hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()[:8]
    return clean


def read(file: Path) -> tuple[dict, str]:
    data = Path(file).read_bytes()
    if len(data) > MAX_BYTES:
        raise PackError("the package is larger than 256 KB")
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PackError(f"not valid JSON: {exc}") from exc
    return validate(raw), hashlib.sha256(data).hexdigest()


def library_dir() -> Path:
    return sub("community") / "packages"


def install(file: Path, source: str = "file", reviewed: bool = False, blocked: set[str] | None = None,
            root: Path | None = None) -> Pack:
    clean, sha = read(file)
    if blocked and sha in blocked:
        raise PackError("the community index blocks this package")
    root = Path(root or library_dir())
    root.mkdir(parents=True, exist_ok=True)
    record = {"pack": clean, "sha256": sha, "source": source, "reviewed": bool(reviewed),
              "imported": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    target = root / f"{clean['id']}.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    return Pack(clean, sha, source, bool(reviewed), target)


def library(root: Path | None = None) -> list[Pack]:
    root = Path(root or library_dir())
    out = []
    for f in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            record = json.loads(f.read_text(encoding="utf-8"))
            out.append(Pack(validate(record["pack"]), record["sha256"], record.get("source", "file"),
                            bool(record.get("reviewed")), f))
        except (OSError, ValueError, KeyError):
            continue
    return out


def remove(pack: Pack, root: Path | None = None) -> None:
    """Delete an imported package (only a file of the package library)."""
    root = Path(root or library_dir()).resolve()
    if pack.path and pack.path.resolve().parent == root and pack.path.suffix == ".json":
        pack.path.unlink(missing_ok=True)


def export(kind: str, content: dict, out_file: Path, name: str, author: str = "", description: str = "",
           license: str = "CC0-1.0", game_name: str = "", game_ids: list[str] | None = None) -> Path:
    raw = {"format": FORMAT, "version": VERSION, "kind": kind, "name": name, "author": author,
           "description": description, "license": license,
           "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "game": {"name": game_name, "ids": [i for i in (game_ids or []) if GAME_ID.match(i)]},
           "content": content}
    clean = validate(raw)
    clean.pop("id")
    out_file = Path(out_file)
    if out_file.suffix.lower() != SUFFIX:
        out_file = out_file.with_suffix(SUFFIX)
    out_file.write_text(json.dumps(clean, indent=1, ensure_ascii=False), encoding="utf-8")
    return out_file


# -- using a package ----------------------------------------------------------------------------------------------
def matching_games(pack: Pack, games: list) -> list:
    """Library games the package is meant for: by launcher id, else by name."""
    ids = set(pack.data["game"]["ids"])
    exact = [g for g in games if g.id in ids]
    if exact:
        return exact
    name = pack.data["game"]["name"].casefold()
    return [g for g in games if name and g.name.casefold() == name]


def apply(pack: Pack, services, game_id: str | None = None, desktop_path: Path | None = None) -> str:
    """Use the package. Returns what happened, for the status line."""
    content = pack.data["content"]
    if pack.kind == "dlss_preset":
        if not game_id:
            raise PackError("choose a game")
        services.stack.choose_recipe(game_id, content["recipe"])
        services.stack.choose_preset(game_id, content["preset"], content.get("custom"))
        return "dlss"
    if pack.kind == "booster_profile":
        if not game_id:
            raise PackError("choose a game")
        overrides = services.db.game_overrides(game_id)
        overrides["boost_choice"] = {"profile": content["profile"], "options": content.get("options") or {},
                                     "from": pack.name}
        services.db.set_game_overrides(game_id, overrides)
        return "booster"
    from akeno.desktop import config as desktop_config
    cfg = desktop_config.load(desktop_path)
    cfg["widgets"] = json.loads(json.dumps(content["widgets"]))
    cfg["enabled"] = True
    desktop_config.save(cfg, desktop_path)
    return "widgets"
