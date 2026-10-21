# Dependencies: where each one comes from, and what AKENO may automate

Reviewed 2026-10-20 for v1.1.0. This page is the reasoning behind `akeno/deps/resolver.py` and
`akeno/backends/runtimes.py`. If a fact here changes (a new official download, a licence change), update
this page, the catalog and the resolver together.

## v1.3 separate local community workflow

The historic v1.1 signed-only import rules below still apply to the official dependency workflow. v1.3 additionally provides a separately scoped user-reviewed local community cache and offline experiment service; it does not disable the official verifier or execute upstream installers. See [COMMUNITY_RUNTIME_GUIDE.md](COMMUNITY_RUNTIME_GUIDE.md) and [COMMUNITY_RUNTIME_REVIEW.md](COMMUNITY_RUNTIME_REVIEW.md). Modified/unsigned models are never NVIDIA-authenticated, bundled or automatically downloaded; uncertain licensing/provenance keeps them unreviewed. Local review records are explicit user evidence, not automatic maintainer endorsement.

## Summary

| Dependency | Needed by | Source AKENO uses | Automatic? | Why |
|---|---|---|---|---|
| OptiScaler DLSS 5 1.3.0 (Skynizz) | OptiScaler backend | Official GitHub release URL pinned in `data/catalog.json` | **Yes**, after you confirm | GPL-3.0, redistributable; archive + per-file SHA-256 pinned at review |
| DLSS5-Feeder 1.19.0 | Feeder backend | Official GitHub release URL pinned in `data/catalog.json` | **Yes**, after you confirm | MIT; archive + per-file SHA-256 pinned at review |
| Intel PresentMon 2.6.0 | Benchmarks | Official GitHub release, pinned | **Yes**, on first benchmark | MIT |
| `nvngx_dlssnr.dll` (NVIDIA DLSS 5 Neural Rendering model) | both backends | **Your own NVIDIA driver installation** (and other local copies, see below) | **Import yes**, download **no** | Proprietary NVIDIA file; no official stand-alone download exists |
| `nvngx_dlss.dll` (NVIDIA DLSS Super Resolution) | Feeder; OptiScaler only for games without DLSS | NVIDIA-signed copies already on your PC (other DLSS games, NGX store) | **Import yes**, download **no** | NVIDIA SDK licence forbids stand-alone distribution |
| ReShade with add-on support | Feeder | reshade.me (you install it) | **No** (guided link) | BSD-3-Clause, but no reviewed + pinned build in the catalog yet |
| Neural add-on (Deep Fried Chicken / renodx-dlss5) | Feeder | Discord (you install it) | **No** (guided link) | Distributed through expiring Discord links; cannot be reviewed or pinned |
| LumeniteFX / reshade-shaders | Feeder (motion vectors) | GitHub (you install them) | **No** | Licences/releases not reviewed yet |

## NVIDIA runtimes — the important one

### Facts

* `nvngx_dlssnr.dll` "ships in driver packages" (Skynizz/optiscaler-dlss5, `OptiScaler/dlssnr/README.md`).
  Both upstream projects state they do **not** redistribute it.
* NVIDIA's official DLSS SDK (github.com/NVIDIA/DLSS) ships `nvngx_dlss.dll`, `nvngx_dlssd.dll` and
  `nvngx_dlssg.dll` in `lib/Windows_x86_64/rel/` — **not** `nvngx_dlssnr.dll` (checked 2026-10-20: 404).
* The NVIDIA RTX SDK licence (DLSS SDK `LICENSE.txt`) lets *developers* distribute SDK files "as incorporated
  in object code format into a software application" and says "you may not distribute or sublicense the SDK
  as a stand-alone product". AKENO is not the application those files belong to, and AKENO is GPL — so it
  neither bundles these DLLs nor downloads them for you.
