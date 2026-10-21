# Bandukids focused integration progress

Branch: `codex/akeno-v1.3-bandukids-runtime`, based on current v1.3 `672cd25`. Preserve v1.3/Claude/v1.2/PR #3. No force push or main merge.

## Initial source inspection — 2026-10-21

- GitHub source pinned to `d7ed7bb0bfa3e5ff53bee977f431b92a54a0557e`. MIT applies to project code; THIRD_PARTY_NOTICES explicitly excludes NVIDIA runtimes. No model DLL or upstream worker binary downloaded/executed.
- README advertises RTX 40/30/20 and 72+ FPS on RTX 4070 Ti SUPER; this is ComfyUI video/image processing, not game evidence. Native worker builds D3D12 resources, CUDA/NVOF optical flow and NGX calls in a separate process.
- Contradiction: Initialize requires valid Authenticode for all three runtimes and 310.8.x versions; integration test expects Feature 18 rejection on Ada. runtime/README says it does not load RenoDX/Streamline. Treat marketing and implementation separately; no bypass added.
- User supplied exact DLL pin `8270b350cd82de5ce89806872cdd6b6a9249b80836b91bbeb3573470744cc206`, independently matched to their local file, with Windows HashMismatch. This establishes their reported identity, not our binary analysis/safety/license permission. Version/exports/modification diff remain to be collected locally.
- Updated user requirement includes FiveM: develop a separately reviewed Legacy-server route with exact client/plugin/NR evidence, not an offline-checkbox override. GTA Online/protected multiplayer remain blocked. No real FiveM recipe can be approved from the ComfyUI worker.

## Next steps

1. Add named pinned Bandukids candidate and initially unverified RTX 4080 profile; RTX 20/30 stay unknown. Keep all reviews explicit and never call this hash officially signed.
2. UI guided source links, local selection, full exact-pin/PE/signature/risk/provenance inspection and incomplete review template with source identity.
3. Maintain offline adapter/backup/evaluation flow; add a separately pinned maintainer-review FiveM community gate without bypassing client/server restrictions or inventing evidence.
4. Preserve tests, add focused regression coverage, full Windows build/artifacts/screenshots, draft PR targeting v1.3.

Use Git checkpoints within five minutes during active work. Continue from git status and this file; all hardware/installation success remains unverified.

## Checkpoint — named candidate/import policy

- Added named Bandukids candidate, exact supplied DLL hash, pinned source-code commit, guided static source pages and unverified RTX 4080 PCI profile (0x2704). RTX 20/30/50 remain unverified; candidate metadata is not an approved review.
- Separate schema-2 local reviews bind this exact candidate/hash/GitHub source/Hugging Face provenance. No fictitious GitHub release is required, but security/build/licensing evidence and explicit local-use permission remain mandatory. Old schema-1 generic reviews remain supported for unrelated files and cannot relabel this known modified hash.
- Selected named candidate rejects a different file; exact match without review cannot import. Even a contradictory apparent valid-NVIDIA result cannot mark the known modified hash official. Existing PE/architecture/export/version/signature-inspection and copy rehash checks remain.
- UI offers Bandukids selection/source pages and an intentionally incomplete review template. No worker, shim, model or upstream script downloaded/executed. **57 focused candidate+existing community tests passed** (synthetic registry pins only; not the real DLL).
- Hugging Face metadata-only API request was denied by environment networking (403); no binary request was made. Continue source/license investigation through supported networking if available.
- Previously pushed 39343e0. Next: narrowly scoped maintainer-reviewed FiveM community recipe gate, focused/offline/FiveM tests, full suite, documentation, Windows build and draft PR.

## Checkpoint — separately qualified FiveM community recipe

- Added a distinct maintainer-reviewed `reviewed_bandukids` FiveM runtime policy. Default signed-runtime behavior remains unchanged. Community NR uses only the imported exact named pin and a pinned schema-2 review; SR still requires NVIDIA signature verification.
- Additional mandatory recipe evidence: offline AND exact-client neural evaluation, confirmed permitted plugin loading and compatible client protections. Recipe binds exact client/configuration, GPU PCI, VRAM, driver, D3D11, backend/package/file layout, server permission and review digest. Production REVIEWED_BACKENDS remains empty; these are not editable user checkboxes or a generic offline override.
- Added a Bandukids review entry to the existing FiveM panel, plus named-candidate inspection/qualification in read-only diagnostic exports. Existing plugin target, Pure Mode/protection restrictions, source rehashing, backup/restore and process guards remain intact. GTA Online/ambiguous GTA/protected multiplayer stay blocked in the generic community route.
- **60 existing FiveM tests passed** after the scoped change. **31 focused Bandukids/FiveM/integration tests passed**, including synthetic fully qualified Feeder install/restore and denial for each missing evidence, changed runtime/review, restricted Pure Mode, denied permission, detected protection and fake signed flag. No real runtime/game/server success is claimed.
- Hugging Face metadata remains blocked (403) after a saved draft addition for huggingface.co; saving is not runtime application/publication. No binary request/download was made. Continue with this documented research limitation rather than bypass networking.
- Previously pushed 96daea7. Next: full regression, source/permission documentation, actual EN/DE renders, final Windows build/artifact hashes and draft PR targeting v1.3.
