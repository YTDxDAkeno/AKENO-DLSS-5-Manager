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

## Checkpoint — v1.3.1 source validation

- Full Linux regression: **304 passed, 1 Windows-only skipped** in 51.17s. Added offline OptiScaler install/restore, ComfyUI video-log non-evidence and explicit source-link UI coverage. Synthetic fixture pins do not validate the supplied proprietary DLL.
- Version advanced to 1.3.1; screenshot capture now includes the named Bandukids review screen. Saved English and German 150% source renders with explicit fixture provenance.
- Added BANDUKIDS_REVIEW.md documenting pinned upstream source, signature/test contradictions, unknown patch/security/permission, hardware matrix, local review and FiveM qualification protocol. Existing generic community and FiveM guides explain the separate route.
- Next: create draft PR against codex/akeno-v1.3-community-runtime, finish exact-source Windows CI/build verification, archive hashes/results. No actual RTX 4080, proprietary DLL, game or FiveM execution has been tested here.

## Checkpoint — exact candidate diagnostic qualification

- Draft PR #4 created against the existing v1.3 branch: https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/pull/4 . GitHub authentication and push are working; no credentials were replaced.
- Final review found that an unrelated signed-runtime FiveM recipe could otherwise populate the Bandukids-specific diagnostic eligibility label. Bound that label to the exact named community policy/candidate/runtime hash; added both signed and named-recipe diagnostic regression cases.
- Focused FiveM diagnostics/integration tests passed (see current test output); prior e090b09 full suite was 304 passed, 1 Windows-only skip. Final Windows build must use this follow-up source, not an older green checkpoint. Source v1.3.1 smoke passed with all 8 pages and 19 design assets.
- Production recipe catalog remains empty and no real FiveM/RTX 4080 approval is implied. Next: final full suite and exact-commit Windows artifact/screenshot/checksum verification.

## Checkpoint — final Windows build green

- Frozen source: a6b039ff5975325c31d073675c5922550a16ffff. Full local regression: **306 passed, 1 Windows-only skip** (52.26s). Focused diagnostic/integration checks: **21 passed**.
- Exact-source Windows run https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39652619114 succeeded: **307 Windows tests passed**; Linux CI 306 passed/1 skipped. Source/packaged v1.3.1 smoke, all 8 pages/19 assets, normal packaged GUI lifetime, screenshot generation, ZIP and Inno Setup installer succeeded.
- Build artifact ID 12370950135; evidence/screenshots artifact ID 12370895288. Download is currently blocked by network policy at the observed productionresultssa6.blob.core.windows.net host (403; signed URL query kept private). Added only that host to the preserved environment draft; saved configuration requires publication and did not change current access. No independent artifact checksum/ZIP inspection is claimed yet.
- The same-source pull_request run 39652628758 also succeeded. Next: attempt its normal artifact download, independently verify if accessible, document exact checksums and remaining limitations, finish continuation bundle and draft PR.

## Final verification and continuation

- Same-source PR build **39652628758** downloaded successfully through the normal GitHub artifact route. Its checked-out merge 292df3f84008420ccbd91249d4ae78ed4a2b2b83 has the identical Git tree to a6b039ff5975325c31d073675c5922550a16ffff; confirmed via API/local Git.
- Independently recomputed ZIP/installer SHA-256 matched the downloaded manifest and CI logs; ZIP integrity and launcher/FiveM artwork presence passed. No NVIDIA model or upstream worker/shim is bundled. Downloaded JUnit confirms **307 Windows tests, zero failures/errors/skips**. Source/packaged smoke reports version 1.3.1, 8 pages and 19 assets on Microsoft's render driver (no NVIDIA GPU).
- Inspected actual Windows named-candidate normal/150% screenshots and archived selected captures with fixture provenance. docs/BUILD_BANDUKIDS.md records exact artifact links, byte counts, checksums, source tree and limitations. Local artifacts: /workspace/akeno-bandukids-build-a6b039f-pr .
- FiveM route is implemented and synthetic installation/rollback tested; **no production recipe approved**. Next account should read BANDUKIDS_REVIEW.md before collecting real RTX 4080 offline then exact permitted FiveM client/server/plugin evidence. Never replace review fields with guessed success or turn user permission into a protection bypass. GTA Online/protected multiplayer remain blocked; the known modified DLL is never officially NVIDIA-signed.
- Source frozen at a6b039f; subsequent checkpoints are evidence/docs only. Draft PR #4 remains unmerged. Git bundle will be saved to /workspace/akeno-v1.3-bandukids-runtime.bundle after this checkpoint. Continue on the new development branch; preserve the v1.3 base and Claude's work. Cloud network additions are saved draft configuration, not published/applied.
