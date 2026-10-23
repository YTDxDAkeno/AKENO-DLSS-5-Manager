# AKENO 2.1: test results

What was run, on what, and what it does and does not prove. Fixture tests use synthetic PE files shaped like the
real releases; the real-asset tests use the original upstream archives. Neither involves a GPU or a running game.

## Test suites

| Suite | Where | Result |
|-------|-------|--------|
| Full suite (`python -m pytest`) | Linux, Python 3.13, offscreen Qt | **509 passed**, 9 skipped (the real-asset and real-bundle tests, which need their inputs) |
| Full suite | GitHub Actions Linux (Python 3.12) and Windows Server 2022 | passed (runs 120 and later) |
| UI tests at 150 % (`QT_SCALE_FACTOR=1.5`) | Windows CI step and Linux | passed |
| Real upstream assets (`tests/test_stack_real_assets.py`, `AKENO_REAL_ASSETS_DIR`) | Linux and the CI contract job | **5 passed** |
| Bundled dependency contracts (`tests/test_v2_bundle.py`, `AKENO_TEST_REAL_BUNDLE=1`) | Windows CI, on the ReShade 6.8.0 built there (64- and 32-bit) | passed when the Windows job completes |

The v2 baseline had 490 passed, 3 skipped. Tests of removed v2 code were replaced, not dropped: the stack engine
has 55 dedicated tests (`test_stack_engine.py` 22, `test_stack_x86.py` 11, `test_stack_ui.py` 9,
`test_stack_fivem.py` 8, `test_stack_real_assets.py` 5).

### What the new tests cover

* **Installs and removal** for every recipe: files and their bitness, generated configs, preset changes, version
  up/downgrade, switching methods, previous version, foreign files replaced or disabled with approval, and a
  byte-for-byte snapshot of the game folder after removal.
* **32-bit routes** (`test_stack_x86.py`): Direct3D 11, Direct3D 10, Direct3D 9 and 8 through dgVoodoo2 (with an
  existing ENB `d3d9.dll` that comes back), OpenGL, DXVK through the per-user Vulkan layer (registered and removed),
  reuse of a machine-wide ReShade layer and the explanation when it lacks add-on support, 64-bit OpenGL/D3D9/Vulkan
  transports, 64-bit consumers beside a 32-bit game, a helper left running after a crash, and the helper's own
  self-test result parsing.
* **FiveM Legacy**: `plugins/` only, the Cfx acknowledgement hash (checked against an independent implementation),
  `CitizenFX.ini` added and removed, running-client detection, the connect address.
* **Downloads**: resume after a dropped connection, corrupted archives, missing upstream assets (404), GitHub rate
  limits, the cache.
* **GUI** (offscreen, the shipped theme): installing from My Games and the FiveM page, the Downloads page, the
  German UI, the Advanced overview agreeing with My Games, a 32-bit game's page, and **300 games** where selecting
  the last one keeps the list position, shows the game page from its top and never scrolls the page itself.
* **Layout**: every page at 1280×680 logical pixels (a 1080p screen at 150 %) in German without sideways
  scrolling; the install button visible at 1920×1080; ultrawide centring.
* **Borderlands 4**: a UE5 executable that imports only `d3d11.dll` is DirectX 12 from any store; an old record
  is re-analysed; the user's own renderer choice still wins.

## Real-package contract

`tools/validate-stack-catalog.py --download` fetched and verified **22 of 22** pinned original assets with the
production extractor: Skynizz 1.3.0/1.2.1/1.2.0, Dagherbou 0.2.0-patch1/0.2.0, RenoDX DLSS 5 7.0.0-rc8/8.5.0-rc10/
6.5.3, DLSS5-Feeder 1.19.0/1.18.0-beta.2/1.17.0 (64-bit files) and the same three for the 32-bit route (`dlss5-feed.addon32`
x86, `host64\dlss5-feed-host64.exe` x64), nvngx_dlss 310.9.16/310.9.1, nvngx_dlssnr 310.8.0/310.8.2 SF-v2/
310.8.0-RTX40/310.8.3 Lecram, dgVoodoo2 2.87.5/2.87.4 (`MS\x86\D3D9.dll`, `D3D8.dll` x86, `MS\x64\D3D9.dll` x64, all
identifying as dgVoodoo, `dgVoodoo.conf` with every key AKENO sets). No problems. The real installs then put the
original files into fixture folders: Skynizz + SF-v2, Feeder + RenoDX, FiveM Legacy, and a 32-bit Direct3D 9 game
through dgVoodoo2 and the helper, each removed again byte for byte.

This job found one real defect during 2.1: five OptiScaler pins pointed at asset names that do not exist upstream
(404). It was fixed, and the catalog now rejects such pins.

## Windows build

The Windows job builds ReShade 6.8.0 (crosire/reshade `18deaa5`, full add-on support) from source for 64-bit and
32-bit, assembles the dependency bundle with corresponding source, runs the tests, the 150 % UI tests and the source
smoke test, builds `AKENO.exe` with PyInstaller, smoke-tests the packaged executable and checks that its GUI keeps
running, audits the payload for NVIDIA files, and produces the **portable ZIP**, the **Inno Setup installer** and
**SHA256SUMS.txt** as the `AKENO-DLSS5-Manager-windows-x64` artifact, plus test results and screenshots.

## Needs real games and GPUs

None of this is provable without the hardware. The user reports community DLSS 5 Neural Rendering already
working on an RTX 4080 in FiveM and other games. Still to check on real machines:

1. **32-bit Direct3D 11 game** (for example Splinter Cell: Blacklist): ReShade overlay shows "DLSS 5 Feed", the first
   frame starts `host64\dlss5-feed-host64.exe`, `host64\dlss5-feed-host.log` shows the NGX session, the image changes.
2. **32-bit Direct3D 9 game through dgVoodoo2** (Fable Anniversary, BioShock): the game starts with dgVoodoo2 (turn
   `dgVoodooWatermark` on in `dgVoodoo.conf` once to confirm), depth works with `DepthCopyBeforeClears=1`.
3. **32-bit OpenGL game** (Worms Ultimate Mayhem, KOTOR) and **32-bit DXVK game** (WoW 3.3.5a, Guild Wars Reforged):
   ReShade loads (OpenGL: `opengl32.dll`; DXVK: the per-user Vulkan layer; `dxvk.allowFse = False` may be needed).
4. **Helper self-test** on the RTX 4080 and RTX 5090: "Test the 64-bit helper" should report 300/300.
5. **Borderlands 4**: after updating, the game shows DirectX 12 without a manual choice.
6. **FiveM Legacy** on the server build: ReShade loads from `plugins/` with the acknowledgement line, the Feeder's
   private D3D12 device starts, RenoDX DLSS 5 picks it up; frame time with Performance on the RTX 4080.
7. **Model builds**: SF-v2 on the RTX 4080, NVIDIA 310.8.0 on the RTX 5090, each with Performance/Balanced/Quality.
8. **Removal** after a real gaming session: the game folder is restored, the Vulkan layer entry is gone
   (`HKCU\Software\Khronos\Vulkan\ImplicitLayers`), and the game starts as before.
