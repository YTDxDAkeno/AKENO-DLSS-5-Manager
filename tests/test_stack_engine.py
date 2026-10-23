"""The stack engine end to end in temporary game folders: choose, download, install, preset, switch, restore."""

import json
from pathlib import Path

import pytest

from akeno.stack.errors import StackError
from helpers import make_pe, rtx
from stack_fixtures import dlss_game, plain_game, services, snapshot


@pytest.fixture
def env(tmp_path, db, monkeypatch):
    return services(tmp_path, db, monkeypatch)


def method(state, rid):
    return next(m for m in state.methods if m.recipe.id == rid)


def test_recommendation_and_versions_for_dlss_game_on_rtx4080(env, tmp_path):
    svc, fx = env
    game, _ = dlss_game(svc, tmp_path)
    st = svc.stack.state(game.id)
    assert st.recommended.recipe.id == "optiscaler"
    assert method(st, "feeder").available           # also offered, ranked below the native methods
    roles = {r.role: r for r in st.recommended.roles}
    # RTX 40: the community build listed for RTX 20-50, never NVIDIA's RTX-50-only build.
    assert roles["nr_runtime"].version.version == "310.8.2"
    assert roles["consumer_optiscaler"].version.version == "1.3.0"
    assert all(r.state == "download" for r in roles.values())
    assert st.preset == "balanced"


def test_rtx5090_gets_nvidia_signed_model_and_quality(tmp_path, db, monkeypatch):
    svc, fx = services(tmp_path, db, monkeypatch, gpu=rtx("NVIDIA GeForce RTX 5090", 0x2B85, 32, "32.0.16.1742"))
    game, _ = dlss_game(svc, tmp_path)
    st = svc.stack.state(game.id)
    nr = next(r for r in st.recommended.roles if r.role == "nr_runtime")
    assert nr.version.version == "310.8.0" and nr.version.signed == "nvidia"
    assert st.preset == "quality"


def test_selecting_rtx50_only_model_on_rtx40_is_blocked_with_explanation(env, tmp_path):
    svc, fx = env
    game, _ = dlss_game(svc, tmp_path)
    svc.stack.choose_version(game.id, "nr_runtime", "nvngx-dlssnr:310.8.0")
    st = svc.stack.state(game.id)
    blocker = next(i for i in st.selected.issues if i.level == "block")
    assert "does not run on RTX 40" in blocker.message and blocker.action == "versions"


def test_full_optiscaler_install_preset_and_complete_removal(env, tmp_path):
    svc, fx = env
    game, exe_dir = dlss_game(svc, tmp_path)
    before = snapshot(exe_dir.parent.parent)
    seen = []
    prepared = svc.stack.prepare(game.id, "optiscaler", preset="quality",
                                 progress=lambda label, cur, tot: seen.append((label, cur, tot)))
    assert seen and seen[-1][1] > 0
    assert not prepared.approvals
    res = svc.stack.install(prepared)
    assert res.ok, res.messages
    for name in ("winmm.dll", "nvngx.dll_dlssnr.dll", "nvngx_dlssnr.dll", "OptiScaler.ini", "akeno-dlss5.json"):
        assert (exe_dir / name).is_file(), name
    ini = (exe_dir / "OptiScaler.ini").read_text()
    assert "Dx12Upscaler=dlss" in ini and "PreSr=true" in ini and "CacheInterval=1" in ini and "; comment kept" in ini
    # the game's own DLSS runtime is untouched
    assert snapshot(exe_dir)["nvngx_dlss.dll"] == before["bin/x64/nvngx_dlss.dll"]
    r = svc.stack.apply_preset(game.id, "performance")
    assert r.ok, r.messages
    assert "CacheInterval=2" in (exe_dir / "OptiScaler.ini").read_text()
    r = svc.stack.apply_preset(game.id, "ultra")
    ini = (exe_dir / "OptiScaler.ini").read_text()
    assert r.ok and "PreSr=false" in ini and "WorkingScale=1.0" in ini and "CacheEnabled=false" in ini
    # OptiScaler's own overlay rewrites its ini: that must not block later changes.
    (exe_dir / "OptiScaler.ini").write_text(ini + "\n; saved by OptiScaler overlay\n")
    r = svc.stack.apply_preset(game.id, "balanced")
    assert r.ok, r.messages
    out = svc.stack.uninstall(game.id)
    assert out.ok, out.messages
    assert snapshot(exe_dir.parent.parent) == before


