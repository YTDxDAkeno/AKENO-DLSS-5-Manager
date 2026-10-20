# Project status — v1.0.0

This page states exactly what is implemented, what was verified and how, and what has **not** been
verified. It is kept honest on purpose: please update it when you verify something new.

## Implemented

| Area | State |
|---|---|
| Native Windows desktop app (PySide6), 8 working pages, EN/DE, persistent settings | Implemented |
| Hardware detection: DXGI adapters + D3D12 feature level + UMD driver version, display-class registry (exact VRAM, driver date), NVML driver version, displays, OS/CPU/RAM/HAGS | Implemented |
| GPU identification by PCI device ID (Turing/Ampere/Ada/Blackwell, GTX-without-Tensor-Cores), multi-GPU, unsupported vendors | Implemented, unit-tested |
| Game discovery: Steam (all libraries), Epic, GOG, Ubisoft Connect, EA app, Xbox/MS Store, manual folders | Implemented, fixture-tested |
| Game analysis: main executable scoring, PE imports → API, architecture, DLSS/FSR/XeSS files, existing mods (ReShade, OptiScaler, ASI loader, DXVK, Special K, neural add-ons, unknown hooks), anti-cheat files, running state | Implemented, fixture-tested |
| Compatibility database with evidence + engine (5 statuses, confidence) | Implemented; 11 game entries, each citing upstream documentation |
| Recommendation engine (measured → estimated → hardware-only, can recommend "disabled") | Implemented, unit-tested |
| OptiScaler DLSS 5 adapter (Skynizz 1.3.0, pinned) — full automated install/configure/restore | Implemented; end-to-end tested in temporary folders with a synthetic package |
| DLSS5-Feeder adapter (1.19.0, pinned) — installs feeder files once prerequisites exist | Implemented; requirement checks tested; ReShade/Vulkan-layer steps are guided, not automated |
| Secure download manager (HTTPS allow-list, redirect checks, pinned SHA-256, size limits, retry, cancel, cache) and safe archive extraction | Implemented, unit-tested incl. malicious archives |
| NVIDIA runtime import from DriverStore / file with Authenticode + NVIDIA signer check | Implemented; signature check only runs on Windows |
| Transactional installer, verified backups, rollback, restore chain, change detection, manual snapshots, pruning | Implemented, tested incl. injected failures |
| Benchmarks via PresentMon 2.6.0 (pinned), CSV import, statistics, graphs, comparison warnings, CSV/JSON export | Implemented; statistics and CSV parsing tested |
| Diagnostics export (scrubbed), rotating logs | Implemented |
| PyInstaller build, Inno Setup per-user installer, portable ZIP, CI on Windows + Linux | Implemented |

## Verification performed

* **Automated tests:** 74 tests (core, security, installer, adapters, compatibility, recommendations,
  benchmarks, settings, i18n coverage, offscreen UI navigation/actions/language switch). They pass on
  Linux in the development container; the CI workflow runs the same suite on `windows-2022`.
* **Windows CI:** smoke test from source and from the packaged `AKENO.exe` on a GitHub Windows runner
  (real DXGI/registry/process code paths; the runner has **no NVIDIA GPU** — it reports the Microsoft
  Basic Render Driver or a virtual adapter), screenshots of every page, installer and ZIP build.
  See the latest *Build and test* run in the Actions tab for the actual result of each step.
* **Package review:** the OptiScaler DLSS 5 1.3.0 zip, DLSS5-Feeder 1.19.0 zip and PresentMon 2.6.0
  executable were downloaded from their official GitHub release URLs on 2026-10-20; archive and
  per-file SHA-256 values were recorded in `catalog.json` and the zip contents were listed. The
  binaries were **not** reverse-engineered or malware-analysed beyond that.
* **Screenshots** in `docs/screenshots/` are real renders of this application, made on Linux with the
  offscreen Qt platform and **test-fixture data** (`tools/fixture_home.py`: a synthetic RTX 3080 system
  and synthetic game folders). They show the UI, not a real PC.

## Not verified (needs a real Windows PC with an NVIDIA RTX GPU)

* Hardware detection output on real RTX 20/30/40/50 systems and multi-GPU laptops (DXGI + NVML values).
* Authenticode verification of a real `nvngx_dlssnr.dll` from an NVIDIA driver, and whether current
  drivers place that file in `DriverStore` at all (upstream says it "ships in driver packages").
* A complete real installation of OptiScaler DLSS 5 into a real game, the game starting with it, and
  Neural Rendering actually running — on any GPU generation.
* DLSS5-Feeder in a real game (requires ReShade with add-ons and a neural add-on).
* PresentMon capture against a real game, including the elevation (UAC) fallback path and "Stop".
* Launcher discovery against real Epic/GOG/Ubisoft/EA/Xbox installations (tested only with fixtures
  built from their documented manifest formats).
* The Inno Setup installer and uninstaller on a clean Windows 10 and Windows 11 machine.
* German texts for dynamic engine messages that contain values (game names, file names) are shown in
  English; all fixed UI texts are translated.

## Known limitations

* RTX 20/30/40: upstream reports that NVIDIA's *signed* runtime may refuse to create the neural
  feature. AKENO refuses modified NVIDIA DLLs, so on those GPUs Neural Rendering may simply not start.
* Only the reviewed package versions in `catalog.json` can be installed; new upstream releases need a
  catalog update (review + new pinned hashes).
* AKENO does not install ReShade, register Vulkan layers or edit NVIDIA Profile Inspector settings.
* Builds are not code-signed.
