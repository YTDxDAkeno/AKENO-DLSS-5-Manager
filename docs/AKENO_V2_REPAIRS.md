# AKENO 2.1: what was repaired and rebuilt

Branch `claude/gifted-maxwell-ty7zo5`, based on the v2 baseline `956e5a2` (kept unchanged in history). The audit
behind these changes is [AKENO_V2_CLAUDE_AUDIT.md](AKENO_V2_CLAUDE_AUDIT.md); defect numbers (D1…) refer to it.

## A new mod engine: `akeno/stack/`

The v2 `akeno/mods/*` manager and `akeno/fivem/community_stack.py` gates were replaced by one engine that every
page uses (My Games, FiveM Legacy, Installed Mods, Downloads, Restore, the Advanced overview).

| Module | Job |
|--------|-----|
| `catalog.py` | Schema 3 catalog (`akeno/data/stack/components.json`, `recipes.json`, `games.json`): components and versions pinned to the original release asset (archive SHA-256 plus the SHA-256 of every installed file), recipes (which roles a complete installation needs, per API and bitness), game profiles (executable layout, renderer, preferred recipes, per-game settings). Validation refuses a pin whose URL is not exactly `releases/download/<tag>/<asset>`. |
| `releases.py` | GitHub release discovery with ETag cache, token support and readable rate-limit errors (D15). Newer upstream releases appear as "not reviewed yet" versions. |
| `sources.py` | Obtaining files: verified downloads with resume and retry (D19), the CI-built ReShade bundle (64- and 32-bit), the official ReShade setup (64- and 32-bit DLL extracted and identity-checked), local imports, discovery of runtimes already on the PC. Every binary is checked for the bitness its role needs. |
| `transports.py` | How ReShade gets into a game for its API and bitness: `dxgi.dll`, `opengl32.dll`, dgVoodoo2 + `dxgi.dll`, or a Vulkan layer registered per user. |
| `planner.py` | The file-level plan: copy, configure, take back what a previous version installed but this one does not (D13), replace or disable other tools' files only with approval (D14), exactly one neural consumer, the 32-bit `host64\` split. |
| `configs.py` | Presets mapped to real keys (OptiScaler `[DlssNr]`, RenoDX `[RenoDX.DLSS5]`, Feeder cfg, ReShade ini, dgVoodoo2 conf). |
| `targets.py` | Game folder or `FiveM.app`, running-process checks, launch (Steam, Epic, exe, `fivem://connect`). |
| `service.py` | Recommendation, per-game selection, prepare (download with progress and cancel), install, preset change, previous version, uninstall, helper recovery and self-test. |

## Repairs by defect

| # | Repair |
|---|--------|
| D1 | Archive member limit raised to 256 MiB; the 165.8 MB `nvngx_dlssnr.dll` extracts with its hash checked. |
| D2 | AKENO writes FiveM's own `[Addons] ReShade5=ID:<hash> …` line (Cfx `HashString` of the computer name) during install and removes it on uninstall if it added it. |
| D3 | Pure mode is reported as information only: Cfx `PurePackfile.cpp` checks RPF packfiles, not `plugins/`. |
| D4 | FiveM Legacy's renderer is Direct3D 11 from the Cfx source, not a PE scan of `GTA5.exe`. |
| D5 | The FiveM inventory skips shader folders and counts only classified files. |
| D6 | No permission or consent forms. The FiveM page has the client details, a server address with **Launch and connect**, and an optional `server.cfg` check. |
| D7 | RenoDX DLSS 5, `nvngx_dlss.dll` and `nvngx_dlssnr.dll` download from RankFTW/rhi-repo releases, pinned like everything else. |
| D8 | RTX 20/30/40 get the 310.8.2 SF-v2 build by default; 310.8.0-RTX40, the NVIDIA 310.8.0 (RTX 50) and 310.8.3 Lecram stay selectable; GPU-incompatible builds are blocked with a reason. |
| D9 | Every preset (Performance, Balanced, Quality, Ultra, Custom) is defined for every consumer and API. |
| D10–D12, D22 | DLSS/FSR/XeSS detection also looks in engine plugin folders; OptiScaler picks a free proxy name by Skynizz's installer rule; ReShade needs no import evidence. |
| D13 | Up- and downgrades remove files the new version does not use and restore originals they had replaced; only one active installation per game. |
| D14 | Foreign files can be replaced or disabled after approval in the preview; their originals return on removal. |
| D15 | GitHub errors (404, 403/429 rate limits, offline) are explained with a remedy, never as a raw exception. |
| D16 | Anti-cheat games need a confirmation instead of a hard block. |
| D17 | Game profiles match the executable layout, whatever the store. |
| D18 | The main path is My Games; diagnostics live in Advanced. |
| D19 | Downloads resume with HTTP `Range`, retry transient errors and keep a cache, so reinstalling works offline. |
| D20 | Only versions some recipe can install are listed. |
| D21 | `ConfigVersion=7` is written only for RenoDX builds that use it (8.5.0-rc10). |
| D23 | Config files the mods rewrite at run time no longer block updates or removal. |

