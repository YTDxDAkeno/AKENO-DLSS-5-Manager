# AKENO v1.5 implementation and continuation

Branch: `codex/akeno-v1.5-universal-dlss-engine`, based on the remotely confirmed v1.4.1 `60d7583`. Previous branches, releases and PRs are preserved. No merge or force push is part of this work. Version: **1.5.0**.

## What is implemented

The **Rendering pipelines** button on Secure Install opens a graphical workflow with four tabs: route selection, isolated runtime self-test, official DLL management, and real-image A/B comparison. English and German strings and synthetic high-DPI screenshots are covered by tests. The existing Community Runtime Manager remains available for import, observation and restore.

`pipeline/recipes.py` declares separate API/architecture/input/consumer/component/log/success contracts. `resolver.py` returns compatible, experimental, missing prerequisite, incompatible and unsafe states. Existing `diagnostics/session.py` supplies actual execution status separately; neither an eligible route nor a successful install is promoted to execution verified. UI analysis and prepared pipeline installation call this same resolver; official swaps also use its component-management policy. Extra prepare/apply checks validate live bytes, signatures, ownership and running-process state.

| Route | Implemented behavior | Remaining qualification |
|---|---|---|
| A: native DLSS + OptiScaler NR | One reversible prepared plan using the existing pinned Skynizz1.3.0 adapter/forwarder and imported runtime; evidenced proxy selection; process/log verification | Actual native calls and physical NR output still require observation. The exact Borderlands4 profile establishes engine integration, not a successful session. |
| B: native DLSS + ReShade/RenoDX NR | Explicit separate contract, consumer conflict detection and prerequisite reasons | **Not deployable:** the requested public RenoDX repository is not the independently licensed/versioned Discord NR consumer. No unverified consumer binary is substituted. |
| C: Feeder + OptiScaler NR | D3D12/x64 composite plan contains pinned Feeder1.19.0, shader, full-addon ReShade, motion-provider assets, official SR, NR, OptiScaler and forwarder; exact rollback | Requires local component permission and complete shader dependencies. Depth/motion technique configuration and actual consumer execution remain physical acceptance steps. No universal Feeder compatibility is claimed. |
| D: legacy bridges | Explicit x86/helper/API contract and specific missing prerequisite | **Not deployable yet:** no complete reviewed bridge/bitness/dependency set is shipped. Bare D3D9 is not a supported Feeder route; OpenGL/Vulkan transport is not proof of a complete consumer pipeline. |
| E: official DLL swap | NVIDIA Authenticode + exact PE family/version; hash-addressed inventory; previously observed active paths; exact original preconditions; complete matching Streamline sets; multi-game preview/apply | Does not add DLSS/RR/FG/MFG/NR integration. No generation unlock or unsupported MFG flags. |
| F: configuration | Per-application game/NVIDIA app guidance, separate from NR activation | No programmatic driver-profile editor/NVAPI write path. No global signature-policy change. |

These qualifications are intentional missing integrations, not verified working routes. A full “universal engine” hardware acceptance is **not complete**.

## Discovery, dependencies and ownership

- Native support is grounded in the exact Borderlands4 identity/layout or game PE imports. A copied `nvngx_dlss.dll` does not establish native support. Existing file inventory, native support, loaded modules, initialization and evaluation remain separate.
- Proxy resolution follows bounded game-local import chains or eligible process-module observations and selects only a supported unused/owned filename. It does not choose an arbitrary free filename as proof that Windows will load it. The existing OptiScaler adapter also calls this central resolver, so its readiness and installer no longer accept the historical arbitrary-free-filename heuristic.
- Known packages retain the existing pinned download/cache/offline archive mechanism. Public CI contains no NVIDIA runtime. Private manifests require source/license/local-use metadata, exact hashes, architecture/API and exact dependency versions; cycles/missing versions fail before deployment. The private bundle builder also accepts an optional pinned Feeder archive and a validated pipeline manifest/assets, available through the bundled-manifest import picker without network access.
- Full ReShade addon exports are required. Motion-provider includes/assets are bounded and pinned. Generic RenoDX HDR add-ons are never treated as an NR consumer. Consumer detection preserves duplicate/versioned add-ons so two copies are conflicts. Foreign mods are preserved; the application does not silently remove them. Restore an owned previous transaction before switching an incompatible consumer.
- Imported dependencies are private local declarations, **not independent security audits**. License metadata does not itself establish redistribution permission. No proprietary component is added to the public catalog or Git.
- Official components use a separate inventory/cache. Exact PE original/internal/export identity and NVIDIA signature are checked; NR with an invalid signature remains on the community path. DLSS1 and later SR are not interchangeable. Streamline uses a complete per-folder version set rather than the highest version seen on one file.
- Active DLL copies come from an explicit read-only snapshot of the selected executable/PID/birth identity. The EXE hash, file identities and DLL pins bind that observation; game updates invalidate it. Multiple discovered copies are not overwritten blindly. Close the game before preparing an update. Bulk operations preview every selected game and return per-game transaction results.
- Battle.net discovery adds registered Blizzard game roots through bounded Windows uninstall entries. Existing Steam/Epic/GOG/Ubisoft/EA/Xbox/manual/Unreal and FiveM discovery remains; the scanner does not walk entire drives on refresh.

## Self-test and diagnostic source build

`diagnostics/selftest.py` invokes only the catalog-pinned Feeder1.19.0 `host64/dlss5-feed-host64.exe --test`, from a new directory under AKENO's private home. The host archive/member and consumer/runtime bytes are verified. No game files are modified and no multiplayer process is involved. Setup, timeout, cancellation, process identity, module observations, actual host GPU/driver, NGX result, Feature18 evidence, separate SR/NR counts, errors and elapsed time are written to a redacted local JSON result.

