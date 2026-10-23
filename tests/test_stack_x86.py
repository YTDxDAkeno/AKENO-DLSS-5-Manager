"""32-bit games: the DLSS5-Feeder helper route, one transport per graphics API, fully reversible.

Layouts follow DLSS5-Feeder's README and tools/Install-DLSS5Feeder.ps1 (1.19.0): 32-bit ReShade + dlss5-feed.addon32
beside the game, host64\\ with dlss5-feed-host64.exe, a 64-bit ReShade as dxgi.dll, the neural consumer and both NGX
runtimes; dgVoodoo2 in front of Direct3D 8/9; opengl32.dll for OpenGL; a Vulkan layer for Vulkan and DXVK.
"""

import pytest

from akeno.backends import ini
from akeno.games import pe
from helpers import make_pe
from stack_fixtures import services, snapshot


def x86_game(svc, tmp_path, name="Retro Shooter", imports=("d3d11.dll", "dxgi.dll", "kernel32.dll"), extra=()):
    root = tmp_path / "games" / name
    make_pe(root / "Game.exe", list(imports), machine="x86", pad=60000)
    for rel, data in extra:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return svc.library.add_manual(str(root), name), root


def machine(path):
    return pe.parse(str(path)).machine


@pytest.fixture
def env(tmp_path, db, monkeypatch):
    from akeno.stack import transports
    registry = transports._MemoryRegistry()
    monkeypatch.setattr(transports, "REGISTRY", registry)
    svc, fx = services(tmp_path, db, monkeypatch)
    return svc, fx, registry


def install(svc, game, preset="balanced", approve=False):
    res = svc.stack.install(svc.stack.prepare(game.id, "feeder_x86", preset=preset), approve=approve)
    assert res.ok, res.messages
    return res


def test_32bit_directx11_game_gets_the_helper_route_and_comes_back_byte_for_byte(env, tmp_path):
    svc, fx, _ = env
    game, root = x86_game(svc, tmp_path)
    assert game.architecture == "x86"
    st = svc.stack.state(game.id)
    assert [m.recipe.id for m in st.methods] == ["feeder_x86"], "a 32-bit game only sees the 32-bit route"
    assert st.recommended.available and not any(i.code == "x86" for i in st.issues)
    assert "translator" not in [r.role for r in st.recommended.roles], "dgVoodoo2 only for Direct3D 8/9"
    before = snapshot(root)
    install(svc, game)
    host = root / "host64"
    assert machine(root / "dxgi.dll") == "x86" and machine(host / "dxgi.dll") == "x64"
    assert (root / "dlss5-feed.addon32").is_file() and not (root / "dlss5-feed.addon64").exists()
    assert (root / "reshade-shaders/Shaders/DLSS5_Feed.fx").is_file()
    assert (root / "reshade-shaders/Shaders/dh_uber_motion.fx").is_file()
    for name in ("dlss5-feed-host64.exe", "renodx-dlss5.addon64", "nvngx_dlss.dll", "nvngx_dlssnr.dll"):
        assert (host / name).is_file(), name
    assert not (root / "renodx-dlss5.addon64").exists() and not (root / "nvngx_dlssnr.dll").exists()
    game_ini = ini.get_values((root / "ReShade.ini").read_text())
    assert game_ini["ADDON"]["AddonPath"] == ".\\" and "RenoDX.DLSS5" not in game_ini
    assert "DLSS5_MV_PROVIDER=0" in game_ini["GENERAL"]["PreprocessorDefinitions"]
    host_ini = ini.get_values((host / "ReShade.ini").read_text())
    assert "ADDON" not in host_ini, "the helper's ReShade loads its own folder's add-ons"
    assert host_ini["GENERAL"]["EffectSearchPaths"] == ".\\"
    assert host_ini["RenoDX.DLSS5"]["NRResolutionScale"] == "0.67" and host_ini["RenoDX.DLSS5"]["EnableHooks"] == "2"
    # a preset change rewrites the helper's consumer settings only
    assert svc.stack.apply_preset(game.id, "performance").ok
    assert ini.get_values((host / "ReShade.ini").read_text())["RenoDX.DLSS5"]["NRResolutionScale"] == "0.5"
    assert svc.stack.uninstall(game.id).ok
    assert snapshot(root) == before