def test_custom_preset_validates_supported_keys(env, tmp_path):
    svc, fx = env
    game, exe_dir = dlss_game(svc, tmp_path)
    svc.stack.install(svc.stack.prepare(game.id, "optiscaler"))
    r = svc.stack.apply_preset(game.id, "custom", {"DlssNr.WorkingScale": 0.8, "DlssNr.Passes": 2})
    ini = (exe_dir / "OptiScaler.ini").read_text()
    assert r.ok and "WorkingScale=0.8" in ini and "Passes=2" in ini
    with pytest.raises(ValueError):
        svc.stack.apply_preset(game.id, "custom", {"DlssNr.Unknown": 1})
    with pytest.raises(ValueError):
        svc.stack.apply_preset(game.id, "custom", {"DlssNr.WorkingScale": 9})


def test_version_upgrade_downgrade_and_previous_restore(env, tmp_path):
    svc, fx = env
    game, exe_dir = dlss_game(svc, tmp_path)
    svc.stack.choose_version(game.id, "consumer_optiscaler", "optiscaler-dlss5:1.2.1")
    assert svc.stack.install(svc.stack.prepare(game.id, "optiscaler")).ok
    old_dll = (exe_dir / "winmm.dll").read_bytes()
    svc.stack.choose_version(game.id, "consumer_optiscaler", "optiscaler-dlss5:1.3.0")
    p = svc.stack.prepare(game.id)
    assert any(r.path == "winmm.dll" and r.action == "update" for r in p.rows)
    assert svc.stack.install(p).ok
    assert (exe_dir / "winmm.dll").read_bytes() != old_dll
    prev = svc.stack.previous_versions(game.id)
    assert prev and prev[0]["params"]["versions"]["consumer_optiscaler"] == "optiscaler-dlss5:1.2.1"
    # going back works from the cache only: no new requests for the archive
    before = len(fx["server"].requests)
    assert svc.stack.install(svc.stack.restore_previous(game.id)).ok
    assert len(fx["server"].requests) == before
    assert (exe_dir / "winmm.dll").read_bytes() == old_dll


def test_switching_method_removes_files_of_the_old_method(env, tmp_path):
    svc, fx = env
    game, exe_dir = dlss_game(svc, tmp_path)
    original = snapshot(exe_dir)
    assert svc.stack.install(svc.stack.prepare(game.id, "optiscaler")).ok
    p = svc.stack.prepare(game.id, "renodx")
    removed = {r.path for r in p.rows if r.action == "remove"}
    assert {"winmm.dll", "nvngx.dll_dlssnr.dll", "OptiScaler.ini"} <= removed
    assert svc.stack.install(p).ok
    assert not (exe_dir / "winmm.dll").exists() and (exe_dir / "dxgi.dll").is_file()
    assert (exe_dir / "renodx-dlss5.addon64").is_file()
    assert "[RenoDX.DLSS5]" in (exe_dir / "ReShade.ini").read_text()
    assert svc.stack.uninstall(game.id).ok
    assert snapshot(exe_dir) == original


def test_foreign_files_need_approval_and_come_back(env, tmp_path):
    svc, fx = env
    reshade_old = make_pe(tmp_path / "x" / "dxgi.dll", [], dll=True, version=(5, 9, 0, 0),
                          strings={"ProductName": "ReShade"}).read_bytes()
    chicken = b"MZ fake add-on"
    game, exe_dir = dlss_game(svc, tmp_path, extra=[("dxgi.dll", reshade_old),
                                                    ("deep-fried-chicken.addon64", chicken)])
    original = snapshot(exe_dir)
    p = svc.stack.prepare(game.id, "renodx")
    actions = {r.path: r.action for r in p.approvals}
    assert actions == {"dxgi.dll": "replace", "deep-fried-chicken.addon64": "disable"}
    with pytest.raises(StackError) as e:
        svc.stack.install(p)
    assert e.value.code == "approval_needed"
    res = svc.stack.install(p, approve=True)
    assert res.ok, res.messages
    assert (exe_dir / "deep-fried-chicken.addon64.akeno-disabled").is_file()
    assert svc.stack.uninstall(game.id).ok
    assert snapshot(exe_dir) == original