* DLSS5-Feeder README: NVIDIA's **signed** `nvngx_dlssnr.dll` 310.8.0 creates the neural feature (NGX feature
  18) on **RTX 50 only**; on RTX 20/30/40 it fails with `feature 18 create failed with 0xbad00001` (issue #131).
  The route the community uses for older GPUs is a **modified** NVIDIA DLL from Discord.

### What AKENO does (lawful and automatic where possible)

"Resolve Dependencies" searches, read-only, these local places for a copy that is already on your PC:

| Location | Why it is searched |
|---|---|
| `C:\Windows\System32\DriverStore\FileRepository\nv*.inf_amd64_*\` (3 levels) | Where Windows stages the NVIDIA display driver package |
| `C:\Windows\System32\` | Files the NVIDIA driver places system-wide |
| `C:\ProgramData\NVIDIA\NGX\` (incl. renamed `*.bin` model files) | NVIDIA's NGX model store, updated by the driver / NVIDIA App |
| `C:\NVIDIA\DisplayDriver\` (`*.dl_` expanded with Windows' own `expand.exe`) | Default extraction folder of NVIDIA's driver installer |
| Folders of your installed games where AKENO's analyzer saw an NVIDIA runtime | Games ship `nvngx_dlss.dll` |
| `%LOCALAPPDATA%\RHI\DLSS-NR` | Documented by Skynizz's installer (RHI places a copy there) |
| `%USERPROFILE%\Downloads` (2 levels) | Where people put files they fetched themselves |

Every candidate — wherever it was found — must:

1. be a valid 64-bit PE file,
2. establish the intended runtime family through coherent PE identity and NGX exports (including legitimate NVIDIA CL build metadata); conflicting renamed libraries are refused,
3. carry a valid Authenticode signature from NVIDIA (checked with `WinVerifyTrust`).

A modified NVIDIA DLL fails step 3 and is refused, even if it would make Neural Rendering start on an older
GPU, because installing it means bypassing NVIDIA's code signature. AKENO never patches, downloads or
substitutes NVIDIA binaries. Verified copies are copied into `%LOCALAPPDATA%\AKENO\runtimes\` (re-checked
right before the import) and installed into the game from there, with backup and rollback.

### When nothing is found

The wizard says exactly that and offers the shortest lawful path:

* `nvngx_dlssnr.dll`: install/update the NVIDIA display driver (button opens nvidia.com/drivers), restart,
  click *Resolve Dependencies* again — or *Import file…* (still signature-checked).
* `nvngx_dlss.dll`: install/update any game that uses DLSS, rescan the library, resolve again — or import.

AKENO does not claim the installation is automatic when this file cannot be obtained.

## Open-source packages

The resolver downloads only catalog entries whose trust is `verified`: an HTTPS URL on an allow-listed
GitHub host and an archive SHA-256 recorded by the maintainers from the official release at review time
(not taken from the download source at install time), plus the SHA-256 of every file AKENO extracts.
Redirects are re-checked against the allow-list, sizes are limited, archives are checked for path
traversal, symlinks and zip bombs, and bundled scripts are never run. A corrupt or tampered download is
discarded and reported; nothing in the game folder is touched until the transactional install.

Adding or updating a package: download the official release, review its contents, pin the archive hash
and the hash of every extracted file in `akeno/data/catalog.json`, run the tests, document it here.

## Not automated, on purpose

* **ReShade**: BSD-3-Clause would allow it, but reshade.me could not be reached from the review
  environment to record a reviewed hash. Until a build is pinned in the catalog, AKENO links to reshade.me.
* **Neural add-ons for DLSS5-Feeder** and **patched NVIDIA DLLs**: distributed through Discord, not
  reviewable; patched NVIDIA DLLs would also defeat NVIDIA's code signature.
* **Vulkan layer registration** for the Feeder (machine-wide registry change needing administrator rights).
* Anything that touches anti-cheat, DRM, NVIDIA authentication or licensing.

## v1.2 identity/evidence update

See [RTX_RUNTIME_REVIEW.md](RTX_RUNTIME_REVIEW.md): displayed version is not build identity; NVIDIA CL metadata is not itself a reason to reject a legitimate signed runtime. Exact hash, family and actual signer matter. Local copying still requires appropriate permission; Authenticode does not establish a redistribution license. No new proprietary download source was added.
