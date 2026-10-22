# AKENO 1.4.1: real-test reliability fixes

Base branch: `fix/v1.4-large-game-executables`, commit `e10c938`. Its separate 16-GiB game-file policy and 512-MiB runtime policy are preserved. This release changes diagnostics and measurement interpretation, not the experimental installation eligibility or protection rules.

## Actual evidence supplied by the user

RTX 5090 / driver 617.42 / Windows 11 / Borderlands 4 Steam 1285190 / DX12. Transaction 5 successfully installed community OptiScaler NR 1.3.0 and Bandukids NR 310.8.0.0, SHA-256 `8270b350cd82de5ce89806872cdd6b6a9249b80836b91bbeb3573470744cc206`. The modified signature is not NVIDIA authentication. No independent RTX 5090 runtime evaluation or safety review is established.

No separate `akeno-community-report.json` was available in this workspace; the exact evidence in the bugfix prompt is the source. Its empty fresh neural events, existing ~2.32-MB log cursor, game-not-running state, and six captures predating transaction 5 do not prove NR execution or current-installation performance. No missing log lines are invented.

## Reproduced root causes and functional changes

- Every three-second poll used the ordinary UI busy path. Diagnostics now have a silent single worker, one coalesced pending request, explicit Start/Stop, closed-dialog/generation guards, a monitoring indicator and last-check timestamp. Unchanged status/technical contents do not repaint or reset the scroll position.
- Polls reanalyzed the game and rehashed the EXE/model/artifacts. Session start now verifies full hashes once, compares installed files to transaction pins, and records file identities (device/inode/size/mtime/ctime). Polls check metadata and bounded log anchors, reuse the hashes, and invalidate changed inputs. A new session performs full revalidation. Installation/import/post-copy/restore cryptographic checks remain separate and unchanged. Cached metadata checks are explicitly **not fresh cryptographic verification**.
- Logs previously reread their entire old prefix and appended content. A per-session reader now advances offsets, retains partial lines, bounds reads to 256 KiB per stream and 8 MiB total per poll, and caps log count, events and excerpts. It detects rotation/truncation and avoids reusing earlier-session success. Discovery covers backend-relevant directories, timestamped filenames, and safe in-root `[Log] LogFileName` settings; it does not traverse unrelated game assets or external directories.
- Verification now follows Waiting for game → detected process → backend/NR events → exit/final report. The process identity includes exact executable, PID and OS creation identity where observable. A replacement/reused PID ends the old session; a new verification session gets new cursors and a new ID. Starting after game launch is disclosed. Exit preserves meaningful evidence; an ordinary exit is not a verified crash.
- Read-only Windows Toolhelp module snapshots distinguish installed backend modules from the NR model and ordinary DLSS SR. Access-denied/unobservable modules remain unknown. No injection, anti-cheat bypass, privilege workaround or protection changes are used.
- Reports now show expected proxy, installed file pins, effective small INI contents, transaction/package version, bounded log sizes/offsets/append activity, classified events/error codes, missing stages, process attribution and polling overhead. JSON and support ZIP exports are local and redacted. ZIPs contain only bounded JSON/text; raw recent excerpts require explicit consent and are still redacted. No DLLs or game assets are exported.
- Community classification is `community_experimental`, not `reviewed_community`. Legacy records remain readable and are normalized for exports. Source/hash match, local permission, independent security review and verified GPU/game evaluation remain separate facts. No new expert-review gate is introduced.
- Privacy redaction protects HTTPS URLs before processing filesystem text, handles Windows/UNC/Unix paths, preserves exact hashes and useful process/hash metadata, and supports nested reports. It fixes the drive-pattern match inside `https:`.

## PresentMon methodology