def test_existing_modern_reshade_is_reused_not_replaced(env, tmp_path):
    svc, fx = env
    mine = make_pe(tmp_path / "x" / "dxgi.dll", [], dll=True, version=(6, 7, 3, 0), strings={"ProductName": "ReShade"},
                   exports=["ReShadeRegisterAddon", "ReShadeUnregisterAddon"]).read_bytes()
    game, exe_dir = dlss_game(svc, tmp_path, extra=[("dxgi.dll", mine)])
    p = svc.stack.prepare(game.id, "renodx")
    assert not p.approvals
    assert any(r.path == "dxgi.dll" and r.action == "keep" for r in p.rows)
    assert svc.stack.install(p).ok
    assert (exe_dir / "dxgi.dll").read_bytes() == mine
    svc.stack.uninstall(game.id)
    assert (exe_dir / "dxgi.dll").read_bytes() == mine


def test_streamline_games_get_enable_hooks_1_for_renodx(env, tmp_path):
    svc, fx = env
    game, exe_dir = dlss_game(svc, tmp_path, streamline=True)
    assert svc.stack.install(svc.stack.prepare(game.id, "renodx", preset="ultra")).ok
    text = (exe_dir / "ReShade.ini").read_text()
    assert "EnableHooks=1" in text and "NRPasses=2" in text and "NREnableUpscaling=0" in text
    assert "ConfigVersion" not in text                  # 7.0.0-rc8 has no ConfigVersion 7 contract
    svc.stack.choose_version(game.id, "consumer_renodx", "renodx-dlss5:8.5.0-rc10")
    assert svc.stack.install(svc.stack.prepare(game.id)).ok
    assert "ConfigVersion=7" in (exe_dir / "ReShade.ini").read_text()


def test_feeder_stack_for_game_without_dlss(env, tmp_path):
    svc, fx = env
    game, root = plain_game(svc, tmp_path)
    st = svc.stack.state(game.id)
    assert st.recommended.recipe.id == "feeder"
    assert not method(st, "optiscaler").available and not method(st, "renodx").available
    assert "No DLSS, FSR 2+ or XeSS" in method(st, "optiscaler").blockers[0].message
    res = svc.stack.install(svc.stack.prepare(game.id, "feeder", preset="performance"))
    assert res.ok, res.messages
    for rel in ("dxgi.dll", "dlss5-feed.addon64", "renodx-dlss5.addon64", "nvngx_dlss.dll", "nvngx_dlssnr.dll",
                "dlss5-feed.cfg", "AKENO-DLSS5.ini", "reshade-shaders/Shaders/DLSS5_Feed.fx",
                "reshade-shaders/Shaders/dh_uber_motion.fx", "reshade-shaders/Shaders/ReShade.fxh"):
        assert (root / rel).is_file(), rel
    assert not (root / "dlss5-feed-helper.addon64").exists() and not (root / "host64").exists()
    ini = (root / "ReShade.ini").read_text()
    assert "DLSS5_MV_PROVIDER=0" in ini and "EnableHooks=2" in ini and "NRResolutionScale=0.5" in ini
    assert "PresetPath=.\\AKENO-DLSS5.ini" in ini and "AddonPath=.\\" in ini
    preset = (root / "AKENO-DLSS5.ini").read_text()
    assert preset.index("DH_UBER_MOTION_020") < preset.index("DLSS5_Feed@")
    cfg = (root / "dlss5-feed.cfg").read_text()
    assert "mode=2" in cfg and "work_resolution=100" in cfg
    assert svc.stack.uninstall(game.id).ok
    assert sorted(p.name for p in root.iterdir()) == ["Racer.exe"]