The upstream host counts **DLAA/SR** evaluates. A `300/300` line alone yields **inconclusive NR**, not a pass. Exact NR counts require identified consumer evidence. An adapter mismatch or unobserved exact runtime module also prevents a hardware pass. Results are stored against exact device/driver/runtime hash/consumer version/API axes and do not globally change GPU-generation support.

`tools/prepare_nr_diagnostic.py` applies independent counter additions only to Skynizz commit `fc702f8ca22f97b01bd6a02f3386bffb3484fc35`. It wraps existing NR create/evaluate call sites and records actual return results. The driver-proxy path is also counted. It changes no proprietary runtime bytes, caller checks, signatures or protections. Counters saturate, avoid a log line per frame, and publish at most once every five seconds. **Counts are observed lower bounds**; abrupt exit may lose the last partial interval. Successful API returns do not by themselves establish GPU completion or visual quality, and multiple passes/probes are calls rather than distinct displayed frames.

Protocol `AKENO_NR_V1` includes build identity, PID, Windows process birth, monotonic sequence, create/evaluate attempts/success/failure and last result plus the persistent last evaluation error. A later success cannot erase that observed failure code. Creation counts use the backend API's returned feature pointer; precise underlying NGX creation errors remain separate upstream log evidence. AKENO accepts a counter summary only for an explicitly imported matching diagnostic build, pinned installed files, a path-matched loaded managed module and the exact selected process lifetime. Repeated totals are not summed. Restart/replay or PID/birth mismatches cannot verify the game.

The separate [diagnostic compile workflow](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39839261747) successfully compiled the pinned derivative on Windows. Its public artifact contains source additions and the build record, **not a privately licensed NVIDIA runtime or a ready-to-install diagnostic binary package**. To use the diagnostic backend, explicitly compile the pinned source locally with `tools/build-nr-diagnostic.ps1`, then import its generated `diagnostic-manifest.json`. Source additions are GPL-3.0-or-later; upstream/component licenses and corresponding source obligations remain applicable. No automatic game deployment occurs during compilation or import.

This local-build requirement remains a usability limitation. A redistributable diagnostic binary package with a complete corresponding-source/dependency distribution still needs release review. The normal public AKENO Windows executable is built independently of that optional package.

## PresentMon and player-facing measurements

PresentMon no longer requests elevation after a normal-launch permission failure. Automatic monitoring checks the current ETW token/membership state, latches denial, stops FPS capture, and continues neural log diagnostics. Only an explicit manual checkbox plus confirmation can request one elevated capture. Cancellation never starts a second elevated stop command. The console tool hash is verified before launch; captures are serialized, bounded, cancellable and tied to the selected process lifetime. `--terminate_on_proc_exit` stops stale recording; AKENO does not stop a foreign named ETW session or change group membership.

The desktop Qt HUD is opt-in and injects nothing. NR counters reuse the current observation. FPS/frame-time/sensors are honestly labeled as the **last completed capture**, with its timestamp; this is not a per-frame in-game telemetry overlay and may not appear over exclusive fullscreen games. All v1.4.1 PID/swapchain/frame-type, generated/displayed/application-FPS and historical-attribution logic remains.

A/B comparison imports actual user screenshots, requires matching dimensions and declared camera/resolution/settings/renderer, rejects identical images and writes a local JSON plus interactive HTML split view. It preserves exact image hashes. Off/on labels and matching conditions are user attestations, not capture-time proof. Still images do not establish temporal flicker/ghosting; no invented quality score is calculated. Automatic matched-frame acquisition and capture-time configuration binding remain future work.

## Recovery and checks

The transactional installer now journals intended hashes **before** atomic replacement, verifies installed bytes, and supports explicit official swaps only with original-hash preconditions. Recovery preflights **all** interrupted files/backups/renames before changing any. Unknown modified files, damaged backups, links and running games block automatic recovery. Original restore/rollback chains and FiveM restrictions remain in use. Per-game mutation serialization prevents overlapping installer operations.

Initial expanded local full suite: **410 passed, 2 skipped**. Additional end-to-end cases exercise native/composite plans, exact restore, source/plan tampering, official signatures/families/observed paths, local dependencies/includes, isolated self-test execution and SR-not-NR interpretation, A/B artifacts, HUD, UAC denial and no overlap. Final test/build totals are recorded in CI and the delivery message. Windows CI includes EN/DE screenshots and 150% Qt tests; source and packaged executable smoke and ordinary GUI startup are checked. Synthetic fixture screenshots/results are labeled and are not RTX hardware evidence.

## Continuation

1. Read this document and `AKENO_V1_5_UPSTREAM_RESEARCH.md`; inspect `git status` and the latest branch commit. Do not reset other branches.
2. Complete a redistributable diagnostic backend package/source/dependency review if shipping its binaries, rather than substituting a self-declared compiled hash for an audit.
3. Obtain fresh RTX5090/4080 local host/game logs and module snapshots using `AKENO_V1_5_HARDWARE_TESTS.md`. Do not reinstall transaction5 just because its report is inconclusive.
4. Integrate an exact licensed NR RenoDX/other consumer artifact before enabling route B; review a complete helper/bridge stack before enabling route D.
5. Programmatic official driver-profile overrides, instant in-game telemetry, automatic matched frame capture, exact hardware acceptance and actual game execution evidence remain incomplete.
6. Draft PR targets the v1.4.1 branch; never merge automatically. API network denial may prevent creation even though Git push and public CI work. Record that limitation rather than claiming a PR exists.
