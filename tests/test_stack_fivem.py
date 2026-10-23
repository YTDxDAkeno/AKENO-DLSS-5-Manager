"""FiveM Legacy: plugin-only install with the client's own ReShade acknowledgement, presets and full restore."""

import pytest

from akeno.stack.errors import StackError
from akeno.stack.targets import FiveMTarget, cfx_hash, reshade5_value, target_for
from helpers import make_pe, rtx
from stack_fixtures import fivem_install, services, snapshot


@pytest.fixture
def env(tmp_path, db, monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "AKENO-PC")
    return services(tmp_path, db, monkeypatch)


def test_cfx_hash_matches_cfx_hashstring():
    # Cfx HashString("a") (Jenkins one-at-a-time over the lower-cased string).
    h = 0
    for c in b"a":
        h = (h + c) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= h >> 11
    h = (h + (h << 15)) & 0xFFFFFFFF
    assert cfx_hash(b"a") == h == cfx_hash(b"A")
    # MSVC char is signed: bytes >= 0x80 are added as negative values.
    assert cfx_hash(b"\xe9") != cfx_hash(b"\x69")


def test_fivem_is_d3d11_legacy_and_gets_the_fivem_method(env, tmp_path):
    svc, fx = env
    game, root, app, gta = fivem_install(svc, tmp_path, shader_files=2500)
    assert game.api.value == "d3d11"
    inst = svc.library.fivem_installations.get(game.id)
    assert inst is not None and not inst.errors          # shader packs no longer exhaust the inventory
    st = svc.stack.state(game.id)
    assert [m.recipe.id for m in st.methods] == ["fivem"]
    assert st.recommended.available, [i.message for i in st.recommended.issues]
    assert st.preset == "performance"                       # RTX 4080 default for FiveM
    nr = next(r for r in st.recommended.roles if r.role == "nr_runtime")
    assert nr.version.version == "310.8.2"
    assert any(i.code == "fivem_ack" for i in st.recommended.issues)


def test_complete_fivem_install_preset_and_restore(env, tmp_path):
    svc, fx = env
    game, root, app, gta = fivem_install(svc, tmp_path)
    gta_before = snapshot(gta)
    app_before = snapshot(app)
    prepared = svc.stack.prepare(game.id, "fivem", preset="performance")
    paths = {r.path for r in prepared.rows}
    assert "plugins/d3d11.dll" in paths and "CitizenFX.ini" in paths and prepared.fivem_ack
    assert all(op.rel_path.startswith("plugins/") for op in prepared.plan.ops)
    res = svc.stack.install(prepared)
    assert res.ok, res.messages
    plugins = app / "plugins"
    for rel in ("d3d11.dll", "dlss5-feed.addon64", "renodx-dlss5.addon64", "nvngx_dlss.dll", "nvngx_dlssnr.dll",
                "ReShade.ini", "AKENO-DLSS5.ini", "dlss5-feed.cfg", "reshade-shaders/Shaders/DLSS5_Feed.fx",
                "reshade-shaders/Shaders/dh_uber_motion.fx"):
        assert (plugins / rel).is_file(), rel
    assert not (plugins / "dlss5-feed-helper.addon64").exists()
    ini = (plugins / "ReShade.ini").read_text()
    assert "RESHADE_DEPTH_INPUT_IS_REVERSED=1" in ini and "DLSS5_MV_PROVIDER=0" in ini
    assert "NRResolutionScale=0.5" in ini and "EnableHooks=2" in ini and "NREnableUpscaling=0" in ini
    assert "preset=5" in (plugins / "dlss5-feed.cfg").read_text()
    citizen = (app / "CitizenFX.ini").read_text()
    assert f"ReShade5={reshade5_value()}" in citizen and "IVPath=" in citizen
    assert target_for(svc.library.get(game.id)).reshade_ack() == "present"
    assert snapshot(gta) == gta_before                       # GTA V is never touched
    # FiveM rewrites CitizenFX.ini itself (e.g. its saved build); presets still work.
    (app / "CitizenFX.ini").write_text(citizen.replace("SavedBuildNumber=3258", "SavedBuildNumber=3407"))
    assert svc.stack.apply_preset(game.id, "ultra").ok
    assert "NRPasses=2" in (plugins / "ReShade.ini").read_text()
    out = svc.stack.uninstall(game.id)
    assert out.ok, out.messages
    after = snapshot(app)
    citizen_after = (app / "CitizenFX.ini").read_text()
    assert "ReShade5" not in citizen_after and "SavedBuildNumber=3407" in citizen_after
    assert {k: v for k, v in after.items() if k != "CitizenFX.ini"} == \
        {k: v for k, v in app_before.items() if k != "CitizenFX.ini"}