def test_game_running_blocks_install(tmp_path, db, monkeypatch):
    svc, fx = services(tmp_path, db, monkeypatch, running=lambda d: [type("P", (), {"name": "Racer.exe"})()])
    game, root = plain_game(svc, tmp_path)
    p = svc.stack.prepare(game.id, "feeder")
    import akeno.stack.targets as targets
    monkeypatch.setattr(targets.GameTarget, "running", lambda self: [type("P", (), {"name": "Racer.exe"})()])
    with pytest.raises(StackError) as e:
        svc.stack.install(p)
    assert e.value.code == "game_running"


def test_changed_folder_after_preview_requires_new_preview(env, tmp_path):
    svc, fx = env
    game, exe_dir = dlss_game(svc, tmp_path)
    p = svc.stack.prepare(game.id, "optiscaler")
    (exe_dir / "winmm.dll").write_bytes(b"someone else")
    with pytest.raises(StackError) as e:
        svc.stack.install(p)
    assert e.value.code == "changed_after_preview"


def test_anti_cheat_requires_acknowledgement(env, tmp_path):
    svc, fx = env
    game, exe_dir = dlss_game(svc, tmp_path, extra=[("BattlEye/BEClient_x64.dll", b"x")])
    (exe_dir / "BattlEye").mkdir(exist_ok=True)
    game = svc.library.reanalyze(game.id)
    st = svc.stack.state(game.id)
    assert any(i.code == "anti_cheat" and i.level == "block" for i in st.issues)
    svc.stack.save_selection(game.id, anti_cheat_ack=True)
    assert not any(i.code == "anti_cheat" for i in svc.stack.state(game.id).issues)


def test_corrupted_download_is_rejected_and_explained(env, tmp_path):
    svc, fx = env
    game, exe_dir = dlss_game(svc, tmp_path)
    url = next(u for u in fx["server"].files if "OptiScaler-DLSS5-1.3.0" in u)
    fx["server"].files[url] = b"PK\x03\x04 broken"
    from akeno.stack.errors import explain
    with pytest.raises(Exception) as e:
        svc.stack.prepare(game.id, "optiscaler")
    assert explain(e.value).code == "hash_mismatch"
    assert not any(p.name.endswith(".part") for p in (tmp_path / "dl").iterdir())
    assert sorted(p.name for p in exe_dir.iterdir()) == ["NightCity.exe", "nvngx_dlss.dll"]


def test_interrupted_download_resumes_with_range(env, tmp_path):
    svc, fx = env
    game, exe_dir = dlss_game(svc, tmp_path)
    url = next(u for u in fx["server"].files if "nvngx_dlssnr_310.8.SF-v2" in u)
    fx["server"].cut_after[url] = 200
    svc.stack.prepare(game.id, "optiscaler")
    ranges = [r for u, r in fx["server"].requests if u == url]
    assert ranges[0] is None and ranges[1] == "bytes=200-"


def test_missing_upstream_asset_is_explained(env, tmp_path):
    svc, fx = env
    game, exe_dir = dlss_game(svc, tmp_path)
    url = next(u for u in fx["server"].files if "OptiScaler-DLSS5-1.3.0" in u)
    fx["server"].fail[url] = 404
    from akeno.stack.errors import explain
    with pytest.raises(Exception) as e:
        svc.stack.prepare(game.id, "optiscaler")
    err = explain(e.value)
    assert err.code == "not_found" and "cached copy" in err.remedy
    assert len([1 for u, _ in fx["server"].requests if u == url]) == 1       # 404 is not retried


