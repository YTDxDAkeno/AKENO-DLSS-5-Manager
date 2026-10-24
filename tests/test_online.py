"""ONLINE: Cloud Sync between two simulated PCs sharing one folder (merges, playtime per PC, conflicts, deletions,
encryption, refusals, quota) and community packages (validation, import, use, the reviewed index)."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from akeno.packages import index as cindex
from akeno.packages import packs
from akeno.sync import engine as sync
from akeno.sync.data import LocalData


class Secrets:
    def __init__(self):
        self.value = {}

    def load(self):
        return dict(self.value)

    def save(self, value):
        self.value = dict(value)


def add_game(db, game_id: str, name: str = "Speedway") -> None:
    db.execute("INSERT OR REPLACE INTO games(id, data, updated_at) VALUES(?,?,?)",
               (game_id, json.dumps({"id": game_id, "name": name}), "now"))


@pytest.fixture
def pcs(tmp_path):
    from akeno.core.db import Database
    from akeno.core.settings import Settings
    from akeno.performance.engine import BoostEngine
    folder = tmp_path / "OneDrive"
    folder.mkdir()
    made = []

    def pc(name: str):
        root = tmp_path / name
        root.mkdir()
        db = Database(root / "akeno.sqlite3")
        svc = SimpleNamespace(db=db, settings=Settings(db), boost=BoostEngine(root / "boost.json"))
        data = LocalData(svc, desktop_path=root / "desktop.json")
        eng = sync.SyncEngine(data, root / "sync.json", Secrets(), (f"dev-{name}", name))
        eng.configure(folder=str(folder), enabled=True)
        add_game(db, "steam:1091500", "Cyberpunk 2077")
        made.append(db)
        return SimpleNamespace(svc=svc, db=db, engine=eng, root=root)
    yield pc, folder
    for db in made:
        db.close()


def test_merge_rules():
    base = {"notes": "a", "category": "", "playtime": {"A": 10}, "last_played": "2026-01-01"}
    mine = {"notes": "a", "category": "RPG", "playtime": {"A": 20}, "last_played": "2026-01-03"}
    theirs = {"notes": "b", "category": "", "playtime": {"A": 10, "B": 5}, "last_played": "2026-01-02"}
    merged, conflicts = sync.merge(base, mine, theirs)
    assert conflicts == [] and merged["notes"] == "b" and merged["category"] == "RPG"
    assert merged["playtime"] == {"A": 20, "B": 5} and merged["last_played"] == "2026-01-03"
    _, conflicts = sync.merge(base, dict(mine, notes="x"), theirs)
    assert conflicts == ["notes"]


def test_two_pcs_merge_settings_games_playtime_desktop(pcs):
    pc, folder = pcs
    a, b = pc("A"), pc("B")
    a.svc.settings.set("language", "de")
    a.svc.settings.set("github_token", "ghp_secret_value")
    a.db.set_game_meta("steam:1091500", favorite=1, notes="Path tracing on", category="RPG")
    a.db.add_playtime("steam:1091500", 3600)
    from akeno.desktop import config as dcfg
    cfg = dcfg.load(a.root / "desktop.json")
    cfg["widgets"] = [{"id": "w1", "type": "clock", "options": {}}]
    cfg["pause"]["battery"] = False
    cfg["dock"]["items"] = [{"type": "app", "path": "C:/private/tool.exe"}]
    dcfg.save(cfg, a.root / "desktop.json")
    report = a.engine.sync()
    assert report.pushed >= 3 and report.conflicts == 0
    text = "".join(p.read_text() for p in (folder / sync.FOLDER / "items").glob("*.json"))
    assert "ghp_secret_value" not in text and "github_token" not in text, "tokens are never synced"
    assert "C:/private/tool.exe" not in text, "dock pins stay on their PC"
    b.db.add_playtime("steam:1091500", 1800)
    report = b.engine.sync()
    assert report.conflicts == 0
    assert b.svc.settings.get("language") == "de" and b.svc.settings.get("github_token") == ""
    meta = b.db.game_meta("steam:1091500")
    assert meta["favorite"] == 1 and meta["notes"] == "Path tracing on" and meta["playtime_s"] == 5400
    bcfg = dcfg.load(b.root / "desktop.json")
    assert [w["type"] for w in bcfg["widgets"]] == ["clock"] and bcfg["pause"]["battery"] is False
    a.engine.sync()
    assert a.db.game_meta("steam:1091500")["playtime_s"] == 5400
    for _ in range(3):                                   # repeated syncs never count playtime twice
        a.engine.sync()
        b.engine.sync()
    assert a.db.game_meta("steam:1091500")["playtime_s"] == 5400
    assert b.db.game_meta("steam:1091500")["playtime_s"] == 5400
    assert {d["name"] for d in a.engine.devices().values()} == {"A", "B"}


def test_conflict_is_decided_by_the_user(pcs):
    pc, _ = pcs
    a, b = pc("A"), pc("B")
    a.db.set_game_meta("steam:1091500", notes="start")
    a.engine.sync()
    b.engine.sync()
    a.db.set_game_meta("steam:1091500", notes="from A", category="Shooter")
    b.db.set_game_meta("steam:1091500", notes="from B", launch_args="-dx12")
    a.engine.sync()
    report = b.engine.sync()
    assert report.conflicts == 1
    conflict = b.engine.state["conflicts"]["game/steam:1091500"]
    assert conflict["fields"] == ["notes"]
    b.engine.resolve("game/steam:1091500", "remote")
    meta = b.db.game_meta("steam:1091500")
    assert meta["notes"] == "from A" and meta["category"] == "Shooter" and meta["launch_args"] == "-dx12"
    a.engine.sync()
    assert a.db.game_meta("steam:1091500")["launch_args"] == "-dx12"
    assert not b.engine.state["conflicts"]


def test_benchmarks_dlss_booster_and_deletion(pcs, monkeypatch):
    pc, _ = pcs
    a, b = pc("A"), pc("B")
    bench = a.db.add_benchmark("steam:1091500", "DLSS 5 Quality", {"stats": {"avg_fps": 97.5}, "csv_path": "C:/x.csv",
                                                                   "gpu": "RTX 5090"})
    overrides = a.db.game_overrides("steam:1091500")
    overrides["stack"] = {"recipe": "some-recipe", "preset": "quality", "custom": None, "versions": {"x": "local"}}
    a.db.set_game_overrides("steam:1091500", overrides)
    a.svc.boost.state["games"]["steam:1091500"] = {"profile": "max_fps", "options": {"priority": "high"}}
    add_game(a.db, "epic:alanwake2", "Alan Wake 2")
    o2 = a.db.game_overrides("epic:alanwake2")
    o2["stack"] = {"recipe": "r2", "preset": "balanced"}
    a.db.set_game_overrides("epic:alanwake2", o2)
    a.engine.sync()
    b.engine.sync()
    got = b.db.benchmarks()
    assert len(got) == 1 and got[0]["stats"]["avg_fps"] == 97.5 and "csv_path" not in json.dumps(got[0]["synced"])
    sel = b.db.game_overrides("steam:1091500")["stack"]
    assert sel["recipe"] == "some-recipe" and sel["preset"] == "quality" and "versions" not in sel
    assert b.db.game_overrides("steam:1091500")["boost_choice"]["profile"] == "max_fps"
    assert b.svc.boost.configured("steam:1091500") is None, "a suggestion only: nothing applied on B"
    assert "dlss/epic:alanwake2" not in b.engine.state["base"], "not in B's library yet: retried later"
    add_game(b.db, "epic:alanwake2", "Alan Wake 2")
    b.engine.sync()
    assert b.db.game_overrides("epic:alanwake2")["stack"]["recipe"] == "r2"
    a.db.delete_benchmark(bench)
    a.engine.sync()
    report = b.engine.sync()
    assert report.deleted == 1 and b.db.benchmarks() == []


def test_encrypted_folder_and_wrong_passphrase(pcs, tmp_path):
    pc, folder = pcs
    a = pc("A")
    a.engine.configure(folder=str(tmp_path / "enc"), passphrase="correct horse battery")
    (tmp_path / "enc").mkdir()
    a.db.set_game_meta("steam:1091500", notes="very private note")
    a.engine.sync()
    files = list((tmp_path / "enc" / sync.FOLDER / "items").glob("*.json"))
    assert files and all("very private note" not in f.read_text() and "steam:1091500" not in f.read_text()
                         for f in files)
    b = pc("B")
    b.engine.configure(folder=str(tmp_path / "enc"), passphrase="wrong")
    with pytest.raises(sync.SyncError, match="passphrase"):
        b.engine.sync()
    b.engine.configure(passphrase="correct horse battery")
    b.engine.sync()
    assert b.db.game_meta("steam:1091500")["notes"] == "very private note"


def test_guard_refuses_secrets_and_quota_stops(pcs):
    with pytest.raises(sync.SyncError):
        sync.guard({"settings": {"access_token": "x"}})
    with pytest.raises(sync.SyncError):
        sync.guard({"blob": b"MZ"})
    pc, _ = pcs
    a = pc("A")
    a.engine.configure(quota_mb=1)
    a.db.set_game_meta("steam:1091500", notes="n" * 150_000)
    for i in range(12):
        add_game(a.db, f"steam:{i}", f"G{i}")
        a.db.set_game_meta(f"steam:{i}", notes="x" * 150_000)
    report = a.engine.sync()
    assert any("quota" in s for s in report.skipped)
    assert a.engine.usage() <= 2 ** 20


# -- community packages ---------------------------------------------------------------------------------------------
def recipe_id() -> str:
    from akeno.stack.catalog import StackCatalog
    return next(iter(StackCatalog.load().recipes))


def raw_pack(**over) -> dict:
    raw = {"format": "akeno-pack", "version": 1, "kind": "dlss_preset", "name": "Cyberpunk path tracing",
           "author": "Tester", "license": "CC0-1.0", "game": {"name": "Cyberpunk 2077", "ids": ["steam:1091500"]},
           "content": {"recipe": recipe_id(), "preset": "quality"}}
    raw.update(over)
    return raw


@pytest.mark.parametrize("change", [
    {"format": "zip"}, {"kind": "script"}, {"version": 99}, {"name": ""},
    {"content": {"recipe": "rm -rf", "preset": "quality"}},
    {"content": {"recipe": "__R__", "preset": "evil"}},
    {"content": {"recipe": "__R__", "preset": "custom", "custom": {"x; del": 1}}},
    {"content": {"recipe": "__R__", "preset": "custom", "custom": {"Key": "$(calc.exe)"}}},
    {"kind": "booster_profile", "content": {"profile": "max_fps", "options": {"run": "cmd.exe"}}},
    {"kind": "booster_profile", "content": {"profile": "custom", "options": {"priority": "realtime"}}},
    {"game": {"name": "", "ids": []}},
])
def test_package_refusals(change):
    raw = raw_pack(**change)
    if isinstance(raw.get("content"), dict) and raw["content"].get("recipe") == "__R__":
        raw["content"]["recipe"] = recipe_id()
    with pytest.raises(packs.PackError):
        packs.validate(raw)


def test_package_cleaning_strips_personal_data():
    clean = packs.validate(raw_pack(kind="booster_profile", content={
        "profile": "custom", "options": {"priority": "high", "frame_limit": 141,
                                         "background": {"action": "pause", "names": ["Discord.exe", "secret.exe"]}}}))
    assert clean["content"]["options"]["background"] == {"action": "pause"}, "program lists stay on the PC"
    layout = packs.validate(raw_pack(kind="widget_layout", game={}, content={"widgets": [
        {"type": "notes", "options": {"text": "private"}}, {"type": "clock", "options": {"h24": True}}]}))
    assert layout["content"]["widgets"][0]["options"] == {} and layout["content"]["widgets"][1]["options"]


def test_package_import_use_and_blocklist(tmp_path, db, monkeypatch):
    file = tmp_path / "cp.akenopack"
    file.write_text(json.dumps(raw_pack()), encoding="utf-8")
    lib = tmp_path / "lib"
    sha = hashlib.sha256(file.read_bytes()).hexdigest()
    with pytest.raises(packs.PackError, match="blocks"):
        packs.install(file, blocked={sha}, root=lib)
    pack = packs.install(file, root=lib)
    assert [p.id for p in packs.library(lib)] == [pack.id] and not pack.reviewed
    games = [SimpleNamespace(id="steam:1091500", name="Cyberpunk 2077"), SimpleNamespace(id="gog:1", name="Other")]
    assert [g.id for g in packs.matching_games(pack, games)] == ["steam:1091500"]
    chosen = {}
    stack = SimpleNamespace(choose_recipe=lambda g, r: chosen.update(recipe=(g, r)),
                            choose_preset=lambda g, p, c: chosen.update(preset=(g, p, c)))
    add_game(db, "steam:1091500")
    assert packs.apply(pack, SimpleNamespace(stack=stack, db=db), "steam:1091500") == "dlss"
    assert chosen["recipe"] == ("steam:1091500", recipe_id()) and chosen["preset"][1] == "quality"
    boost = tmp_path / "b.akenopack"
    boost.write_text(json.dumps(raw_pack(kind="booster_profile", content={"profile": "low_latency"})))
    bp = packs.install(boost, root=lib)
    packs.apply(bp, SimpleNamespace(db=db), "steam:1091500")
    assert db.game_overrides("steam:1091500")["boost_choice"]["profile"] == "low_latency"
    out = packs.export("widget_layout", {"widgets": [{"type": "cpu"}]}, tmp_path / "w", "My widgets")
    assert out.suffix == ".akenopack" and packs.read(out)[0]["kind"] == "widget_layout"
    packs.remove(pack, root=lib)
    assert len(packs.library(lib)) == 1


def test_community_index_filters_and_verifies(tmp_path, monkeypatch):
    good = "a" * 64
    index = cindex.parse({"format": "akeno-community-index", "version": 1, "packages": [
        {"id": "ok", "name": "OK", "kind": "dlss_preset", "url": "https://raw.githubusercontent.com/o/r/main/p.akenopack",
         "sha256": good, "size": 1000},
        {"id": "http", "name": "x", "kind": "dlss_preset", "url": "http://raw.githubusercontent.com/x", "sha256": good,
         "size": 10},
        {"id": "host", "name": "x", "kind": "dlss_preset", "url": "https://evil.example/x", "sha256": good, "size": 10},
        {"id": "sha", "name": "x", "kind": "dlss_preset", "url": "https://github.com/x", "sha256": "zz", "size": 10},
        {"id": "big", "name": "x", "kind": "dlss_preset", "url": "https://github.com/x", "sha256": good,
         "size": 10 ** 9},
        {"id": "exe", "name": "x", "kind": "program", "url": "https://github.com/x", "sha256": good, "size": 10}],
        "blocked": ["b" * 64, "not-a-hash"]})
    assert [e.id for e in index.entries] == ["ok"] and index.blocked == {"b" * 64}
    payload = json.dumps(raw_pack()).encode()
    entry = cindex.Entry("cp", "CP", "dlss_preset", "https://github.com/o/r/releases/download/x/cp.akenopack",
                         hashlib.sha256(payload).hexdigest(), len(payload))
    monkeypatch.setattr(cindex, "_get", lambda url, limit, timeout=20: payload)
    path = cindex.download(entry, tmp_path)
    assert packs.read(path)[0]["name"] == "Cyberpunk path tracing"
    monkeypatch.setattr(cindex, "_get", lambda url, limit, timeout=20: payload + b" ")
    with pytest.raises(cindex.IndexError_, match="SHA-256"):
        cindex.download(entry, tmp_path)
    assert "issues/new" in cindex.report_url("o/r", "CP", "c" * 64)


def test_index_refuses_hosts_outside_github():
    with pytest.raises(cindex.IndexError_):
        cindex._get("https://evil.example/index.json", 100)
    with pytest.raises(cindex.IndexError_):
        cindex._get("http://raw.githubusercontent.com/o/r/main/index.json", 100)


# -- pages ----------------------------------------------------------------------------------------------------------
def test_online_pages(akeno_home, qtbot, tmp_path, monkeypatch):
    from akeno.core.db import Database
    from akeno.desktop import client
    from akeno.services import AppServices
    from akeno.ui.controller import Controller
    from akeno.ui.main_window import MainWindow
    monkeypatch.setattr(client.DesktopClient, "start", lambda self: True)
    svc = AppServices(db=Database(akeno_home / "suite.sqlite3"))
    win = MainWindow(Controller(svc))
    qtbot.addWidget(win)
    win.show()
    for key in ("community", "sync"):
        win.go(key)
        assert win.current_page() == key
    page = win.pages["community"]
    file = tmp_path / "layout.akenopack"
    packs.export("widget_layout", {"widgets": [{"type": "clock"}, {"type": "cpu"}]}, file, "Gaming widgets")
    pack = page.import_file(file)
    assert pack is not None and page.mine.count() == 1
    monkeypatch.setattr("akeno.ui.pages.online.confirm", lambda *a, **k: True)
    assert page.use(pack) == "widgets"
    from akeno.desktop import config as dcfg
    assert [w["type"] for w in dcfg.load()["widgets"]] == ["clock", "cpu"]
    sp = win.pages["sync"]
    folder = tmp_path / "cloud"
    folder.mkdir()
    sp.set_folder(str(folder))
    sp.chk.setChecked(True)
    win.sync.sync()
    sp.load()
    assert sp.engine.state["last_sync"] and (folder / sync.FOLDER / "akeno-sync.json").is_file()
    win.sessions.stop()
    from akeno.ui import workers
    workers.drain(10000)
