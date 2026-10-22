# Current development status — v1.5

The current branch adds a shared pipeline resolver, controlled native/Feeder plans, official component swaps, isolated self-test, source counter instrumentation, crash recovery, permission-safe performance capture and local A/B reports. **Physical RTX5090/4080/game acceptance remains pending.** RenoDX NR and legacy bridge deployment, programmatic driver overrides and a redistributable diagnostic binary package remain incomplete. See [AKENO_V1_5_IMPLEMENTATION.md](AKENO_V1_5_IMPLEMENTATION.md), [AKENO_V1_5_UPSTREAM_RESEARCH.md](AKENO_V1_5_UPSTREAM_RESEARCH.md) and [AKENO_V1_5_HARDWARE_TESTS.md](AKENO_V1_5_HARDWARE_TESTS.md).

The record below describes earlier versions and is retained as history.

# Project status — v1.3 community runtime integration

v1.3 adds a separate Community Runtime Manager, explicit local review records, four trust categories and controlled offline-only dry-run/install/rollback using existing adapters. No RHI model is pre-approved and no RTX 4080/5090 NR execution has been established here. See COMMUNITY_RUNTIME_PROGRESS.md and COMMUNITY_RUNTIME_GUIDE.md; historic signed-only limitations below remain applicable to the original official/FiveM workflows, not this separate local review path.

**v1.2 update:** FiveM is integrated into Library and Secure Install with persistent server profiles, read-only diagnostics and separate execution levels. NVIDIA CL metadata is accepted only with coherent runtime/export identity and valid NVIDIA signer verification. Exact build hashes matter; newer signed runtimes are not categorically rejected by GPU generation. See [INTEGRATION_PROGRESS.md](INTEGRATION_PROGRESS.md) for current test/build results and [RTX_RUNTIME_REVIEW.md](RTX_RUNTIME_REVIEW.md) for evidence limits. The original v1.1 capability record below is retained as history; its old literal-OriginalFilename and version-only assumptions are superseded by the v1.2 review.

This page states exactly what is implemented, what was verified and how, and what has **not** been
verified. It is kept honest on purpose: please update it when you verify something new.

## Implemented

| Area | State |
|---|---|
| Native Windows desktop app (PySide6), 8 working pages, EN/DE, persistent settings | Implemented |
| **v1.1** AKENO design: bundled artwork (header, recommendation, sidebar, GPU illustration, logo, icons) behind data-driven widgets; design-token theme; animated profile rows; premium action buttons | Implemented; asset loading and DPI scaling unit-tested |
| **v1.1** Layout: dashboard fits a maximised 1920x1080 window; header/sidebar density levels for short windows; action buttons pinned below the scroll area; ultrawide content capped and centred | Implemented; regression-tested at 1920x1009, 1280x680 (150 %) and 3440x1400 |
| Hardware detection: DXGI adapters + D3D12 feature level + UMD driver version, display-class registry (exact VRAM, driver date), NVML driver version, displays, OS/CPU/RAM/HAGS | Implemented |
| GPU identification by PCI device ID (Turing/Ampere/Ada/Blackwell, GTX-without-Tensor-Cores), multi-GPU, unsupported vendors | Implemented, unit-tested |
| **v1.1** Support report per GPU (official vs. community status, backend, missing dependencies, limitations, recommendations) and DLSS technology matrix (SR / RR / FG / MFG / NR for RTX 20–50) | Implemented, unit-tested |
| **v1.1** On-PC verification of Neural Rendering from backend logs next to the game (only explicit success/failure lines written after the install count) | Implemented, unit-tested with synthetic logs |
| Game discovery: Steam (all libraries), Epic, GOG, Ubisoft Connect, EA app, Xbox/MS Store, manual folders | Implemented, fixture-tested |
| Game analysis: main executable scoring, PE imports → API, architecture, DLSS/FSR/XeSS files, existing mods, anti-cheat files, running state | Implemented, fixture-tested |
| Compatibility database with evidence + engine (5 statuses, confidence); **v1.1** `nr_runtimes` evidence (runtime version × GPU generation) | Implemented |
| Recommendation engine (measured → estimated → hardware-only, can recommend "disabled") | Implemented, unit-tested |
| **v1.1** NVIDIA runtime discovery: DriverStore (all `nv*.inf` packages, sub-folders), System32, NGX model store incl. renamed `*.bin`, extracted driver packages (`*.dl_` via `expand.exe`), library games, RHI folder, Downloads; identity + NVIDIA signature required; modified/renamed files refused | Implemented; rules unit-tested on Linux with an injected signature verifier |
| **v1.1** Dependency resolver + "Resolve Dependencies": plan, consent list, local import, verified package download with progress/cancel, offline/corrupt handling, guided links | Implemented, unit-tested end to end in temporary folders |
| **v1.1** Install wizard (System / Game / Dependencies / Installation), exact block reasons, never shows planned steps as passed | Implemented, UI-tested offscreen |
| OptiScaler DLSS 5 adapter (Skynizz 1.3.0, pinned) — automated install/configure/restore | Implemented; end-to-end tested in temporary folders with a synthetic package |
| DLSS5-Feeder adapter (1.19.0, pinned) — installs feeder files once prerequisites exist | Implemented; ReShade and the neural add-on stay guided |
| Secure download manager and safe archive extraction | Implemented, unit-tested incl. malicious archives |
| Transactional installer, verified backups, rollback, restore chain, snapshots, pruning | Implemented, tested incl. injected failures and repeated installs |
| Benchmarks via PresentMon 2.6.0 (pinned), CSV import, statistics, graphs, export | Implemented; statistics and CSV parsing tested |
| **v1.1** Complete German UI incl. dynamic messages (templates with values) | Implemented; coverage + placeholder tests |
| PyInstaller build, Inno Setup per-user installer, portable ZIP, SHA-256 sums, CI on Windows + Linux | Implemented |

