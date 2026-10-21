# AKENO v1.2 integration handover

## Active branch and scope

- Branch: `codex/akeno-v1.2-fivem-integration`.
- Base: Claude v1.1 commit `3f44822`; merged existing FiveM branch without conflicts. No Claude branch changed; PR #1 retained.
- User brief: integrate FiveM into the existing Windows launcher, fix NVIDIA changelist-metadata false rejection, strengthen runtime identity/authenticity, improve read-only working-setup diagnostics and neural execution evidence, build/test Linux and Windows artifacts.
- User reports father's RTX 4080 already runs DLSS 5 modifications in multiple games. Treat as a user report, not a reason to exclude RTX 40 or manufacture a verified FiveM result. Exact game/backend/driver/runtime/log details requested asynchronously; independent work continues.
- Baseline combined test suite running. No real Windows/NVIDIA/FiveM hardware available locally.
- Confirmed defect: runtime evaluator compares `OriginalFilename` or `InternalName` literally to a DLL filename. CL metadata is rejected before signature verification. Fix requires runtime-specific export/metadata evidence, not removing identity checks.
- Additional security issue identified: Authenticode publisher selection currently enumerates all certificates and accepts any name containing NVIDIA. Bind publisher to the actual signature signer and require the expected organization.

## Current work / next steps

1. Complete baseline; research pinned runtime exports/logs and newer-runtime reports.
2. Add PE export identity and robust CL runtime validation + regression tests.
3. Integrate FiveM discovery/service routing and styled setup panel with persistent owned-server profiles.
4. Add read-only inventory/import diagnostics and attributed neural execution levels.
5. Preserve all v1.1 pages/assets/i18n and dependency workflows; run complete tests, screenshots, Windows CI/build.
6. Create new integration PR targeting Claude branch, do not merge main; record artifacts and limitations.

Commit/push meaningful checkpoints at most five minutes apart during active work. At each checkpoint update results, unresolved errors and exact next steps here. Latest successful pushed commit is available via `git log -1 origin/codex/akeno-v1.2-fivem-integration` (avoid self-referential commit IDs).

## Checkpoint — runtime identity repair

- Combined v1.1 + prior FiveM baseline: **193 tests passed** on Linux.
- Added bounded PE export-table inspection, including export module identity.
- CL metadata now requires coherent NVIDIA product-family evidence and actual NGX Create/Evaluate export pairs; conflicting SR/RR/FG/NR identities, non-DLLs, generic CL-only names and overlay strings remain rejected.
- Authenticode publisher now comes from the actual WinVerifyTrust-verified signer chain, not an arbitrary embedded certificate; expected publisher comparison is exact. Signature checks remain mandatory.
- Added local Swapper cache roots, alternate NGX model store and bounded nested game search. No proprietary downloads.
- Father's follow-up: tool described as “DLSS 5 Swapper”, other programs, latest driver; exact game/runtime/driver/log evidence still unavailable. Do not convert that report into a verified NR claim.
- Next: integrated FiveM service/library/UI, attributed neural diagnostics, compatibility presentation, regression tests and Windows build.

## Checkpoint — integrated wizard and evidence pipeline

- NVIDIA fix tests: 45 targeted runtime/resolver tests passed; 89 FiveM/library/install tests passed before UI wiring.
- FiveM discovery now persists as a distinct launcher entry, retains its inspection through reanalysis, and uses original GPL artwork. Generic install/configure paths explicitly reject FiveM; reviewed transactions remain routed through FiveMService.
- Added an integrated four-step FiveM panel in Library/Secure Install, persistent owned-server policy, read-only diagnostic export, observation/log verification, snapshot and reviewed-plan controls. All eight existing pages remain.
- Added append-aware log observations with artifact hashes, selected-game/target binding, live process-path attribution, rotation/truncation checks and separate installed/loaded/initialized/running/verified states. Known OptiScaler build/failure markers are treated separately from evaluation success. Historical logs do not count as fresh verified execution.
- Updated compatibility presentation: missing evidence is unknown; denied policies and signature/path failures remain blocked. Some older tests intentionally expect the former blanket blocked/creation-is-verified behavior and are being updated to the stricter/new semantics. Full UI/i18n regression work is still in progress; this is NOT a final validated build.
- GitHub credential failure: initial branch push succeeded at f470bb3; later Git reads/push and API return authentication errors. User asked to renew the existing connection, not provide a token in chat. Local commits continue; a Git bundle is saved at `/workspace/akeno-v1.2-fivem-integration.bundle` for recovery. Pushes/PR/Windows Actions remain dependent on restored access.
- Next: finish translations and regression tests, diagnose any actual UI failures, research newer runtime evidence, screenshots and packaging validation.

## Checkpoint — authentication restored and UI regressions

- User renewed the existing GitHub connection. `gh auth status` and `gh api user --jq .login` now confirm YTDxDAkeno; Git read/push retried after that meaningful state change. No credentials were overwritten and no new token created.
- Fixed UI test setup to use the real cache interface rather than an incomplete download stand-in. EN/DE integrated eight-page UI + policy persistence and translation coverage: **9 tests passed**.
- New neural evidence suite: **7 tests passed**, including old-log retouch, rotation, changed artifacts, cross-game binding and separate log streams.
- Updated prior tests to expect unknown/missing evidence without enabling installation, and initialized rather than verified for creation-only log markers. No deployment gate was removed.
- Added German wizard labels/messages and invalidation of prepared plans when policy controls or selection change.
- Next: full combined suite, remaining dynamic FiveM message translations, compatibility/driver evidence refinement, fixture screenshots, Windows CI and build artifacts.

## Checkpoint — support distinctions and main-launcher screenshots

