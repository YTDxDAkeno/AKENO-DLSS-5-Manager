"""Filesystem fixtures only: none of these tests launch FiveM or use an NVIDIA GPU."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from akeno.core.models import GraphicsApi
from akeno.fivem import discover, inspect_installation, Edition
from akeno.fivem.discovery import as_raw_game, classify_process
from helpers import make_pe


@pytest.fixture
def installation(tmp_path):
    root, gta = tmp_path / "FiveM", tmp_path / "GTA V"
    app = root / "FiveM.app"
    app.mkdir(parents=True)
    make_pe(root / "FiveM.exe", ["kernel32.dll"])
    make_pe(gta / "GTA5.exe", ["d3d11.dll"])
    (app / "CitizenFX.ini").write_text(f"[Game]\nIVPath={gta}\nSavedBuildNumber=3258\nUpdateChannel=production\n")
    return root, app, gta


def test_discovery_separates_client_and_base_game(installation, monkeypatch):
    root, app, gta = installation
    monkeypatch.setenv("LOCALAPPDATA", str(root.parent))
    found = discover([root, app, root / "FiveM.exe", gta], processes=[])
    assert len(found) == 1
    i = found[0]
    assert i.edition == Edition.LEGACY and i.game.api == GraphicsApi.D3D11
    assert i.gta_dir == gta and i.game.exe_path == str(root / "FiveM.exe")
    assert i.plugin_dir == app / "plugins" and i.saved_build == "3258"
    assert not i.errors and i.game.architecture == "x64"
    raw = as_raw_game(i)
    assert raw.launcher == "fivem" and raw.exe_hint == str(root / "FiveM.exe")
    assert inspect_installation(gta) is None


def test_portable_discovery_from_running_process(installation, monkeypatch):
    root, app, _ = installation
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    procs = [SimpleNamespace(name="FiveM_b3258_GTAProcess.exe", path=str(app / "FiveM_b3258_GTAProcess.exe"))]
    assert [i.root for i in discover(processes=procs)] == [root]


@pytest.mark.parametrize("name,kind", [
    ("FiveM.exe", "fivem"), ("FiveM_b3258_GTAProcess.exe", "fivem"),
    ("FiveM_GTAProcess.exe", "fivem"), ("GTA5.exe", "gta_v_session_unknown"),
    ("GTA5_Enhanced.exe", "gta_v_enhanced_session_unknown"), ("Other.exe", "unknown"),
])
def test_process_classification_never_guesses_gta_online(name, kind):
    assert classify_process(name) == kind


def test_enhanced_and_ambiguous_editions(installation):
    root, _, gta = installation
    make_pe(gta / "GTA5_Enhanced.exe", ["d3d12.dll"])
    assert inspect_installation(root).edition == Edition.UNKNOWN
    (gta / "GTA5.exe").unlink()
    assert inspect_installation(root).edition == Edition.ENHANCED


@pytest.mark.parametrize("config", ["[Game]\nIVPath=relative", "invalid", "[Game]\nIVPath=/missing", "[Game]\nIVPath=x\nIVPath=y"])
def test_missing_or_invalid_game_path_is_not_legacy(installation, config):
    root, app, _ = installation
    (app / "CitizenFX.ini").write_text(config)
    i = inspect_installation(root)
    assert i.edition == Edition.UNKNOWN and i.errors


def test_inventory_and_fingerprint_detect_changes(installation):
    root, app, gta = installation
    plugins = app / "plugins"
    plugins.mkdir()
    (plugins / "test.asi").write_bytes(b"plugin")
    (plugins / "test.addon64").write_bytes(b"addon")
    first = inspect_installation(root)
    assert {f.kind for f in first.inventory} == {"asi_plugin", "reshade_addon"}
    (plugins / "test.asi").write_bytes(b"changed")
    assert inspect_installation(root).fingerprint != first.fingerprint
    make_pe(gta / "dinput8.dll", [], dll=True)
    assert any(f.path == gta / "dinput8.dll" for f in inspect_installation(root).inventory)


def test_symlink_plugins_are_not_followed(installation, tmp_path):
    root, app, _ = installation
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "danger.asi").write_bytes(b"external")
    (app / "plugins").symlink_to(outside, target_is_directory=True)
    i = inspect_installation(root)
    assert i.errors and not i.inventory


def test_base_game_overlap_rejected(installation):
    root, app, _ = installation
    make_pe(root / "GTA5.exe", ["d3d11.dll"])
    (app / "CitizenFX.ini").write_text(f"[Game]\nIVPath={root}\n")
    assert any("overlap" in e for e in inspect_installation(root).errors)


def test_inventory_limit_is_explicit(installation, monkeypatch):
    from akeno.fivem import discovery
    root, app, _ = installation
    (app / "one.asi").write_bytes(b"1")
    monkeypatch.setattr(discovery, "MAX_FILES", 0)
    assert any("limit" in e for e in inspect_installation(root).errors)