## Follow-up work in 2.1 (after real use on an RTX 4080)

* **Download URLs.** Five OptiScaler pins pointed at asset names that do not exist upstream (404 for every user).
  The bytes were right, only the names were wrong. They are fixed, and the catalog now rejects a URL that is not
  the release's own asset link. The new CI job that downloads every pinned asset found this.
* **My Games scrolling.** The page used to scroll as a whole, so selecting a game far down moved the selected
  game's details above the window, and every selection rebuilt the list. The list is now a model/delegate view
  with its own scroll position, kept on selection and on status refreshes. The game page scrolls separately and
  starts at its top for every newly selected game. The page itself no longer scrolls. Tested with 300 games.
* **32-bit games** (previously a generic "not automated yet" block): the DLSS5-Feeder helper route with one
  transport per API, described in the README and covered by `tests/test_stack_x86.py`:
  * beside the game: 32-bit ReShade (AKENO CI build from ReShade 6.8.0 source, or the official setup),
    `dlss5-feed.addon32`, `DLSS5_Feed.fx`, dh_uber_motion, `ReShade.ini`, preset, `dlss5-feed.cfg`;
  * `host64\`: `dlss5-feed-host64.exe` from the same Feeder release, a 64-bit ReShade as `dxgi.dll`, RenoDX DLSS 5,
    both NGX runtimes and a minimal `ReShade.ini` without `AddonPath` holding the consumer preset;
  * Direct3D 10/11: `dxgi.dll`. Direct3D 8/9: dgVoodoo2 2.87.5 `MS\x86\D3D9.dll`/`D3D8.dll` and `dgVoodoo.conf`
    (`OutputAPI=d3d11_fl11_0`, `VRAM=1GB`, watermark off, the file's own CRLF and alignment kept), then `dxgi.dll`,
    with `DepthCopyBeforeClears=1`. OpenGL: `opengl32.dll`. Vulkan and DXVK: ReShade's own layer manifest beside the
    game, registered under `HKCU\Software\Khronos\Vulkan\ImplicitLayers` (no administrator rights, removed on
    uninstall). ReShade only activates where its `ReShade.ini` sits, so other Vulkan programs load nothing. A
    machine-wide ReShade layer with add-on support is reused; one without add-on support is explained;
  * 64-bit consumers or Feeder add-ons beside a 32-bit game are disabled with approval;
  * recovery: a helper left running after a crash is detected and can be stopped from the game page; the
    helper's own `--test` (300 NGX evaluations) can be run from there and its result line is shown;
  * the same transports serve 64-bit OpenGL, Direct3D 9 and Vulkan games on the in-process Feeder route.
* **Borderlands 4 read as DirectX 11.** Its DirectX 12 assignment was tied to Steam app 1285190 and one exact
  path. Unreal Engine 5 executables link `d3d11.dll` and load `d3d12.dll` at run time, so any other copy got the
  import-table result. The renderer now comes from the game profile for the executable layout (any store, three
  known layouts), records analysed by an older AKENO are re-analysed once on start, and a renderer the user chose
  is shown as "(your choice)" next to what Automatic would pick.
* **Responsive layout.** Action buttons and preset chips wrap, long labels and pills wrap instead of widening the
  page, and card pairs on Hardware, Settings, the Advanced overview and Smart Profiles stack when the window is
  narrow. A test checks every page at 1280×680 (1080p at 150 %) in German.
* **Truthful texts.** Settings no longer claims anti-cheat games are blocked or that only NVIDIA-signed files are
  installed. Hardware and the Advanced overview show what My Games will install for this GPU and game, instead of
  the old installer's runtime-import and server-permission verdicts.

## Removed

`akeno/mods/*`, `akeno/fivem/community_stack.py`, `akeno/ui/mod_manager.py`, `akeno/ui/fivem_community.py`,
their tests and `tools/validate-community-catalog.py`. The v1.x diagnostics, benchmarks, profiles and the legacy
per-backend installer stay available under Advanced.

## Tools

* `tools/validate-stack-catalog.py --download DIR` downloads every pinned asset and verifies it with the production
  extractor: archive and member hashes, the bitness of every binary, the consumer keys, dgVoodoo2 identity and
  config keys, and that no script would be installed.
* `tools/sync-community-releases.py` proposes pins for new upstream releases (never edits the catalog).
* `tools/build_community_bundle.py` packages the CI-built 64- and 32-bit ReShade with corresponding source.
