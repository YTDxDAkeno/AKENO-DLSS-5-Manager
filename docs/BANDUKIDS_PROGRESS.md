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
