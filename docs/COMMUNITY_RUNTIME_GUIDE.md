# Community Runtime Manager — local review and controlled experiments

Open **Secure Install → Community Runtime Manager** (DE: **Sichere Installation → Community-Laufzeitverwaltung**). The original signed-runtime dependency workflow and FiveM wizard remain available. Runtime import does not install anything into a game.

## Find the missing Neural Rendering model

Use **Find local runtimes** first. AKENO reuses bounded searches in DriverStore, System32, both NGX stores, extracted driver packages, known game folders, RHI/Swapper caches and Downloads. Driver updates do not guarantee the model exists. `nvngx_dlss.dll` is SR; `nvngx.dll_dlssnr.dll` is the open-source forwarder; neither satisfies `nvngx_dlssnr.dll` identity. A selected renamed `.bin` is inspected by PE identity, never its filename alone.

**Select local runtime → Inspect runtime** shows original PE filename, effective family, SHA-256, version, architecture, actual signer/signature, exports, declared source, review targets and limitations. Local paths can identify you; inspect exports before sharing reports. AKENO does not obtain community/model archives for you. Use only locally acquired files you are entitled to use.

## Four categories

- **A Official NVIDIA runtime — Signature verified:** valid exact NVIDIA signer, x64 DLL, correct runtime identity/NGX exports and stable hash. This authenticates the file, not your game's NR support. Existing official import checks are unchanged.
- **B Experimental community runtime — Not NVIDIA-signed:** complete explicitly selected local review plus exact DLL hash/version, architecture/exports, source/release/commit, security/licensing/build evidence and limitations. Windows signature inspection must have run. A broken/absent NVIDIA signature cannot become category A; a non-NVIDIA valid signature is not NVIDIA authenticity.
- **C Unverified — Import requires further review:** structurally plausible but unsigned/unreviewed or signature unavailable. Not automatically imported/deployed. Unsigned does not itself mean malicious.
- **D Rejected:** corruption, architecture/identity conflicts, declared pin mismatch, denied exact hashes, installer/service payload exports, links, scripts or archives. Rejection reports the reason.

All manager imports use a separate content-addressed cache. They never enter the official runtime table or satisfy another game's global dependency lookup. The selected game experiment explicitly chooses its imported hash.

## Review record

After inspection, **Save review template** writes JSON with the actual hash/version and incomplete review fields. It deliberately cannot authorize import until completed. Have a competent reviewer inspect the source and binary provenance, modifications/security implications, CUDA/NGX/dependencies, licence/local-use permission, build instructions and known limitations. Record `schema: 1`, exact `sha256`, PE `version`, GitHub `repository`/same-repository tagged `release`, 40-character `upstream_commit`, `reviewer`, ISO `reviewed_at`, `modifications`, `build_instructions`, `security_review`, `license_review`, `local_use_permitted: true`, a nonempty `limitations` list and explicit `targets`.

Each target contains `generation` (RTX 20/30/40/50), exact `driver` (e.g. 617.42), `backend` (`optiscaler_nr` or `dlss5_feeder`), `api` (`d3d11`, `d3d12`, `vulkan`), `status` (`experimental` or `incompatible`) and concrete `evidence`. Do not turn an unsupported claim into evidence. Experimental means eligible for a test, not known working. The review's DLL hash is different from a release ZIP hash. No RHI candidate is pre-approved; see [source review](COMMUNITY_RUNTIME_REVIEW.md).

**Load review record**, inspect again, check local-use/risk consent, then **Import selected runtime**. A metadata/hash mismatch or incomplete review stops import. Review-file provenance is a user attestation, not a signed maintainer catalog. If you cannot substantiate the fields, keep category C and export diagnostics instead. The app cannot prove a hash-matched DLL malware-free or infer a licence from a GitHub upload.

## Offline dry-run, install and restore

Select the actual GPU/game in the main launcher, open the manager, select the imported runtime, then choose a backend/profile. Start with Performance and measure; 16 GB is not a memory-fit guarantee. Confirm a legitimate offline single-player session and experimental deployment. **Review dry-run and dependencies** stages only the existing pinned open-source package, checks every required member, dependencies, API/architecture, anti-cheat records, game location, unmanaged files and exactly matching runtime review target. It does not change game files. Download consent applies to the existing reviewed backend package, not proprietary models.

**Backup and install experiment** rechecks consent, file/package hashes, target, renderer and selected hardware metadata. It uses the existing transaction installer to verify backups before writes and to roll back failed writes/post-copy validation. No third-party script is executed. Existing unmanaged runtimes/configs/hooks block rather than being overwritten. The game must be closed. Files remain confined to the analyzed executable directory. A hostile concurrent administrator or changing multiplayer session after installation is outside this local preflight's protection; do not join multiplayer with the experiment installed.

**Restore experiment** reverses the recorded transaction and preserves conflicts for review. The existing Backups page can also restore/export transaction logs. A failed/incomplete rollback is reported, never called successful. No FiveM override exists. Test a suitable offline backend before any separate FiveM recipe review.

## Observe, verify, benchmark

After installing, start a **fresh observation before the backend's NR creation**, then launch the game normally (or reinitialize through supported backend controls). **Check execution evidence** separates installed, loaded, created, evaluated and attributed working. Files, selected executable and log-prefix cursors are pinned; old/rotated/changed/cross-stream logs do not prove success. No successful marker is invented, and backends without explicit successful evaluation logs remain unverified.

The report exports GPU/driver, runtime identity/hash/review, backend/transaction/log, game/renderer, observation, neural events/errors and existing benchmark sessions. Installed or absence of errors is never green NR success. A live process path plus fresh creation/evaluation binds ordinary logs to the installation; it is not cryptographic proof of GPU instructions. Process exit by itself is not a crash diagnosis. A logged failed creation/evaluation reports failure.

Use the existing PresentMon Benchmark page for baseline/enabled capture, average FPS, frame times, 1% lows and stutter. Match exact GPU/driver/resolution/runtime/backend settings; other sessions are historical context, not proof of NR performance. VRAM and stability must be measured independently when the capture source lacks them. No FPS improvement, memory fit, clean antivirus status or hardware success has been fabricated.
