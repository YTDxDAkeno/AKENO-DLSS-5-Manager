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
