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
