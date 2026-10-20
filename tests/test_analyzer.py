from akeno.core.models import AntiCheatRisk, GraphicsApi
from akeno.games import analyzer, pe
from akeno.games.launchers import RawGame
from helpers import make_pe


def test_pe_parser_reads_imports_machine_version(tmp_path):
    p = make_pe(tmp_path / "a.dll", ["KERNEL32.dll", "d3d12.dll"], machine="x64", dll=True, version=(310, 8, 0, 0),
                strings={"ProductName": "NVIDIA NGX", "OriginalFilename": "nvngx_dlssnr.dll"})
    info = pe.parse(p)
    assert info.valid and info.machine == "x64" and info.is_dll
    assert info.imports == ["kernel32.dll", "d3d12.dll"]
    assert info.file_version == "310.8.0.0"
    assert info.strings["OriginalFilename"] == "nvngx_dlssnr.dll"
    x86 = pe.parse(make_pe(tmp_path / "b.exe", ["d3d9.dll"], machine="x86"))
    assert x86.machine == "x86" and x86.imports == ["d3d9.dll"]


def test_pe_parser_rejects_garbage(tmp_path):
    (tmp_path / "x.exe").write_bytes(b"MZ" + b"\xff" * 10)
    assert not pe.parse(tmp_path / "x.exe").valid
    (tmp_path / "y.exe").write_bytes(b"\x00" * 200)
    assert not pe.parse(tmp_path / "y.exe").valid
    assert not pe.parse(tmp_path / "missing.exe").valid


def _unreal(tmp_path):
    root = tmp_path / "Stalker"
    make_pe(root / "Stalker2.exe", ["kernel32.dll"])
    make_pe(root / "Stalker2" / "Binaries" / "Win64" / "Stalker2-Win64-Shipping.exe", ["d3d12.dll", "dxgi.dll"], pad=4096)
    make_pe(root / "Engine" / "Binaries" / "Win64" / "CrashReportClient.exe", ["d3d11.dll"], pad=99999)
    make_pe(root / "Stalker2" / "Binaries" / "Win64" / "UnityCrashHandler64.exe", [], pad=200000)
    return root


def test_exe_selection_prefers_shipping_binary(tmp_path):
    root = _unreal(tmp_path)
    exe, cands = analyzer.choose_exe(RawGame("t:1", "S.T.A.L.K.E.R. 2", str(root), "steam",
                                             exe_hint=str(root / "Stalker2.exe")))
    assert exe.endswith("Stalker2-Win64-Shipping.exe")
    assert not any("CrashReport" in c or "CrashHandler" in c for c in cands)


def test_api_detection_single_multiple_unknown(tmp_path):
    one = make_pe(tmp_path / "a.exe", ["d3d12.dll"])
    assert analyzer.detect_api(str(one))[0] == GraphicsApi.D3D12
    many = make_pe(tmp_path / "b.exe", ["d3d11.dll", "vulkan-1.dll"])
    api, cands, _ = analyzer.detect_api(str(many))
    assert api == GraphicsApi.UNKNOWN and set(cands) == {"d3d11", "vulkan"}
    none = make_pe(tmp_path / "c.exe", ["kernel32.dll"])
    assert analyzer.detect_api(str(none))[0] == GraphicsApi.UNKNOWN


def test_mods_anti_cheat_and_dlss_detection(tmp_path):
    root = tmp_path / "Game"
    exe = make_pe(root / "game.exe", ["d3d12.dll"])
    make_pe(root / "nvngx_dlss.dll", [], dll=True, version=(310, 2, 1, 0))
    make_pe(root / "dxgi.dll", [], dll=True, strings={"ProductName": "ReShade", "FileDescription": "crosire's ReShade"})
    make_pe(root / "winmm.dll", [], dll=True, strings={"ProductName": "OptiScaler", "OriginalFilename": "OptiScaler.dll"})
    make_pe(root / "version.dll", [], dll=True, strings={"ProductName": "Mystery Hook"})
    (root / "renodx-dlss5-4.7.addon64").write_bytes(b"x")
    (root / "EasyAntiCheat").mkdir()
    g = analyzer.analyze(RawGame("m:1", "Game", str(root), "manual", exe_hint=str(exe)), running=[])
    kinds = {m.name: m.kind for m in g.existing_mods}
    assert kinds["dxgi.dll"] == "reshade" and kinds["winmm.dll"] == "optiscaler"
    assert kinds["version.dll"] == "unknown_proxy" and kinds["renodx-dlss5-4.7.addon64"] == "neural_consumer"
    assert g.dlss_version == "310.2.1.0" and g.upscaler_inputs == ["dlss"]
    assert g.api == GraphicsApi.D3D12 and g.architecture == "x64"
    assert g.anti_cheat == AntiCheatRisk.DETECTED and "Easy Anti-Cheat" in g.anti_cheat_details[0]
    assert g.is_running is False


def test_missing_exe_and_missing_dir(tmp_path):
    empty = tmp_path / "Empty"
    empty.mkdir()
    g = analyzer.analyze(RawGame("m:2", "Empty", str(empty), "manual"))
    assert g.exe_path is None and any("No game executable" in n for n in g.notes)
    assert g.anti_cheat == AntiCheatRisk.NONE_FOUND
    gone = analyzer.analyze(RawGame("m:3", "Gone", str(tmp_path / "gone"), "steam"))
    assert any("no longer exists" in n for n in gone.notes)