def test_directx10_uses_the_same_dxgi_route(env, tmp_path):
    svc, fx, _ = env
    game, root = x86_game(svc, tmp_path, "Old Shooter", imports=("d3d10.dll", "dxgi.dll"))
    assert game.api.value == "d3d10"
    install(svc, game)
    assert machine(root / "dxgi.dll") == "x86" and (root / "host64/dlss5-feed-host64.exe").is_file()


@pytest.mark.parametrize("api,dll", [("d3d9", "D3D9.dll"), ("d3d8", "D3D8.dll")])
def test_directx8_and_9_go_through_dgvoodoo2(env, tmp_path, api, dll):
    svc, fx, _ = env
    enb = make_pe(tmp_path / "enb" / "d3d9.dll", [], machine="x86", dll=True, strings={"ProductName": "ENBSeries"})
    extra = [("d3d9.dll", enb.read_bytes())] if api == "d3d9" else []
    game, root = x86_game(svc, tmp_path, "Fable Like", imports=(api + ".dll", "kernel32.dll"), extra=extra)
    before = snapshot(root)
    st = svc.stack.state(game.id)
    assert "translator" in [r.role for r in st.recommended.roles]
    assert any(i.code == "transport" and "dgVoodoo2" in i.message for i in st.recommended.issues)
    prepared = svc.stack.prepare(game.id, "feeder_x86")
    if api == "d3d9":
        assert any(r.path.lower() == "d3d9.dll" and r.action == "replace" for r in prepared.approvals)
    res = svc.stack.install(prepared, approve=True)
    assert res.ok, res.messages
    assert machine(root / dll) == "x86" and "dgVoodoo" in pe.parse(str(root / dll)).strings.get("ProductName", "")
    assert machine(root / "dxgi.dll") == "x86", "ReShade goes in as dxgi.dll, dgVoodoo owns the Direct3D name"
    conf = ini.get_values((root / "dgVoodoo.conf").read_text())
    assert conf["General"]["OutputAPI"] == "d3d11_fl11_0"
    assert conf["DirectX"]["VRAM"] == "1GB" and conf["DirectX"]["dgVoodooWatermark"] == "false"
    assert conf["Glide"]["VideoCard"] == "voodoo_2", "only the DirectX section is changed"
    assert "\r\n" in (root / "dgVoodoo.conf").read_bytes().decode(), "dgVoodoo's own line endings are kept"
    assert ini.get_values((root / "ReShade.ini").read_text())["DEPTH"]["DepthCopyBeforeClears"] == "1"
    assert svc.stack.uninstall(game.id).ok
    assert snapshot(root) == before, "the ENB d3d9.dll is back"


def test_opengl_32bit_loads_reshade_as_opengl32(env, tmp_path):
    svc, fx, _ = env
    game, root = x86_game(svc, tmp_path, "Worms Like", imports=("opengl32.dll", "kernel32.dll"))
    install(svc, game)
    assert machine(root / "opengl32.dll") == "x86" and not (root / "dxgi.dll").exists()
    assert svc.stack.uninstall(game.id).ok