## Verification performed

* **Automated tests: 121** (v1.0 had 74). They pass on Linux (Python 3.13, PySide6 6.8.1, offscreen Qt) in
  the development container, and the CI workflow runs the same suite on `windows-2022` (Python 3.12) and
  `ubuntu-24.04`. New in 1.1: runtime discovery rules, dependency resolver (missing runtime, local import,
  offline, unavailable package, corrupt download, cancel, modified runtime), assets (bundling, frozen
  layout, DPI 100–200 %, fallback), layout (1920x1080 fit, 150 % reachability, ultrawide), wizard, support
  matrix and log verification, German rendering of dynamic messages, and an end-to-end 150 % render.
* **Windows CI** (no NVIDIA GPU on the runner): tests, smoke test from source and from the packaged
  `AKENO.exe` (real DXGI/registry/process code and the real runtime search over the runner's DriverStore),
  a check that all 19 design assets are inside the package, a normal (non-smoke) start of the packaged
  EXE that must keep running, screenshots at 100 % and 150 %, installer, portable ZIP and checksums.
  See the latest *Build and test* run for the actual result of each step.
* **Package review:** unchanged from v1.0 (OptiScaler DLSS 5 1.3.0, DLSS5-Feeder 1.19.0, PresentMon 2.6.0
  pinned on 2026-10-20). Not re-downloaded in this session (github.com release downloads were not reachable
  from the development environment); the pinned hashes are enforced at download time.
* **Licensing review** of NVIDIA runtimes and other dependencies: see `docs/DEPENDENCIES.md`.
* **Screenshots** in `docs/screenshots/` are real renders of this application with **test-fixture data**
  (synthetic RTX 5090 system); they show the UI, not a real PC.

## Not verified (needs a real Windows PC with an NVIDIA RTX GPU)

* Hardware detection on real RTX 20/30/40/50 systems and multi-GPU laptops (the owner's RTX 5090 32 GB
  was reported as detected correctly by v1.0, but that is not a test record).
* **Where current NVIDIA drivers actually place `nvngx_dlssnr.dll`** (DriverStore root, a sub-folder, the NGX
  store, or not at all) and whether its PE `OriginalFilename` is exactly `nvngx_dlssnr.dll`. Discovery is
  built from the upstream statement "ships in driver packages" and documented NVIDIA locations; if a driver
  names the file differently AKENO refuses it and says so.
* Authenticode verification of a real NVIDIA runtime (signature checks only run on Windows).
* A complete real installation of OptiScaler DLSS 5 into a real game, the game starting with it, and Neural
  Rendering actually running — on any GPU generation. AKENO's log-based verification will report this once
  someone plays with the mod installed.
* The exact log lines the Skynizz OptiScaler build writes for the neural model (AKENO looks for the markers
  documented by DLSS5-Feeder; an OptiScaler-only install may need extra markers).
* DLSS5-Feeder in a real game; PresentMon capture against a real game (incl. UAC fallback).
* Launcher discovery against real Epic/GOG/Ubisoft/EA/Xbox installations (fixtures only).
* The Inno Setup installer/uninstaller on clean Windows 10 and 11 machines; real 125/150/175 % Windows
  scaling on physical displays (tested with Qt's scale factor offscreen).

## Known limitations

* RTX 20/30/40: upstream documents that NVIDIA's *signed* runtime 310.8.0 refuses the neural feature. AKENO
  refuses modified NVIDIA DLLs, so on those GPUs Neural Rendering is expected not to start unless a newer
  signed NVIDIA runtime adds support. AKENO shows this before installing.
* `nvngx_dlssnr.dll` cannot be downloaded by AKENO — there is no official stand-alone source. If your driver
  does not contain it, the only lawful path is a driver update (or importing your own signed copy).
* Only the reviewed package versions in `catalog.json` can be installed; new upstream releases need a
  catalog update (review + new pinned hashes).
* ReShade, the DLSS5-Feeder neural add-on, motion-vector shaders and Vulkan layers are guided, not automated.
* Builds are not code-signed. The design bitmaps were cut from generated mock-ups and have only their
  original detail (they are shown near their native size).
