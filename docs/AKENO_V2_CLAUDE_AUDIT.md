# AKENO DLSS 5 Manager v2: independent audit

Scope: branch `codex/akeno-v2-universal-community-dlss5` at `956e5a2` (draft PR #11), audited from the code,
the original upstream release archives and the Cfx.re (FiveM) source. The repairs are on branch
`claude/gifted-maxwell-ty7zo5` (AKENO 2.1.0); [AKENO_V2_REPAIRS.md](AKENO_V2_REPAIRS.md) lists what changed.

## How it was audited

* Read the v2 code paths end to end: `akeno/mods/*` (manager, catalog, releases, RenoDX installer),
  `akeno/fivem/community_stack.py`, `akeno/fivem/discovery.py`, `akeno/security/{archive,downloads}.py`,
  `akeno/install/transaction.py`, `akeno/ui/mod_manager.py`, `akeno/ui/fivem_community.py`.
* Downloaded the original release assets the catalog points at and opened each one with AKENO's own extractor:
  Skynizz/optiscaler-dlss5 1.2.0, 1.2.1, 1.3.0; Dagherbou/OptiScaler_DLSSNR 0.1.2, 0.2.0, 0.2.0-patch1;
  jlrouzies-fr/DLSS5-Feeder 1.17.0, 1.18.0-beta.1, 1.18.0-beta.2, 1.19.0; the RankFTW/rhi-repo releases
  (RenoDX DLSS 5 6.5.3, 7.0.0-rc8, 8.5.0-rc10; RenoDX DLSS SF; nvngx_dlss 310.9.1 and 310.9.16; nvngx_dlssnr 310.8.0
  NVIDIA, 310.8.0-RTX40, 310.8.2 SF-v2, 310.8.3 Lecram). Compared layouts, DLL names, member paths, INI keys and
  sizes against what the code expected.
* Read the upstream installers and docs for their real defaults: Skynizz's installer settings, Dagherbou's ini,
  the Feeder's `dlss5-feed.cfg` keys and motion-vector providers, the RenoDX DLSS 5 reference ini,
  kerim0x1/FiveM-DLSS5's reference stack, RHI's manifest (which runtime builds it lists for which GPUs).
* Read citizenfx/fivem (`4c4fe28`): `ReShadeFixups.cpp` (which plugin names FiveM loads, the ReShade 5+
  acknowledgement and its hash), `PurePackfile.cpp` (what pure mode checks), the Legacy renderer.
* Ran the 490-test baseline (490 passed, 3 skipped) and then drove the real GUI offscreen.

## Verdict on the baseline

The v2 baseline had a solid foundation: a journaled transaction engine with verified backups, a safe archive
extractor, pinned hashes, a PE parser and a lot of UI work. It was not usable as the product described, though.
For the two main scenarios it did not reach an installed state:

1. **A DLSS game on an RTX 4080 or 5090** could not get a Neural Rendering runtime: the NR archive was rejected by
   the extractor (D1), there was no download source for runtimes or the RenoDX consumer (D7), and RTX 40 had no
   runtime at all (D8).
2. **FiveM Legacy** was blocked before any file was written: the user had to fill in server permission forms
   (D6), a pure-mode gate rejected servers whose pure mode does not affect plugins (D3), and the client
   acknowledgement FiveM needs for ReShade 5+ had to be present already but was never written (D2).

Unit tests passed because they tested the gates with fixtures, not the path from a real release archive to a
running configuration.

## Defects found

| # | Area | Defect in `956e5a2` | Effect for the user |
|---|------|---------------------|---------------------|
| D1 | Archive extractor | `MAX_MEMBER = 128 MiB` (`akeno/security/archive.py:17`); `nvngx_dlssnr.dll` is 165.8 MB | Every NR runtime archive is refused as unsafe |
| D2 | FiveM | `_client_exception` requires `CitizenFX.ini [Addons] ReShade5=ID:<hash> …` to exist and the code is explicitly forbidden to write it | FiveM blocks ReShade 6.8; AKENO blocks the install until the user hand-edits the ini |
| D3 | FiveM | `fivem_pure`: pure level unknown or 1 blocks (`community_stack.py:66,84`) | Private servers blocked. Cfx `PurePackfile.cpp` only checks RPF packfiles; it never inspects `plugins/` |
| D4 | FiveM | Renderer taken from a PE import scan of `GTA5.exe` | Wrong or unknown renderer; the D3D11 gate fails for a correct install |
| D5 | FiveM | Inventory stops at 2000 files (`discovery.py:19`), counting shader packs | A normal ReShade shader collection in `plugins/` blocks detection |
| D6 | FiveM | Required server id, permission reference, `graphics_plugins_allowed`, consent and client-plugin confirmation | The owner of a private server gets a generic "unavailable" |
| D7 | Downloads | Consumer (RenoDX DLSS 5), SR and NR runtimes had no download path; only manual import with "review" forms | The recommended stack cannot be installed from AKENO |
| D8 | RTX 40 | Only the NVIDIA 310.8.0 runtime was considered; it refuses RTX 20/30/40 | The RTX 4080 (the FiveM PC) has no working option |
| D9 | Presets | UI offers Ultra, OptiScaler backend has no `ultra` profile (`optiscaler_nr.py` profiles) | Choosing Ultra raises "This preset is not supported" |
| D10 | Detection | Native DLSS support only from a Steam profile or a PE import of the main exe | Games whose DLSS lives in another DLL show as "no DLSS" |
| D11 | OptiScaler | Proxy DLL name chosen from import evidence only | No proxy name for games whose imports are not visible in the exe |
| D12 | ReShade | `dxgi.dll` required import evidence | Same, for the Feeder/RenoDX stacks |
| D13 | Updates | A new version installed beside the old one; stale files stayed; two active installs possible | Up/downgrades leave mixed DLLs behind |
| D14 | Conflicts | No way to replace a foreign file with approval | Any existing mod file blocks forever |
| D15 | GitHub | API rate limits and 404s surfaced as raw exceptions | Unreadable errors |
| D16 | Anti-cheat | Hard block, also for offline single-player (GTA V Story Mode) | No informed choice |
| D17 | Profiles | Game profiles matched by Steam app id only | Epic/GOG/manual copies get no profile |
| D18 | UI | Consent checkboxes, review forms and raw JSON on the main path | Diagnostic-first, not a mod manager |
| D19 | Downloads | No resume (no `Range` support), restart from zero | 160 MB runtimes restart after any interruption |
| D20 | Versions | Versions listed that no recipe could install | Dead choices |
| D21 | RenoDX | Consumer accepted only with the exact "ConfigVersion 7" contract, and `ConfigVersion=7` written for every build | Builds without that contract (6.5.3, 7.0.0-rc8) refused or misconfigured |
| D22 | Feeder | Feeder + OptiScaler required import evidence as well | Blocked for D3D11 games without DLSS |
| D23 | Transactions | `ReShade.ini`/`OptiScaler.ini` are rewritten by the mods at runtime and then counted as "modified by user" | Every update and uninstall after the first launch is blocked as a conflict |

Smaller findings: the header counted transactions, not installed games; the diagnostic overview (the old
dashboard) showed "Off, not recommended" for FiveM because of the permission gate in D6.

## Upstream facts the rebuild relies on

* **Skynizz 1.3.0** (`OptiScaler-DLSS5-1.3.0-win-x64.zip`, 6.7 MB): root `DLSS5-Optimised/`, installs
  `OptiScaler.dll` under a free proxy name (or `OptiScaler.asi` beside an ASI loader), `OptiScaler.ini`
  (78 kB, `[DlssNr]` section) and the forwarder `nvngx.dll_dlssnr.dll`; `nvngx_dlssnr.dll` comes from the user or
  RHI. Installer defaults: `Dx12Upscaler=dlss`, `PreSr=true`, `WorkingScale=0.67`, cache interval 1,
  `CacheRefreshBlend=1.0`, `WhitePointSource=3`.
* **Dagherbou 0.2.0(-patch1)** (`OptiScaler-DLSSNR-v0.2.0*.zip`, 130 MB): files at the root plus an
  `OptiScaler/` dependency folder (FSR/XeSS DLLs, `D3D12_OptiScaler/D3D12Core.dll`); 17 `DlssNr` keys, no
  pre-SR/cache keys.
* **DLSS5-Feeder 1.19.0**: in-process `dlss5-feed.addon64` plus `reshade-shaders/Shaders/DLSS5_Feed.fx`; zip uses
  backslash member names; provider `DLSS5_MV_PROVIDER` 0 = `texMotionVectors` (dh_uber_motion), 3 = LumeniteFX
  (AGNYA licence, so never downloaded automatically). The helper add-on must not be combined with in-process.
* **RHI (RankFTW/rhi-repo)** publishes the consumer and the runtimes as plain GitHub releases. Its manifest lists
  310.8.2 SF-v2 for RTX 20/30/40/50, the NVIDIA 310.8.0 build for RTX 50 only, 310.8.0-RTX40 and 310.8.3 Lecram.
* **RenoDX DLSS 5** keys: `NeuralUplift`, `NRResolutionScale`, `NRPasses`, `NRStyle`, `EnableHooks`,
  `NREnableUpscaling`; 8.5 writes `ConfigVersion=7`; `EnableHooks=1` is needed for Streamline games;
  `NRStyle=2` has crash reports.
* **FiveM** (Cfx `ReShadeFixups.cpp`): loads graphics mods only from `FiveM.app\plugins`, only as
  `d3d8/d3d9/d3d10/d3d10_1/d3d11/dxgi.dll`, checks the ProductName "ReShade", hard-blocks 5.9.0, and needs
  `[Addons] ReShade5=ID:%08x acknowledged that ReShade 5.x has a bug that will lead to game crashes` where the hash
  is Cfx `HashString` (Jenkins one-at-a-time over the lower-case `COMPUTERNAME`, signed char). FiveM's console tells
  users to add exactly this line. Legacy renders through D3D11. `fivem://connect/<addr>` connects on launch.

## What could not be verified here

No NVIDIA GPU, no game and no FiveM server are available to this audit. Image quality, frame times, whether each
runtime build loads on each GPU generation, and in-game behaviour of every preset still need the user's hardware.
[AKENO_V2_TEST_RESULTS.md](AKENO_V2_TEST_RESULTS.md) lists that checklist.