def test_existing_old_reshade_in_plugins_is_replaced_with_approval(env, tmp_path):
    svc, fx = env
    old = make_pe(tmp_path / "x" / "d3d11.dll", [], dll=True, version=(5, 9, 0, 0),
                  strings={"ProductName": "ReShade"}).read_bytes()
    helper = b"MZ helper"
    game, root, app, gta = fivem_install(svc, tmp_path, {"d3d11.dll": old, "dlss5-feed-helper.addon64": helper})
    before = snapshot(app / "plugins")
    p = svc.stack.prepare(game.id, "fivem")
    actions = {r.path: r.action for r in p.approvals}
    assert actions == {"plugins/d3d11.dll": "replace", "plugins/dlss5-feed-helper.addon64": "disable"}
    assert svc.stack.install(p, approve=True).ok
    assert (app / "plugins" / "dlss5-feed-helper.addon64.akeno-disabled").is_file()
    assert svc.stack.uninstall(game.id).ok
    assert snapshot(app / "plugins") == before


def test_running_fivem_blocks_changes(env, tmp_path, monkeypatch):
    svc, fx = env
    game, root, app, gta = fivem_install(svc, tmp_path)
    p = svc.stack.prepare(game.id, "fivem")
    from types import SimpleNamespace
    import akeno.core.processes as procs
    monkeypatch.setattr(procs, "list_processes",
                        lambda: [SimpleNamespace(pid=1, name="FiveM_b3258_GTAProcess.exe", path=None)])
    with pytest.raises(StackError) as e:
        svc.stack.install(p)
    assert e.value.code == "game_running" and "GTAProcess" in e.value.message


def test_enhanced_path_is_explained(env, tmp_path):
    svc, fx = env
    game, root, app, gta = fivem_install(svc, tmp_path)
    (gta / "GTA5.exe").unlink()
    make_pe(gta / "GTA5_Enhanced.exe", ["d3d12.dll"])
    game = svc.library.reanalyze(game.id)
    st = svc.stack.state(game.id)
    b = next(i for i in st.recommended.issues if i.code == "fivem_edition")
    assert "Enhanced" in b.remedy


def test_server_address_launch_validation(env, tmp_path, monkeypatch):
    svc, fx = env
    game, root, app, gta = fivem_install(svc, tmp_path)
    t = target_for(svc.library.get(game.id))
    assert isinstance(t, FiveMTarget)
    import akeno.stack.targets as targets
    calls = []
    monkeypatch.setattr(targets, "is_windows", lambda: True)
    monkeypatch.setattr(targets.subprocess, "Popen", lambda args, cwd=None: calls.append(args))
    t.launch("192.168.1.20:30120")
    assert calls[-1][1] == "fivem://connect/192.168.1.20:30120"
    t.launch("cfx.re/join/abc123")
    assert calls[-1][1] == "fivem://connect/cfx.re/join/abc123"
    with pytest.raises(StackError):
        t.launch("evil; rm -rf")


def test_rtx5090_fivem_uses_signed_model(tmp_path, db, monkeypatch):
    svc, fx = services(tmp_path, db, monkeypatch, gpu=rtx("NVIDIA GeForce RTX 5090", 0x2B85, 32, "32.0.16.1742"))
    game, root, app, gta = fivem_install(svc, tmp_path)
    st = svc.stack.state(game.id)
    nr = next(r for r in st.recommended.roles if r.role == "nr_runtime")
    assert nr.version.version == "310.8.0" and st.preset == "quality"