The actual pinned console version is PresentMon 2.6.0. Its [column documentation](https://github.com/GameTechDev/PresentMon/blob/v2.6.0/README-ConsoleApplication.md) distinguishes `MsBetweenAppStart`, `MsBetweenPresents`, `DisplayedTime`, `FrameType`, PID and swapchain. Capture now requests frame-type tracking.

The parser never concatenates different applications/PIDs/swapchains/present runtimes. PID filtering is strict when requested. An optional explicit swapchain is available in the UI; otherwise the dominant stream is selected by accumulated timing coverage, **not the largest number of tiny presents**, and ambiguity is warned. Generated/repeated frames are excluded from application samples and analyzed separately when supported. Rendered FPS, displayed FPS and generated-frame share are reported only from supporting data. Missing frame types, identity or appropriate timing columns remain limitations.

Median FPS is withheld for unvalidated/heterogeneous streams. Where supported, it is derived from median frame time; it is not the median of a pile of reciprocal mixed-stream intervals. Tiny heterogeneous intervals produce a warning rather than a gameplay conclusion. The main legacy statistic is labeled **Selected stream FPS**, and analysis shows reliability/attribution limitations.

Lows use 1000 divided by the mean of the slowest 1%/0.1% frame times (0.1% requires 1000 samples). A spike exceeds `max(2 × median, median + 8 ms, 33.33 ms)`; adjacent spikes form one stutter episode. This is an explicit heuristic, not a crash/quality verdict. Changing this heuristic does not repair ambiguous source streams; those remain warned.

Capture contexts include transaction/runtime/package/version, observation ID, process birth/PID/EXE hash, renderer, GPU/driver, start/end times and user-entered conditions. Context is compared at capture boundaries. Imported CSVs never inherit the current runtime/backend as proven identity. Current installation, current session, historical installation, baseline and unconfirmed captures are separated. All six older example sessions remain historical, not transaction-5 evidence. Baseline is a user label/attestation, not an independently instrumented proof that NR was disabled.

## Upstream evidence and limitations

OptiScaler source was inspected at tag v1.3.0, commit `fc702f8ca22f97b01bd6a02f3386bffb3484fc35`:

- `shaders/dlssnr/DlssNr_Dx12.cpp`: forwarder loading, pass creation, explicit failure codes, supersample evaluation results.
- `dlssnr/DlssNr_Proxy.cpp`: `CreateFeature(18)` failure, feature creation and idempotent NGX re-init.
- `dlssnr/DlssNrFeature_Vk.cpp`: model initialization and feature creation.
- `Config.cpp`: configured log filename and timestamp suffix rules.

NGX success is `1`; a capability probe is not successful creation/evaluation. Generic SR/FG initialization and a future-intent message such as “first evaluated next frame” do not establish evaluation. Critically, the normal DX12 successful Evaluate branch of this version does **not** emit an explicit per-frame success event. Thus no recognized success line can remain inconclusive even if modules load. AKENO does not infer success from missing errors or alter upstream binaries to manufacture evidence. A permitted backend build with an explicit, attributable evaluation trace may be needed for conclusive verification.

## Measured monitoring overhead

A local Linux synthetic test uses a sparse **771-MiB EXE**, **158-MiB model**, and **2.32-MiB preexisting log**. Full session-start hashes run once for each binary; 300 idle polls perform **zero additional full-file hashes and zero log-body reads**. The initial recorded mean idle poll was **0.275 ms**. Small bounded anchor reads and metadata calls still occur. This measures the incremental evidence reader on this cloud filesystem, **not Windows gaming overhead, physical disk bandwidth, GPU execution or FPS impact**. Whole-report timings and read-byte counters are also included in live reports; Windows process/module enumeration adds platform-dependent cost.

## Validation and hardware instructions

Initial full Linux/offscreen suite: **383 passed, 2 skipped**. The Windows-only skipped checks exercise real Authenticode and read-only process APIs. Later focused checks and final Windows build results are recorded in the delivery message/CI artifacts. New regression tests cover idle polling, large-file hash call counts, changed metadata, appended/unchanged/rotated logs, exact process lifetimes, old benchmark separation, multiple streams, FG, timing/stutter math, redaction, support ZIP consent, legacy trust migration, silent coalesced Qt updates and EN/DE screenshots. Existing installer/rollback/supersession, RTX 4080/5090, offline bundles, Borderlands and FiveM tests remain in the full suite.

1. Open the updated build and select Borderlands 4 / RTX 5090 / DX12.
2. Keep the installed transaction; do not overwrite the supplied runtime. Open **Start DLSS 5 Verification Session** before launching the permitted offline game.
3. Wait for process detection. If already running at session start, restart/reinitialize if necessary to capture initialization. Confirm module observations are available; if unavailable, retain that limitation.
4. Inspect log append activity and exact NR events. Empty old-log tails mean unknown execution, not success or an invented backend failure.
5. Optionally enable FPS capture with local PresentMon/ETW permission. Enter version, scene type/workload, resolution, DLSS/FG mode, renderer and settings. Choose the intended swapchain if the report identifies multiple streams.
6. Capture a repeatable gameplay scene. Menus/loading and unknown settings remain uncontrolled. Performance differences do not prove NR image quality.
7. Exit the game, retain the final report and export a support ZIP. Include redacted excerpts only with deliberate consent. Restore through the existing recorded transaction when desired.

Remaining evidence: actual read-only module snapshots and fresh backend logs from this Windows PC, explicit successful Feature 18 evaluation attributed to its selected process/runtime, and a new capture for that exact transaction/session. No RTX 5090, RTX 4080 or FiveM NR success is claimed by CI or synthetic fixtures. FiveM protections and the separate permitted development workflow are preserved.