def test_dxvk_32bit_game_gets_a_per_user_vulkan_layer(env, tmp_path):
    svc, fx, registry = env
    dxvk = make_pe(tmp_path / "dxvk" / "d3d9.dll", ["vulkan-1.dll"], machine="x86", dll=True,
                   strings={"ProductName": "DXVK", "FileDescription": "DXVK d3d9"})
    game, root = x86_game(svc, tmp_path, "WoW Like", imports=("d3d9.dll", "kernel32.dll"),
                          extra=[("d3d9.dll", dxvk.read_bytes())])
    assert game.api.value == "vulkan" and "DXVK" in game.api_source
    before = snapshot(root)
    st = svc.stack.state(game.id)
    assert "translator" not in [r.role for r in st.recommended.roles], "DXVK, not dgVoodoo2"
    res = install(svc, game)
    manifest = root / "AKENO-ReShade32.json"
    assert machine(root / "ReShade32.dll") == "x86" and manifest.is_file()
    assert '".\\\\ReShade32.dll"' in manifest.read_text() and "VK_LAYER_reshade" in manifest.read_text()
    assert str(manifest) in registry.user_values(), "registered for this Windows user"
    assert (root / "d3d9.dll").read_bytes() == dxvk.read_bytes(), "DXVK itself is never touched"
    assert (root / "ReShade.ini").is_file(), "ReShade only activates where its ini sits next to the exe"
    assert any("Vulkan layer" in m for m in res.messages)
    assert svc.stack.uninstall(game.id).ok
    assert str(manifest) not in registry.user_values()
    assert snapshot(root) == before


def test_an_existing_machine_wide_reshade_layer_is_reused_or_explained(env, tmp_path):
    svc, fx, registry = env
    layer_dir = tmp_path / "ProgramData" / "ReShade"
    good = make_pe(layer_dir / "ReShade32.dll", [], machine="x86", dll=True, version=(6, 8, 0, 0),
                   strings={"ProductName": "ReShade"}, exports=["ReShadeRegisterAddon", "ReShadeUnregisterAddon"])
    from akeno.stack import transports
    (layer_dir / "ReShade32.json").write_text(transports.layer_manifest("ReShade32.dll", "x86"))
    registry.machine["x86"][str(layer_dir / "ReShade32.json")] = 0
    game, root = x86_game(svc, tmp_path, "Vk32", imports=("vulkan-1.dll", "kernel32.dll"))
    install(svc, game)
    assert not (root / "ReShade32.dll").exists() and not registry.user_values(), "the machine-wide layer is used"
    assert (root / "ReShade.ini").is_file() and (root / "dlss5-feed.addon32").is_file()
    assert svc.stack.uninstall(game.id).ok
    # a machine-wide layer without add-on support blocks with a clear way out
    make_pe(good, [], machine="x86", dll=True, version=(6, 8, 0, 0), strings={"ProductName": "ReShade"})
    st = svc.stack.state(game.id)
    block = next(i for i in st.recommended.issues if i.code == "vk_layer")
    assert block.level == "block" and "add-on" in block.message


def test_64bit_opengl_directx9_and_vulkan_games_use_the_matching_transport(env, tmp_path):
    svc, fx, registry = env
    cases = (("opengl32.dll", "Gl64", "opengl32.dll"), ("d3d9.dll", "Dx9x64", "D3D9.dll"),
             ("vulkan-1.dll", "Vk64", "ReShade64.dll"))
    for imp, name, expected in cases:
        root = tmp_path / "games" / name
        make_pe(root / "Game.exe", [imp, "kernel32.dll"], pad=60000)
        game = svc.library.add_manual(str(root), name)
        before = snapshot(root)
        res = svc.stack.install(svc.stack.prepare(game.id, "feeder", preset="balanced"))
        assert res.ok, (name, res.messages)
        assert machine(root / expected) == "x64", name
        assert (root / "dlss5-feed.addon64").is_file() and not (root / "host64").exists()
        if imp == "d3d9.dll":
            assert machine(root / "dxgi.dll") == "x64" and (root / "dgVoodoo.conf").is_file()
        if imp == "vulkan-1.dll":
            assert str(root / "AKENO-ReShade64.json") in registry.user_values()
        assert svc.stack.uninstall(game.id).ok
        assert snapshot(root) == before, name
    assert not registry.user_values()


