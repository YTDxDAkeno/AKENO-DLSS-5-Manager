# AKENO v1.5 upstream research

Source review: 2026-10-22. Base: `60d7583`. All 19 upstream HEAD trees were read-only shallow cloned; only source/text was retrieved. No upstream installer, PowerShell script, or binary was executed. Release and issue pages were inspected separately from the source revision. No copied manager implementation is introduced. AKENO itself is GPL-3.0. Source licenses are distinct from binary redistribution rights.

## beeradmoore/dlss-swapper

- Source: [`53b1ecc9d9ae59a23bb3e6194a55ba6243426d9c`](https://github.com/beeradmoore/dlss-swapper/tree/53b1ecc9d9ae59a23bb3e6194a55ba6243426d9c). License: GPL-3.0.
- Inspected files: `src/Data/DLLManager.cs`, `src/Data/Game.cs`, `src/Data/KnownDLLs.cs`, `src/Data/BattleNet/BattleNetLibrary.cs`.
- Release-page tags observed: `v1.2.7.0`, `v1.2.6.1`, `v1.2.5.0`. Issue index: https://github.com/beeradmoore/dlss-swapper/issues (an empty index is not proof of no defects).
- Test inventory: no dedicated test files identified in the inspected tree. Upstream tests were inspected, not executed.
- Decision: Hash-indexed DLL-family manifests (`DLLManager`, `KnownDLLs`), launcher-specific library classes and batch results. Keep AKENO hash-pinned catalog and add an independent family inventory; never infer integration from copied DLLs. GPL source is compatible in principle with AKENO GPL-3.0; no manager code copied.

## Recol/DLSS-Updater

- Source: [`1404e83e1779f29dedfcdf20a168deadb74f7394`](https://github.com/Recol/DLSS-Updater/tree/1404e83e1779f29dedfcdf20a168deadb74f7394). License: AGPL-3.0.
- Inspected files: `dlss_updater/backup_manager.py`, `dlss_updater/dll_repository.py`, `tests/test_restore_group.py`, `tests/test_orphaned_backups.py`.
- Release-page tags observed: `V5.1.2`, `V5.1.1`, `V5.1.0`. Issue index: https://github.com/Recol/DLSS-Updater/issues (an empty index is not proof of no defects).
- Test inventory: `tests/__init__.py`, `tests/test_bulk_selection.py`, `tests/test_db_timestamps.py`. Upstream tests were inspected, not executed.
- Decision: Backup records distinguish added versus replaced files; restore-group/orphan regressions and file-in-use checks. AKENO already has verified per-file backups and supersession chains. Implement independent crash recovery preflight and exact replace ownership; do not import AGPL Python implementation into the GPL app.

## handsomefox/dlss-updater

- Source: [`6faf1eb46565d9eadb35d0af5ed78f3d5fa11833`](https://github.com/handsomefox/dlss-updater/tree/6faf1eb46565d9eadb35d0af5ed78f3d5fa11833). License: MIT.
- Inspected files: `crates/dlss-core/src/workflow.rs`, `crates/dlss-platform/src/windows.rs`, `crates/dlss-core/src/game_risk.rs`.
- Release-page tags observed: `v1.3.5`, `v1.3.4`, `v1.3.3`. Issue index: https://github.com/handsomefox/dlss-updater/issues (an empty index is not proof of no defects).
- Test inventory: no dedicated test files identified in the inspected tree. Upstream tests were inspected, not executed.
- Decision: Rust workflow plans strict upgrades separately from target/restore profiles and stores immutable content-addressed backups. Adopt an independent plan/diff with original-hash preconditions, retain AKENO transactions. MIT source not copied.

## xt0n1-t3ch/DLSSync

- Source: [`903db98d89db789df651f6faef8b889d2af8b92b`](https://github.com/xt0n1-t3ch/DLSSync/tree/903db98d89db789df651f6faef8b889d2af8b92b). License: Apache-2.0.
- Inspected files: `crates/backup-store/src/lib.rs`, `crates/dlssync-application/tests/recovery_acceptance.rs`, `crates/pe-version/src/identity.rs`.
- Release-page tags observed: `v1.7.0`, `nexus-build-1.6.9`, `v1.6.9`. Issue index: https://github.com/xt0n1-t3ch/DLSSync/issues (an empty index is not proof of no defects).
- Test inventory: `crates/dll-catalog/tests/download_resilience.rs`, `crates/dll-catalog/tests/live_endpoint.rs`, `crates/dlssync-application/tests/cli_parity_execution.rs`. Upstream tests were inspected, not executed.
- Decision: Backup entries bind game/family/original path/hash; recovery acceptance tests distinguish incomplete operations. Preserve per-file journal ownership, add interrupted-state recovery. Apache-2.0 source not copied.

## emoose/DLSSTweaks

- Source: [`1d2fddbe3d1e8f403f795afc17e7db239a83f6a2`](https://github.com/emoose/DLSSTweaks/tree/1d2fddbe3d1e8f403f795afc17e7db239a83f6a2). License: MIT.
- Inspected files: `src/ProxyNvngx.cpp`, `src/module_hooks/nvngx_dlss.cpp`, `dlsstweaks.ini`.
- Release-page tags observed: `0.310.5.0`, `0.310.2.1`, `0.310.0.0`. Issue index: https://github.com/emoose/DLSSTweaks/issues (an empty index is not proof of no defects).
- Test inventory: no dedicated test files identified in the inspected tree. Upstream tests were inspected, not executed.
- Decision: ForceDLAA and quality/preset ratios apply to existing DLSS, not NR. Some nvngx deployment instructions require a signature-policy registry override. Do not adopt that mode. Expose a safe per-application settings guide independent of NR; no registry/signature disable.

## RankFTW/RHI

- Source: [`b0570336431bfce265e0f6dc19156ea751e404f7`](https://github.com/RankFTW/RHI/tree/b0570336431bfce265e0f6dc19156ea751e404f7). License: GPL-3.0.
- Inspected files: `RenoDXCommander/Services/DlssStreamlineService.Detection.cs`, `RenoDXCommander/Services/DlssStreamlineService.Swap.cs`, `RenoDXCommander/Services/GameDetectionService.cs`, `RenoDXCommander/Services/GameDetectionService.Platform.cs`.
- Release-page tags observed: `RHI-2.8.7`, `RHI-2.8.6`, `RHI-2.8.5`. Issue index: https://github.com/RankFTW/RHI/issues (an empty index is not proof of no defects).
- Test inventory: no dedicated test files identified in the inspected tree. Upstream tests were inspected, not executed.
- Decision: Exact-path override detection avoids recursive scans and separates DLSS from Streamline folders; fallback heuristic prefers deeper game copies over OptiScaler copies. Swap service keeps per-DLL backups. Implement independent bounded inventory, explicit active-path provenance and whole-set Streamline checks; presence alone remains unresolved.

## liongooder/DLSS-5-Master

- Source: [`d05872a74ed148b16a6f415694325f78c8a2553a`](https://github.com/liongooder/DLSS-5-Master/tree/d05872a74ed148b16a6f415694325f78c8a2553a). License: MIT.
- Inspected files: `src/DLSS5Master/Core/Installers.cs`.
- Release-page tags observed: `v0.6.1`, `v0.6.0`, `v0.5.1`. Issue index: https://github.com/liongooder/DLSS-5-Master/issues (an empty index is not proof of no defects).
- Test inventory: no dedicated test files identified in the inspected tree. Upstream tests were inspected, not executed.
- Decision: Installers declares Native/Multipass/Feeder/OptiScaler routes, per-API/bitness route options, and explicitly declines unimplemented Vulkan/legacy wrappers. Adopt independent route records; no blanket claim that every listed proxy loads.

## Sauravk-G/DLSS5-Anywhere

- Source: [`0eb3e0655c0b832393b827b97bed11bb898b496a`](https://github.com/Sauravk-G/DLSS5-Anywhere/tree/0eb3e0655c0b832393b827b97bed11bb898b496a). License: MIT.
- Inspected files: `dlss5_anywhere/core/installer.py`, `dlss5_anywhere/core/strategy.py`, `dlss5_anywhere/core/detector.py`, `tests/test_neural_consumer_choice.py`, `tests/test_installer.py`.
- Release-page tags observed: no tagged release surfaced by public page. Issue index: https://github.com/Sauravk-G/DLSS5-Anywhere/issues (an empty index is not proof of no defects).
- Test inventory: `tests/test_addon_isolation.py`, `tests/test_anti_cheat_false_positives.py`, `tests/test_cli_profile.py`. Upstream tests were inspected, not executed.
- Decision: Consumer isolation moves overlapping add-ons into backups; tests exercise x86 host64 placement and mutually exclusive RenoDX/Chicken selections. Adopt explicit consumer identity and owned reversible replacement. Foreign mods remain blockers; no automatic deletion. Strategy support is not hardware proof.

## Hex4np/dlss5-swapper

- Source: [`ccb67f4bc92cb5da1d25416dd6b338e12fa432c5`](https://github.com/Hex4np/dlss5-swapper/tree/ccb67f4bc92cb5da1d25416dd6b338e12fa432c5). License: MIT.
- Inspected files: `src/shared/install-routes.js`, `src/core/install-guards.js`, `test/runtime-prerequisites.test.js`, `test/compatibility-regressions.test.js`.
- Release-page tags observed: no tagged release surfaced by public page. Issue index: https://github.com/Hex4np/dlss5-swapper/issues (an empty index is not proof of no defects).
- Test inventory: `scripts/test-feeder-payload.js`, `scripts/test-game-filters-ui.js`, `scripts/test-optiscaler-payload.js`. Upstream tests were inspected, not executed.
- Decision: Shared route functions are used by UI and install guards; nativeDlssPresent excludes files in the tool added-file manifest. Reimplement central policy and managed-file provenance. Runtime-prerequisite and compatibility regression tests motivate exact API/runtime cases.

## IsGaraa/DLSS5

- Source: [`18df6eba676e36e77655d7fca0910d0d0ac75aa9`](https://github.com/IsGaraa/DLSS5/tree/18df6eba676e36e77655d7fca0910d0d0ac75aa9). License: MIT wrapper / proprietary bundled files.
- Inspected files: `DLSS5-UI/main.js`, `DLSS5-UI/www/app.js`.
- Release-page tags observed: `v3.0`, `v2.0`, `DLSS5`. Issue index: https://github.com/IsGaraa/DLSS5/issues (an empty index is not proof of no defects).
- Test inventory: no dedicated test files identified in the inspected tree. Upstream tests were inspected, not executed.
- Decision: Electron UI delegates to PowerShell with execution-policy bypass and ships a kit containing NVIDIA DLLs. Do not reuse its launcher or bundled kit. Retain upstream identity as research only; MIT wrapper license does not license NVIDIA redistribution.

## machiavelli31/DLSS-AiO-Swapper

- Source: [`53384affd72453a396f1377e4e8f4b90441b512d`](https://github.com/machiavelli31/DLSS-AiO-Swapper/tree/53384affd72453a396f1377e4e8f4b90441b512d). License: MIT.
- Inspected files: `app/src/shared/install-routes.js`, `app/src/core/install-guards.js`, `app/src/core/file-journal.js`.
- Release-page tags observed: `v3.0.8`, `v3.0.6`, `v3.0.7`. Issue index: https://github.com/machiavelli31/DLSS-AiO-Swapper/issues (an empty index is not proof of no defects).
- Test inventory: no dedicated test files identified in the inspected tree. Upstream tests were inspected, not executed.
- Decision: Route/guard split and file-journal captures with validate-before-recover/atomic JSON. Independent crash recovery and shared resolver; no Electron overlay injection or foreign-mod cleanup copied.

## Skynizz/Prism

- Source: [`69f7e02b4dd855fb119f2e87f356dd0edb4449cd`](https://github.com/Skynizz/Prism/tree/69f7e02b4dd855fb119f2e87f356dd0edb4449cd). License: GPL-3.0.
- Inspected files: `Services/CompatibilityMatrix.cs`, `Services/NeuralRuntimePins.cs`, `Services/FileTransaction.cs`.
- Release-page tags observed: `v1.3.0`, `v1.2.0`, `v1.1.1`. Issue index: https://github.com/Skynizz/Prism/issues (an empty index is not proof of no defects).
- Test inventory: no dedicated test files identified in the inspected tree. Upstream tests were inspected, not executed.
- Decision: CompatibilityMatrix binds exact DLSS-G/Streamline combinations and warns about a reported mixed-addon slowdown. Its MFG unlock claims are addon-specific; do not turn them into official RTX40 MFG support or generic NR eligibility. Unknown combinations remain unknown.

## optiscaler/OptiScaler

- Source: [`e0a4859454b35a60530d1e098d605efd44193582`](https://github.com/optiscaler/OptiScaler/tree/e0a4859454b35a60530d1e098d605efd44193582). License: GPL-3.0.
- Inspected files: `Changelog.md`, `OptiScaler/Config.cpp`.
- Release-page tags observed: `v0.9.4`, `v0.9.3`, `v0.9.2`. Issue index: https://github.com/optiscaler/OptiScaler/issues (an empty index is not proof of no defects).
- Test inventory: no dedicated test files identified in the inspected tree. Upstream tests were inspected, not executed.
- Decision: Stock Config.cpp selects DLSS/FSR/XeSS per API. Stock OptiScaler is not an NR consumer; model distinct capability from fork/version. Existing AKENO INI adapter retained.

## Dagherbou/OptiScaler_DLSSNR

- Source: [`973761621353b99bee3dc7d4bb27b117fef2644f`](https://github.com/Dagherbou/OptiScaler_DLSSNR/tree/973761621353b99bee3dc7d4bb27b117fef2644f). License: GPL-3.0.
- Inspected files: `Changelog.md`, `OptiScaler/dlssnr/README.md`, `OptiScaler/shaders/dlssnr/DlssNr_Dx12.cpp`.
- Release-page tags observed: `v0.2.0-dlssnr`, `v0.2.0-patch1`, `v0.1.2-dIssnr`. Issue index: https://github.com/Dagherbou/OptiScaler_DLSSNR/issues (an empty index is not proof of no defects).
- Test inventory: no dedicated test files identified in the inspected tree. Upstream tests were inspected, not executed.
- Decision: Fork has feature18 creation, forwarder exports, and runtime calls. Different commit/build must have separate pins; do not substitute it for Skynizz1.3.0. No universal generation claim; no source pasted.

## Skynizz/optiscaler-dlss5

- Source: [`fc702f8ca22f97b01bd6a02f3386bffb3484fc35`](https://github.com/Skynizz/optiscaler-dlss5/tree/fc702f8ca22f97b01bd6a02f3386bffb3484fc35). License: GPL-3.0.
- Inspected files: `Changelog.md`, `deploy/install.py`, `OptiScaler/dlssnr/README.md`, `OptiScaler/shaders/dlssnr/DlssNr_Dx12.cpp`, `OptiScaler/shaders/dlssnr/DlssNr_Dx12.h`, `OptiScaler/dlssnr/DlssNr_Proxy.cpp`.
- Release-page tags observed: `v1.3.0`, `v1.2.1`, `v1.2.0`. Issue index: https://github.com/Skynizz/optiscaler-dlss5/issues (an empty index is not proof of no defects).
- Test inventory: no dedicated test files identified in the inspected tree. Upstream tests were inspected, not executed.
- Decision: install.py selects a free filename but does not prove loader use. DX12 ordinary evaluate success is not logged. Add independently written bounded counters at the existing calls in a pinned source diagnostic patch, not proprietary hooks. Derivative source patch remains GPL, binary distribution requires corresponding source and dependency review.

## jlrouzies-fr/DLSS5-Feeder

- Source: [`16cb1946a8331751b0f89a728b797d895ecdfba1`](https://github.com/jlrouzies-fr/DLSS5-Feeder/tree/16cb1946a8331751b0f89a728b797d895ecdfba1). License: MIT / component-specific notices.
- Inspected files: `tools/Verify-DLSS5Feeder.ps1`, `host/dlss5-feed-host64.cpp`, `src/feed_ngx.h`, `external/deepfried/FEEDER-INTEROP-v1.md`, `CAREFUL_FAKE_MALICIOUS_FEEDER.txt`.
- Release-page tags observed: `v1.19.0`, `v1.18.0-beta.2`, `v1.18.0-beta.1`. Issue index: https://github.com/jlrouzies-fr/DLSS5-Feeder/issues (an empty index is not proof of no defects).
- Test inventory: `tests/vk-present-order.cpp`. Upstream tests were inspected, not executed.
- Decision: Verifier enumerates every versioned add-on, checks full ReShade addon exports and host64 bitness, and warns that bare D3D9 requires a translation layer. RunTest counts 300 DLAA/SR calls and explicitly asks for separate feature18 evidence. Integrate pinned host in isolated workspace; SR count alone is inconclusive NR. Published host modifications and log parsing must retain this separation.

## clshortfuse/renodx

- Source: [`b6b32f47789ff9d0e640a0e0db6c9421b5397f34`](https://github.com/clshortfuse/renodx/tree/b6b32f47789ff9d0e640a0e0db6c9421b5397f34). License: MIT.
- Inspected files: `src/addons/dlssfix/addon.cpp`, `test/api_compatibility/tests.cpp`, `CMakeLists.txt`.
- Release-page tags observed: `snapshot`, `nightly-20261022`, `nightly-20261020`. Issue index: https://github.com/clshortfuse/renodx/issues (an empty index is not proof of no defects).
- Test inventory: `test/api_compatibility/tests.cpp`, `test/descriptor_cache_race/addon.cpp`, `test/descriptor_cache_race/dx12_app.cpp`. Upstream tests were inspected, not executed.
- Decision: MIT addon dlssfix checks early-loaded DLSS/Streamline modules; it is not itself proof of the RenoDX DLSS5 consumer. API compatibility/race tests cover infrastructure. Require a specific verified NR consumer artifact; no generic RenoDX HDR addon accepted as NR.

## artur-graniszewski/DLSS-Enabler

- Source: [`c67224e4ecb8020497b05de26b550e44d815eb59`](https://github.com/artur-graniszewski/DLSS-Enabler/tree/c67224e4ecb8020497b05de26b550e44d815eb59). License: MIT root / component-specific binary restrictions.
- Inspected files: `License (DLSS enabler).txt`, `Readme (DLSS enabler).txt`, `DLLSG mod/LICENSE.txt`.
- Release-page tags observed: `v0.9.4`, `v0.9.3`, `v0.9.2`. Issue index: https://github.com/artur-graniszewski/DLSS-Enabler/issues (an empty index is not proof of no defects).
- Test inventory: no dedicated test files identified in the inspected tree. Upstream tests were inspected, not executed.
- Decision: Root MIT does not cover every bundled binary; additional DLSS Enabler notice reserves Nukem binary rights and warns against multiplayer. Do not download/run the installer or reuse binary bundles. FSR FG fallback must never be labeled DLSS NR.

## NVIDIA-RTX/Streamline

- Source: [`625db07d4bca5abc67d781c42af7e7fb0761f1b6`](https://github.com/NVIDIA-RTX/Streamline/tree/625db07d4bca5abc67d781c42af7e7fb0761f1b6). License: MIT / SDK-specific notices.
- Inspected files: `changelog.txt`, `include/sl_security.h`, `source/core/sl.security/secureLoadLibrary.cpp`, `docs/ProgrammingGuideDLSS_G.md`, `docs/ProgrammingGuideDLSS.md`.
- Release-page tags observed: `v2.14.16`, `v2.14.1`, `v2.12.0`. Issue index: https://github.com/NVIDIA-RTX/Streamline/issues (an empty index is not proof of no defects).
- Test inventory: no dedicated test files identified in the inspected tree. Upstream tests were inspected, not executed.
- Decision: SecureLoadLibrary verifies embedded signatures before loading, with separately licensed NGX/Reflex SDK content. Keep NVIDIA Authenticode verification and exact family/version pins; swap a known matching Streamline set, not arbitrary sl.* DLLs. MIT top-level license does not license proprietary runtime redistribution.

## Integration decisions and limits

The new resolver shares immutable route results with installation. Runtime hashes, consumer/backend versions, architecture/API, driver constraints, proxy load evidence and ownership are independent checks. Existing rollback, large-EXE limit, community opt-in, FiveM policy and incremental sessions are reused. No third-party license is used as a safety certificate. Synthetic tests cover planning/parser/transaction logic; real GPU creation/evaluation cannot be established in CI.

Feeder release `v1.19.0` source commit: `3c09eabcd138a68d144b6cc396a23e6b493eea4d`. The catalog archive/host pins predate this work and are revalidated when used. This is separate from current HEAD. Skynizz source is exactly the existing1.3.0 commit. The diagnostic source patch does not alter runtime bytes, caller checks, signatures, DRM, anti-cheat or global policies.