- Complete merged suite at b16c83b: **226 passed**. Additional exact-runtime-hash and driver-warning tests plus EN/DE checks: **17 targeted tests passed** after subsequent edits.
- Version-only community reports no longer establish exact-runtime success/failure; hash-specific evidence is required. Driver 610.88 receives an explicit advisory relative to the documented 616.56+ community matrix, with the official NVIDIA link and no automatic update.
- Official beeradmoore DLSS Swapper source describes replacing existing DLSS/FSR/XeSS libraries, not adding DLSS to unsupported games. Public issue searches returned no relevant exact 310.9.16/RTX 4080 evidence in the queried projects; absence of a result is NOT proof that the configuration cannot work.
- Added a clearly labelled RTX 4080/FiveM screenshot fixture to the existing app, preserving the eight pages and all original artwork. Initial screenshot-fixture syntax error corrected before counting any fixture render. First real launcher render generated successfully; analysis display refined to readable component/status controls with optional technical details rather than raw JSON as the primary UI.
- Snapshot restore now routes through FiveM safety guards from the main Backups page. User-facing FiveM prerequisites are localized; exact original technical details remain available in diagnostic exports.
- Current next steps: run updated complete suite, inspect final English/German and scaled renders, publish new integration PR and verify Windows workflow/artifacts. Existing PR #1 remains open.

## Checkpoint — Windows evidence and final regression pass

- Restored-auth checkpoint b16c83b passed the full Windows and Linux Actions pipeline: https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39521388662 . This validates that earlier integrated snapshot, not every later edit.
- Added real Windows Authenticode API regression using a Microsoft-signed system executable (no proprietary test DLL committed); Linux correctly skips it. Added file-change-during-signature-check rejection.
- 42 targeted runtime/resolver/integration/i18n tests passed with 1 Windows-only skip. Full latest suite running. One prior full run was 227 passed / 1 failed because the resolver discarded the new unknown verdict's text; corrected resolver to retain the exact-build uncertainty note and updated its prior blanket-refusal expectation.
- Existing-setup diagnosis/import dialog is available inside Secure Install for ordinary games too. User permission is required for reuse; import remains signature/identity checked and does not replace game files.
- Added original FiveM art packaging check and Windows screenshot steps for the clearly labelled RTX 4080/FiveM fixture at normal and 150% scale. Selected actual launcher screenshots saved under docs/screenshots/v1.2-fivem/.
- Documented source pins, official Swapper behavior, runtime identity repair, 310.9.16 uncertainty, driver 610.88 warning and physical-PC acceptance work in docs/RTX_RUNTIME_REVIEW.md.
- Next: create integration draft PR, finish current full tests and Windows build, inspect scaled/German renders and publish exact artifact/checksum links. No physical RTX/FiveM compatibility claim is allowed.

## Checkpoint — integration PR and final acceptance checks

- Integration draft PR: https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/pull/2 . GitHub reports MERGEABLE; base is Claude's development branch. PR #1 is retained; main is not merged.
- Latest full local suite before final presentation/attribution refinements: **229 passed, 1 skipped** (real Windows-only signature test). Latest targeted UI/evidence/i18n checks: **17 passed**. Source `AKENO 1.2.0 smoke test OK`, 19/19 original design assets and all eight pages.
- The wizard now explicitly displays recognized hardware, DLSS SR/FG capabilities, NR runtime presence, creation, evaluation, installation attribution, game compatibility and server permission separately. UI plan results are discarded if policy fields changed while a worker was preparing the plan.
- Observation also pins the selected executable hash; replacing a game build invalidates attribution. Creation-only/historical events never turn the attributed status green.
- RTX 40/FiveM recommendations do not extrapolate FPS from another game's RTX 4070 cost model; RTX 40 measured sessions require matching GPU/driver/resolution metadata. Missing prerequisite findings recommend disabled. Removed the inherited blanket “enough VRAM at any working resolution” claim while retaining profiles and PresentMon functionality.
- Inspected normal English and German 150% real launcher renders with labelled synthetic hardware/server data. FiveM steps and all original artwork remain visible; selected renders are committed under docs/screenshots/v1.2-fivem/.
- Windows pipeline for the latest application changes is still running; do not mistake the earlier b16c83b green build for final validation. Exact artifact/checksum links will be recorded after final CI completion.

## Checkpoint — diagnosed Windows signature fixture and large-model metadata

- Windows 5921495 run failed only the new real-signature test: the OS PowerShell file had no embedded signature. The verifier is intentionally for embedded runtime signatures; system components may use catalog signing. The regression now verifies the installed python.org executable's embedded Python Software Foundation signature and asserts it cannot be NVIDIA. No signature bypass/conditional pass was added.
- Found and fixed a separate parser issue relevant to large NR models: v1.1 skipped all version metadata when a file exceeded 96 MB, even when a bounded resource section existed. Version resources are now read up to the existing 8 MB resource cap regardless of total file size; whole-file fallback stays bounded. Added regression above the whole-file scan threshold and protected malformed resource ranges from negative sizes.
- Actions log retrieval initially hit network restrictions on the results receiver/Azure blob redirect; required exact hosts were added while preserving the existing allowed domains. Job log then confirmed the failure above. New-domain configuration is saved; publication remains separate.
- Latest source smoke: AKENO 1.2.0, eight pages, 19/19 original assets. Full local suite at 7992080: pending completion; prior latest full result was 229 passed / 1 Windows-only skip. New parser tests: 28 passed / 1 Windows-only skip.
- Next: push signature-fixture/parser corrections, verify Windows tests/build and artifact checksums, finish final screenshots/report. Current hardware success remains unproven; diagnose the father's working installation with the new read-only export.