def test_64bit_consumers_beside_a_32bit_game_are_moved_out_of_the_way(env, tmp_path):
    svc, fx, _ = env
    stray = make_pe(tmp_path / "stray" / "renodx-dlss5.addon64", [], dll=True).read_bytes() + b"[RenoDX.DLSS5]"
    feed64 = make_pe(tmp_path / "stray" / "dlss5-feed.addon64", [], dll=True).read_bytes()
    game, root = x86_game(svc, tmp_path, "Mixed", extra=[("renodx-dlss5.addon64", stray),
                                                         ("dlss5-feed.addon64", feed64),
                                                         ("host64/dlss5-feed.addon64", feed64)])
    before = snapshot(root)
    prepared = svc.stack.prepare(game.id, "feeder_x86")
    disabled = {r.path.lower() for r in prepared.approvals if r.action == "disable"}
    assert {"renodx-dlss5.addon64", "dlss5-feed.addon64", "host64/dlss5-feed.addon64"} <= disabled
    assert svc.stack.install(prepared, approve=True).ok
    assert not (root / "dlss5-feed.addon64").exists() and (root / "dlss5-feed.addon64.akeno-disabled").exists()
    assert svc.stack.uninstall(game.id).ok
    assert snapshot(root) == before


def test_a_helper_left_running_after_a_crash_can_be_stopped(env, tmp_path, monkeypatch):
    svc, fx, _ = env
    game, root = x86_game(svc, tmp_path)
    install(svc, game)
    from akeno.core import processes
    from akeno.core.processes import ProcessInfo
    helper = ProcessInfo(4242, "dlss5-feed-host64.exe", str(root / "host64" / "dlss5-feed-host64.exe"))
    game_proc = ProcessInfo(4243, "Game.exe", str(root / "Game.exe"))
    running = [helper]
    monkeypatch.setattr(processes, "processes_in_dir", lambda d, procs=None: [
        p for p in running if p.path.lower().startswith(str(d).lower())])
    killed = []
    import akeno.stack.service as service_mod
    monkeypatch.setattr(service_mod.os, "kill", lambda pid, sig: killed.append(pid))
    st = svc.stack.state(game.id)
    assert any(i.code == "helper_running" and i.action == "stop_helper" for i in st.issues)
    running.append(game_proc)
    assert not svc.stack.stop_helper(game.id).ok, "never while the game itself runs"
    running.remove(game_proc)
    assert svc.stack.stop_helper(game.id).ok and killed == [4242]


def test_helper_self_test_reports_the_helpers_own_result(env, tmp_path, monkeypatch):
    svc, fx, _ = env
    game, root = x86_game(svc, tmp_path)
    install(svc, game)
    import akeno.stack.service as service_mod
    monkeypatch.setattr(service_mod, "is_windows", lambda: True)

    def fake_run(args, cwd, **kw):
        assert args[1] == "--test" and cwd.endswith("host64")
        (root / "host64" / "dlss5-feed-host.log").write_text("[host] --test finished: 300/300 evaluates succeeded\n")
    monkeypatch.setattr(service_mod.subprocess, "run", fake_run)
    res = svc.stack.test_helper(game.id)
    assert res.ok and "300/300" in res.messages[0]

    def failing(args, cwd, **kw):
        (root / "host64" / "dlss5-feed-host.log").write_text("[host] --test finished: 12/300 evaluates succeeded\n")
    monkeypatch.setattr(service_mod.subprocess, "run", failing)
    res = svc.stack.test_helper(game.id)
    assert not res.ok and "12/300" in res.messages[0]


def test_64bit_routes_are_not_offered_to_32bit_games_and_vice_versa(env, tmp_path):
    svc, fx, _ = env
    from stack_fixtures import plain_game
    g64, _ = plain_game(svc, tmp_path)
    assert "feeder_x86" not in [m.recipe.id for m in svc.stack.state(g64.id).methods]
    g32, _ = x86_game(svc, tmp_path)
    assert {m.recipe.id for m in svc.stack.state(g32.id).methods} == {"feeder_x86"}