def test_dagherbou_build_installs_its_dependency_folder(env, tmp_path):
    svc, fx = env
    game, exe_dir = dlss_game(svc, tmp_path)
    svc.stack.choose_version(game.id, "consumer_optiscaler", "optiscaler-dlssnr:0.2.0-patch1")
    res = svc.stack.install(svc.stack.prepare(game.id, "optiscaler", preset="ultra"))
    assert res.ok, res.messages
    assert (exe_dir / "OptiScaler" / "libxess.dll").is_file()
    assert (exe_dir / "OptiScaler" / "D3D12_OptiScaler" / "D3D12Core.dll").is_file()
    ini = (exe_dir / "OptiScaler.ini").read_text()
    assert "WorkingScale=1.5" in ini and "PreSr" not in ini.split("[DlssNr]")[1]
    assert svc.stack.uninstall(game.id).ok
    assert not (exe_dir / "OptiScaler").exists()


def test_asi_loader_makes_optiscaler_load_as_asi(env, tmp_path):
    svc, fx = env
    loader = make_pe(tmp_path / "x" / "dinput8.dll", [], dll=True, pad=10).read_bytes() + b"Ultimate-ASI-Loader"
    game, exe_dir = dlss_game(svc, tmp_path, extra=[("dinput8.dll", loader)])
    p = svc.stack.prepare(game.id, "optiscaler")
    assert p.plan.params["proxy"] == "OptiScaler.asi"


def test_selection_is_per_game(env, tmp_path):
    svc, fx = env
    a, _ = dlss_game(svc, tmp_path, "Game A")
    b, _ = dlss_game(svc, tmp_path, "Game B")
    svc.stack.choose_preset(a.id, "ultra")
    svc.stack.choose_recipe(a.id, "renodx")
    assert svc.stack.state(b.id).preset != "ultra" and svc.stack.state(b.id).selected.recipe.id == "optiscaler"
    assert svc.stack.state(a.id).selected.recipe.id == "renodx"


def test_catalog_pin_url_must_be_the_release_asset_itself():
    import pytest
    from akeno.stack.catalog import StackCatalog, _check_asset_url
    _check_asset_url("a/b", "https://github.com/a/b/releases/download/v1/x.zip", "v1", "x.zip")
    with pytest.raises(ValueError):           # a renamed asset would 404 for every user
        _check_asset_url("a/b", "https://github.com/a/b/releases/download/v1/x.zip", "v1", "y.zip")
    with pytest.raises(ValueError):
        _check_asset_url("a/b", "https://github.com/c/d/releases/download/v1/x.zip")
    for c in StackCatalog.load().components.values():
        for v in c.versions:
            if v.source == "download":
                assert v.url.endswith(f"/releases/download/{v.tag}/{v.asset}")


def test_borderlands4_is_directx12_whatever_the_store_and_old_records_are_refreshed(tmp_path, db, monkeypatch):
    """UE5 links d3d11.dll and loads d3d12.dll at run time; the import table alone said DirectX 11."""
    from akeno.core.models import GraphicsApi
    from akeno.games import analyzer
    from akeno.games.launchers import RawGame
    root = tmp_path / "Borderlands 4"
    make_pe(root / "OakGame/Binaries/Win64/Borderlands4.exe", ["d3d11.dll", "dxgi.dll", "kernel32.dll"], pad=60000)
    raw = RawGame(id="epic:catnip", name="Borderlands 4", install_dir=str(root), launcher="epic", launcher_id="catnip")
    game = analyzer.analyze(raw, running=[])
    assert game.api == GraphicsApi.D3D12 and "game profile" in game.api_source
    assert game.analysis_version == analyzer.ANALYSIS_VERSION
    svc, fx = services(tmp_path, db, monkeypatch)
    stale = dict(game.to_dict(), api="d3d11", api_source="executable imports", analysis_version=0)
    db.upsert_game(stale, manual=False)
    assert [g.id for g in svc.library.stale_games()] == [game.id]
    monkeypatch.setattr(svc.library, "_raw_from_game", lambda g: raw)
    svc.library.reanalyze(game.id)
    assert svc.library.get(game.id).api == GraphicsApi.D3D12 and not svc.library.stale_games()
    svc.library.set_override(game.id, "api", "d3d11")       # the user's explicit choice still wins
    assert svc.library.get(game.id).api == GraphicsApi.D3D11
    assert svc.stack.game_api(svc.library.get(game.id), svc.stack.catalog.profile_for(svc.library.get(game.id))) == "d3d11"
