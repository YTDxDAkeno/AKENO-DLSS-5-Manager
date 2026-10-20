import json

import pytest

from akeno.games import launchers, vdf
from akeno.games.launchers import ManualGameError
from helpers import make_pe


def _steam(tmp_path):
    root = tmp_path / "Steam"
    lib2 = tmp_path / "SteamLibrary"
    (root / "steamapps" / "common" / "Cyberpunk 2077" / "bin" / "x64").mkdir(parents=True)
    (lib2 / "steamapps" / "common" / "Control").mkdir(parents=True)
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n "0"\n {\n  "path" "%s"\n }\n "1"\n {\n  "path" "%s"\n  "apps" { "870780" "1" }\n }\n}\n'
        % (str(root).replace("\\", "\\\\"), str(lib2).replace("\\", "\\\\")))
    (root / "steamapps" / "appmanifest_1091500.acf").write_text(
        '"AppState"\n{\n "appid" "1091500"\n "name" "Cyberpunk 2077"\n "installdir" "Cyberpunk 2077"\n}\n')
    (lib2 / "steamapps" / "appmanifest_870780.acf").write_text(
        '"AppState" { "appid" "870780" "name" "Control" "installdir" "Control" }')
    (root / "steamapps" / "appmanifest_228980.acf").write_text(
        '"AppState" { "appid" "228980" "name" "Steamworks Common Redistributables" "installdir" "Steamworks Shared" }')
    (root / "steamapps" / "common" / "Steamworks Shared").mkdir()
    (root / "steamapps" / "appmanifest_999.acf").write_text('"AppState" { "appid" "999" "name" "Gone" "installdir" "Gone" }')
    (root / "steamapps" / "appmanifest_bad.acf").write_text('"AppState" { "appid" ')
    return root


def test_vdf_parser_handles_escapes_and_comments():
    d = vdf.loads('// c\n"a" { "b" "x\\\\y" "c" { "d" "1" } }')
    assert d["a"]["b"] == "x\\y" and d["a"]["c"]["d"] == "1"
    with pytest.raises(vdf.VdfError):
        vdf.loads('"a" {')


def test_steam_multiple_libraries_and_filters(tmp_path):
    games = launchers.scan_steam(str(_steam(tmp_path)))
    ids = sorted(g.id for g in games)
    assert ids == ["steam:1091500", "steam:870780"]


def test_epic_manifests(tmp_path):
    d = tmp_path / "Manifests"
    d.mkdir()
    game = tmp_path / "Games" / "Alan"
    game.mkdir(parents=True)
    (d / "a.item").write_text(json.dumps({"DisplayName": "Alan Wake 2", "InstallLocation": str(game),
                                          "LaunchExecutable": "AlanWake2.exe", "AppName": "Dill",
                                          "AppCategories": ["public", "games", "applications"]}))
    (d / "b.item").write_text(json.dumps({"DisplayName": "Plugin", "InstallLocation": str(game),
                                          "AppCategories": ["plugins"]}))
    (d / "c.item").write_text("{not json")
    games = launchers.scan_epic(str(d))
    assert [g.name for g in games] == ["Alan Wake 2"]
    assert games[0].exe_hint.endswith("AlanWake2.exe")


def test_gog_ubisoft_entries(tmp_path):
    p = tmp_path / "gog"
    p.mkdir()
    games = launchers.scan_gog([("1207658924", {"path": str(p), "gameName": "The Witcher 3", "exe": "bin\\w3.exe"}),
                                ("dlc", {"path": str(p), "gameName": "DLC", "dependsOn": "1207658924"}),
                                ("missing", {"path": str(tmp_path / "nope")})])
    assert [g.id for g in games] == ["gog:1207658924"]
    u = launchers.scan_ubisoft([("635", {"InstallDir": str(p) + "/"})])
    assert u[0].launcher == "ubisoft"


def test_ea_and_xbox(tmp_path):
    ea = tmp_path / "EA" / "Mass Effect"
    (ea / "__Installer").mkdir(parents=True)
    (ea / "__Installer" / "installerdata.xml").write_text(
        "<DiPManifest><gameTitles><gameTitle locale='en_US'>Mass Effect LE</gameTitle></gameTitles>"
        "<contentIDs><contentID>12345</contentID></contentIDs></DiPManifest>")
    g = launchers.scan_ea([str(ea)])[0]
    assert g.name == "Mass Effect LE" and g.id == "ea:12345"
    root = tmp_path / "XboxGames"
    content = root / "Forza" / "Content"
    content.mkdir(parents=True)
    (content / "MicrosoftGame.config").write_text(
        '<Game><ShellVisuals DefaultDisplayName="Forza Horizon"/><ExecutableList>'
        '<Executable Name="forza.exe" Id="Game"/></ExecutableList></Game>')
    x = launchers.scan_xbox([str(root)])[0]
    assert x.name == "Forza Horizon" and x.exe_hint.endswith("forza.exe")


def test_manual_add_valid_and_invalid(tmp_path):
    gd = tmp_path / "MyGame"
    make_pe(gd / "bin" / "game.exe", ["d3d12.dll"])
    g = launchers.manual_game(str(gd))
    assert g.launcher == "manual" and g.id.startswith("manual:")
    g2 = launchers.manual_game(str(gd / "bin" / "game.exe"))
    assert g2.exe_hint.endswith("game.exe")
    with pytest.raises(ManualGameError):
        launchers.manual_game(str(tmp_path / "missing"))
    empty = tmp_path / "Empty"
    empty.mkdir()
    (empty / "readme.txt").write_text("x")
    with pytest.raises(ManualGameError, match="No executable"):
        launchers.manual_game(str(empty))
    with pytest.raises(ManualGameError, match="not a Windows executable"):
        launchers.manual_game(str(empty / "readme.txt"))
    with pytest.raises(ManualGameError):
        launchers.manual_game("")
