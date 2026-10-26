"""Universal Mod Scanner: Minecraft metadata (Fabric, Quilt, Forge, NeoForge, mcmod.info), instances, conflicts,
damaged archives and zip bombs, game folder adapters, FiveM, AKENO-managed vs external, and that scanning changes
nothing on disk."""

import hashlib
import json
import os
import zipfile
from types import SimpleNamespace

import pytest

from akeno.system.mods import archive, conflicts, games, minecraft, versions
from akeno.system.mods.model import SOURCE_AKENO, SOURCE_EXTERNAL, SOURCE_SERVER, ModGroup
from helpers import make_pe


def jar(path, files: dict[str, str | bytes]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def fabric(mid, version="1.0.0", depends=None, **kw):
    return {"fabric.mod.json": json.dumps({"schemaVersion": 1, "id": mid, "version": version, "name": mid.title(),
                                           "depends": depends or {}, **kw})}


def forge_toml(mid, version="1.0.0", deps="", neo=False):
    return {("META-INF/neoforge.mods.toml" if neo else "META-INF/mods.toml"):
            f'modLoader="javafml"\nloaderVersion="[47,)"\n[[mods]]\nmodId="{mid}"\nversion="{version}"\n'
            f'displayName="{mid.title()}"\n{deps}'}


def test_fabric_metadata_with_nested_jar(tmp_path):
    inner = tmp_path / "inner.jar"
    jar(inner, fabric("fabric-api-base", "0.4.31"))
    files = fabric("sodium", "0.5.8", {"minecraft": ">=1.20 <1.21", "fabricloader": ">=0.15"},
                   jars=[{"file": "META-INF/jars/inner.jar"}], provides=["rubidium-compat"])
    files["META-INF/jars/inner.jar"] = inner.read_bytes()
    item = minecraft.read_jar(str(jar(tmp_path / "mods" / "sodium.jar", files)))
    assert (item.mod_id, item.version, item.loader) == ("sodium", "0.5.8", "fabric")
    assert item.minecraft == ">=1.20 <1.21" and "fabric-api-base" in item.nested and "rubidium-compat" in item.provides
    assert item.detection == "fabric.mod.json" and not item.error


def test_fabric_json_with_comments_and_placeholder_version(tmp_path):
    raw = '{\n "schemaVersion": 1,\n // a comment\n "id": "lenient",\n "version": "${version}",\n}'
    item = minecraft.read_jar(str(jar(tmp_path / "l.jar", {"fabric.mod.json": raw})))
    assert item.mod_id == "lenient" and item.version is None


def test_quilt_forge_neoforge_mcmod(tmp_path):
    q = minecraft.read_jar(str(jar(tmp_path / "q.jar", {"quilt.mod.json": json.dumps(
        {"quilt_loader": {"id": "qmod", "version": "2.0", "metadata": {"name": "Q Mod"},
                          "depends": ["quilt_base", {"id": "minecraft", "versions": ">=1.20"}]}})})))
    assert (q.mod_id, q.loader, q.name, q.minecraft) == ("qmod", "quilt", "Q Mod", ">=1.20")
    deps = '[[dependencies.jei]]\nmodId="minecraft"\nmandatory=true\nversionRange="[1.20.1,1.21)"\n'
    f = minecraft.read_jar(str(jar(tmp_path / "jei.jar", {**forge_toml("jei", "${file.jarVersion}", deps),
                                                         "META-INF/MANIFEST.MF": "Implementation-Version: 15.2.0\n"})))
    assert (f.mod_id, f.loader, f.version, f.minecraft) == ("jei", "forge", "15.2.0", "[1.20.1,1.21)")
    n = minecraft.read_jar(str(jar(tmp_path / "n.jar", forge_toml("neomod", "1.0", neo=True))))
    assert n.loader == "neoforge" and n.detection == "META-INF/neoforge.mods.toml"
    old = minecraft.read_jar(str(jar(tmp_path / "old.jar", {"mcmod.info": json.dumps(
        [{"modid": "oldmod", "name": "Old", "version": "1.2", "mcversion": "1.12.2", "requiredMods": ["baubles"]}])})))
    assert (old.mod_id, old.loader, old.minecraft) == ("oldmod", "forge", "1.12.2")
    assert old.depends[0].id == "baubles"


def test_missing_metadata_damaged_archive_and_disabled(tmp_path):
    none = minecraft.read_jar(str(jar(tmp_path / "plain.jar", {"a.txt": "x"})))
    assert none.kind == "unknown" and "metadata" in none.error
    bad = tmp_path / "broken.jar"
    bad.write_bytes(b"PK\x03\x04 this is not a zip")
    item = minecraft.read_jar(str(bad))
    assert item.kind == "unknown" and item.error
    dis = minecraft.read_jar(str(jar(tmp_path / "x.jar.disabled", fabric("xmod"))))
    assert not dis.enabled and dis.mod_id == "xmod"


def test_zip_bomb_and_oversized_entries_are_refused(tmp_path, monkeypatch):
    bomb = jar(tmp_path / "bomb.jar", {"fabric.mod.json": json.dumps({"id": "b"}), "pad.bin": b"\0" * (6 * 2 ** 20)})
    item = minecraft.read_jar(str(bomb))
    assert item.error and "zip bomb" in item.error
    monkeypatch.setattr(archive, "MAX_ENTRY", 10)
    big = jar(tmp_path / "big.jar", {"fabric.mod.json": json.dumps({"id": "big", "name": "x" * 50})})
    assert "larger" in minecraft.read_jar(str(big)).error
    monkeypatch.setattr(archive, "MAX_ARCHIVE", 10)
    assert "larger" in minecraft.read_jar(str(big)).error


def _instance(tmp_path, name, loader, mc, lv, mods: dict):
    root = tmp_path / name
    for fn, files in mods.items():
        jar(root / "mods" / fn, files)
    return minecraft.Instance(name, str(root), "Test", mc, loader, lv)


def _ids(g):
    return sorted(f.id for f in g.findings)


def test_conflicts_duplicates_missing_loader_and_versions(tmp_path):
    inst = _instance(tmp_path, "pack", "fabric", "1.20.1", "0.14.0", {
        "a1.jar": fabric("alpha", "1.0"), "a2.jar": fabric("alpha", "1.1"),
        "b.jar": fabric("beta", "1.0", {"gamma": ">=2.0"}),
        "c.jar": fabric("delta", "1.0", {"epsilon": "*"}), "e.jar": fabric("epsilon", "3.0"),
        "f.jar": forge_toml("forgeonly"),
        "g.jar": fabric("future", "1.0", {"minecraft": ">=1.21"}),
        "h.jar": fabric("loadernew", "1.0", {"fabricloader": ">=0.15"}),
        "i.jar": fabric("old_dep", "1.0", {"epsilon": "<2.0"}),
        "j.jar": fabric("hater", "1.0", breaks={"epsilon": "*"}),
    })
    g = minecraft.scan_instance(inst)
    ids = _ids(g)
    assert "mods.duplicate.alpha" in ids
    assert "mods.missing.beta.gamma" in ids and not any("mods.missing.delta" in i for i in ids)
    assert "mods.loader.forgeonly" in ids
    assert "mods.mcversion.future" in ids and "mods.loaderversion.loadernew" in ids
    assert "mods.depversion.old_dep.epsilon" in ids and "mods.incompatible.hater.epsilon" in ids


def test_no_invented_conflicts(tmp_path):
    inst = _instance(tmp_path, "clean", "quilt", "1.20.4", None, {
        "a.jar": fabric("api", "1.0", {"minecraft": "1.20.x"}),             # Fabric mods run on Quilt
        "b.jar": fabric("needsapi", "1.0", {"api": ">=1.0", "minecraft": "*", "fabricloader": "*"}),
        "c.jar": fabric("weird", "1.0", {"minecraft": "whatever-version-scheme"}),   # unparseable: not a conflict
    })
    g = minecraft.scan_instance(inst)
    assert g.findings == []


def test_neoforge_with_connector_accepts_fabric_mods(tmp_path):
    inst = _instance(tmp_path, "neo", "neoforge", "1.21", None, {
        "connector.jar": forge_toml("connector", neo=True), "fab.jar": fabric("fabmod")})
    assert not any(i.startswith("mods.loader") for i in _ids(minecraft.scan_instance(inst)))


def test_instance_discovery_official_prism_curseforge(tmp_path):
    appdata, home = tmp_path / "Roaming", tmp_path / "User"
    mc = appdata / ".minecraft"
    custom = tmp_path / "Profiles" / "Fabric 1.20.1"
    jar(mc / "mods" / "a.jar", fabric("a"))
    jar(custom / "mods" / "b.jar", fabric("b"))
    (mc / "launcher_profiles.json").write_text(json.dumps({"profiles": {
        "x": {"name": "Fabric", "lastVersionId": "fabric-loader-0.15.11-1.20.1", "gameDir": str(custom)},
        "y": {"name": "Latest", "lastVersionId": "1.20.1"}}}))
    prism = appdata / "PrismLauncher" / "instances" / "Pack"
    jar(prism / ".minecraft" / "mods" / "c.jar", forge_toml("c"))
    (prism / "instance.cfg").write_text("name=My Pack\n")
    (prism / "mmc-pack.json").write_text(json.dumps({"components": [
        {"uid": "net.minecraft", "version": "1.20.1"}, {"uid": "net.minecraftforge", "version": "47.2.0"}]}))
    cf = home / "curseforge" / "minecraft" / "Instances" / "CF Pack"
    jar(cf / "mods" / "d.jar", forge_toml("d", neo=True))
    (cf / "minecraftinstance.json").write_text(json.dumps({"name": "CF Pack", "gameVersion": "1.21",
                                                            "baseModLoader": {"name": "neoforge-21.0.167"}}))
    found = {i.name: i for i in minecraft.discover({"APPDATA": str(appdata), "USERPROFILE": str(home)})}
    assert found["Fabric"].loader == "fabric" and found["Fabric"].mc_version == "1.20.1"
    assert found["Latest"].root == str(mc)
    assert (found["My Pack"].loader, found["My Pack"].loader_version) == ("forge", "47.2.0")
    assert (found["CF Pack"].loader, found["CF Pack"].mc_version) == ("neoforge", "1.21")


def test_version_ids():
    assert minecraft.parse_version_id("fabric-loader-0.15.11-1.20.1") == ("1.20.1", "fabric", "0.15.11")
    assert minecraft.parse_version_id("1.20.1-forge-47.2.0") == ("1.20.1", "forge", "47.2.0")
    assert minecraft.parse_version_id("neoforge-20.4.237") == ("1.20.4", "neoforge", "20.4.237")
    assert minecraft.parse_version_id("neoforge-21.0.167") == ("1.21", "neoforge", "21.0.167")
    assert minecraft.parse_version_id("1.21") == ("1.21", "vanilla", None)
    assert minecraft.parse_version_id("weird") == (None, None, None)


# -- games and FiveM ------------------------------------------------------------------------------------------------
def test_game_adapters_and_akeno_vs_external(tmp_path):
    root = tmp_path / "Game"
    exe = root / "Bin"
    make_pe(exe / "game.exe", ["d3d11.dll"])
    make_pe(exe / "dxgi.dll", dll=True, strings={"ProductName": "ReShade", "FileDescription": "ReShade"})
    make_pe(exe / "winmm.dll", dll=True, strings={"ProductName": "OptiScaler"})
    (exe / "nvngx_dlss.dll").write_bytes(b"MZ")                        # the game's own DLSS: not a mod
    (exe / "plugin.asi").write_bytes(b"MZ")
    (exe / "scripts").mkdir()
    (exe / "scripts" / "fix.asi").write_bytes(b"MZ")
    make_pe(root / "BepInEx" / "core" / "BepInEx.dll", dll=True, version=(5, 4, 22, 0))
    make_pe(root / "BepInEx" / "plugins" / "Cool" / "cool.dll", dll=True, version=(1, 2, 0, 0),
            strings={"ProductName": "Cool Mod"})
    (root / "Proj" / "Content" / "Paks" / "~mods").mkdir(parents=True)
    (root / "Proj" / "Content" / "Paks" / "~mods" / "skin_P.pak").write_bytes(b"x")
    ue = root / "Proj" / "Binaries" / "Win64"
    (ue / "Mods" / "LuaMod").mkdir(parents=True)
    (ue / "Mods" / "LuaMod" / "enabled.txt").write_text("")
    (ue / "UE4SS.dll").write_bytes(b"MZ")
    game = SimpleNamespace(id="g1", name="Game", install_dir=str(root), target_dir=str(exe), launcher="steam")
    g = games.scan_game(game, {"winmm.dll"})
    by = {os.path.basename(m.path): m for m in g.mods}
    assert by["winmm.dll"].source == SOURCE_AKENO and by["dxgi.dll"].source == SOURCE_EXTERNAL
    assert by["dxgi.dll"].kind == "reshade" and by["winmm.dll"].kind == "optiscaler"
    assert "nvngx_dlss.dll" not in by
    assert by["fix.asi"].kind == "asi_plugin" and by["plugin.asi"].kind == "asi_plugin"
    assert by["cool.dll"].name == "Cool Mod" and by["cool.dll"].loader == "BepInEx"
    assert by["skin_P.pak"].kind == "ue_pak" and by["LuaMod"].kind == "ue4ss_mod" and by["LuaMod"].enabled


def test_two_reshade_dlls_and_unknown_proxy(tmp_path):
    exe = tmp_path / "G"
    make_pe(exe / "dxgi.dll", dll=True, strings={"ProductName": "ReShade"})
    make_pe(exe / "d3d11.dll", dll=True, strings={"ProductName": "ReShade"})
    make_pe(exe / "version.dll", dll=True, strings={"ProductName": "Mystery"})
    g = games.scan_game(SimpleNamespace(id="x", name="X", install_dir=str(exe), target_dir=str(exe), launcher="m"),
                        set())
    ids = [f.id for f in g.findings]
    assert any(i.startswith("mods.reshade_twice") for i in ids)
    unknown = next(f for f in g.findings if f.id.startswith("mods.unknown_proxy"))
    assert unknown.links[0].module == "security"


def test_fivem_client_external_akeno_and_server_cache(tmp_path):
    from akeno.fivem.discovery import inspect_installation
    root, gta = tmp_path / "FiveM", tmp_path / "GTA V"
    app = root / "FiveM.app"
    make_pe(root / "FiveM.exe", ["kernel32.dll"])
    make_pe(gta / "GTA5.exe", ["d3d11.dll"])
    app.mkdir(parents=True, exist_ok=True)
    (app / "CitizenFX.ini").write_text(f"[Game]\nIVPath={gta}\n")
    make_pe(app / "plugins" / "d3d11.dll", dll=True, strings={"ProductName": "ReShade"})
    (app / "plugins" / "external.asi").write_bytes(b"MZ")
    (app / "mods").mkdir()
    (app / "mods" / "graphics.rpf").write_bytes(b"RPF7")
    (app / "citizen").mkdir()
    (app / "data" / "server-cache-priv" / "abc").mkdir(parents=True)
    (app / "data" / "server-cache-priv" / "abc" / "car.ytd").write_bytes(b"x" * 100)
    inst = inspect_installation(root)
    g = games.scan_fivem(inst, "fivem:1", {"d3d11.dll"})
    by = {os.path.basename(m.path): m for m in g.mods}
    assert by["d3d11.dll"].source == SOURCE_AKENO and by["external.asi"].source == SOURCE_EXTERNAL
    assert by["graphics.rpf"].kind == "rpf"
    server = next(m for m in g.mods if m.source == SOURCE_SERVER)
    assert server.extra["files"] == 1 and any("not mods installed" in n for n in g.notes)
    assert not any("citizen" in m.path for m in g.mods)


def test_scanning_changes_nothing(tmp_path):
    inst = _instance(tmp_path, "ro", "fabric", "1.20.1", None, {"a.jar": fabric("a"), "b.jar": fabric("b")})
    before = {p: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
              for p in (tmp_path / "ro").rglob("*") if p.is_file()}
    minecraft.scan_instance(inst)
    after = {p: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
             for p in (tmp_path / "ro").rglob("*") if p.is_file()}
    assert before == after and set(os.listdir(tmp_path / "ro")) == {"mods"}


def test_service_scans_library_and_minecraft_and_persists(tmp_path, akeno_home):
    from akeno.system.mods.service import ModScanner
    from akeno.system.store import SystemStore
    exe = tmp_path / "G"
    make_pe(exe / "dxgi.dll", dll=True, strings={"ProductName": "ReShade"})
    game = SimpleNamespace(id="g", name="G", install_dir=str(exe), target_dir=str(exe), launcher="steam")
    services = SimpleNamespace(library=SimpleNamespace(games=lambda: [game], get=lambda gid: game,
                                                       fivem_installations={}),
                               installer=SimpleNamespace(managed_files=lambda gid, d: set()))
    appdata = tmp_path / "Roaming"
    jar(appdata / ".minecraft" / "mods" / "a.jar", fabric("a"))
    store = SystemStore(tmp_path / "s.sqlite3")
    sc = ModScanner(services, store, env={"APPDATA": str(appdata), "USERPROFILE": str(tmp_path / "U")})
    groups = sc.scan()
    assert {g.platform for g in groups} == {"game", "minecraft"}
    again = ModScanner(services, store, env={"APPDATA": str(appdata)})
    assert [g.key for g in again.groups] == [g.key for g in groups]
    assert again.groups[0].mods[0].path == groups[0].mods[0].path
    assert sc.rescan("game:g").mods
    with pytest.raises(ValueError):
        sc.add_minecraft_folder(str(tmp_path))
    store.close()


def test_versions_unit():
    assert versions.fabric_matches(">=1.20 <1.21", "1.20.6") and not versions.fabric_matches("1.20.x", "1.21")
    assert versions.maven_matches("[1.20,1.21)", "1.20.1") and versions.maven_matches("[47,)", "47.1") is True
    assert versions.matches("bogus", "1.0", "semver") is None
