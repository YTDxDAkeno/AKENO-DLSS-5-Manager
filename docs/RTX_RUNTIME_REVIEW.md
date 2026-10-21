# v1.2 NVIDIA runtime identity and RTX 4080 evidence

Reviewed 2026-10-21. The owner's father reports successful “DLSS 5 Swapper”/other-tool use on an RTX 4080 with a current driver in several games. This is retained as a user report. Exact game names, executable/backend, driver number, signed runtime hash/version, and neural logs have not yet been supplied. It is neither discarded nor converted into a verified FiveM result.

## Runtime identity fix

v1.1 treated PE OriginalFilename/InternalName as literal DLL names. Legitimate NVIDIA builds may identify themselves by a Perforce changelist, e.g. `CL 38277099`, `CL 37642667` or `CL 37460577`. v1.2 records those labels, numeric versions and SHA-256 separately. Same displayed version is not the same build.

`runtime_identity.py` now requires an x64 DLL and an actual NGX CreateFeature/EvaluateFeature export pair. A conflicting explicit SR/RR/FG/NR/NGX identity is rejected. Literal matching module identities are accepted only with the expected exports; CL-only identities additionally need coherent NVIDIA company and runtime-specific product/description evidence. Generic `NVIDIA NGX` plus a CL number remains insufficient when the family cannot be distinguished. A genuine but insufficiently identified build is reported for review rather than guessed interchangeable. The parser reads bounded export tables; strings merely appended to an overlay do not count as exports.

Authenticode remains mandatory. WinVerifyTrust validates the file; the publisher now comes from the verified signature's leaf using WTHelper provider state, not an arbitrary certificate in the embedded certificate collection. The publisher comparison requires `NVIDIA Corporation`, not a substring. Files are rehashed after signature inspection, source copies are pinned, and transaction copies are checked again. A Microsoft-signature Windows test exercises the real API without redistributing vendor samples; synthetic NVIDIA tests inject clearly labelled verifiers. No real NVIDIA sample is available in this development machine.

Some future legitimate build may still require a new identity rule. Do not solve such a case by accepting every NVIDIA-signed DLL or disabling signatures. Obtain its metadata, export module/names, version, hash and signature result from the read-only report first.

## What DLSS Swapper does

Official source: [beeradmoore/dlss-swapper, 53b1ecc9d9ae59a23bb3e6194a55ba6243426d9c](https://github.com/beeradmoore/dlss-swapper/tree/53b1ecc9d9ae59a23bb3e6194a55ba6243426d9c). README describes managing/swapping existing DLSS/FSR/XeSS libraries and explicitly says it does not add DLSS to a game without support. It also warns about impersonating download sites. This does not invalidate an RTX 4080 setup; it means the executed technology must be identified separately.

The launcher keeps these separate: GPU recognition, official SR/RR/FG/MFG hardware capability, game input support, NR runtime presence/authenticity, neural creation, neural evaluation, and FiveM server permission. RTX 4080 hardware supports SR/RR/FG in appropriately integrated games; official MFG is a different capability. No green NR result is inferred from an SR DLL swap or a working settings menu.

## Runtime 310.9.16.0 and driver evidence

The public [NVIDIA DLSS SDK tree](https://github.com/NVIDIA/DLSS/tree/main/lib/Windows_x86_64/rel) lists `nvngx_dlss.dll`, `nvngx_dlssd.dll`, and `nvngx_dlssg.dll`; the inspected tree did not list `nvngx_dlssnr.dll`. Only filenames/metadata were queried for this follow-up; no DLL downloaded. Seeing version 310.9.16.0 on an SR/RR/FG library does not establish that an NR model with the same version is available or supports this GPU.

Pinned sources remain:

- [DLSS5-Feeder 16cb1946a8331751b0f89a728b797d895ecdfba1](https://github.com/jlrouzies-fr/DLSS5-Feeder/tree/16cb1946a8331751b0f89a728b797d895ecdfba1): older signed 310.8.0 failures on older RTX and specific RTX 5090/driver/consumer measurements; its README is not exact-hash validation for a different build.
- [Skynizz fc702f8ca22f97b01bd6a02f3386bffb3484fc35](https://github.com/Skynizz/optiscaler-dlss5/tree/fc702f8ca22f97b01bd6a02f3386bffb3484fc35): RTX 4070 results in other games, runtime provenance not established for the owner's setup. Source logging distinguishes `DLSS-NR: pass ... built` from evaluation; “first evaluated next frame” is not evaluation success.
- [Dagherbou 973761621353b99bee3dc7d4bb27b117fef2644f](https://github.com/Dagherbou/OptiScaler_DLSSNR/tree/973761621353b99bee3dc7d4bb27b117fef2644f): additional community fork, not a reviewed FiveM/RTX 4080 recipe here.
- Existing BMTB/Vann source assessments are preserved in FIVEM_RESEARCH.md; neither becomes trusted solely because it advertises RTX 40 support.

The limited public issue searches for exact 310.9.16/RTX 4080 terms returned no relevant results in the queried repositories. This is a research limitation, not evidence of incompatibility. Version-only success/failure records now remain unknown unless the exact runtime hash matches reviewed evidence.

The reported RTX 5090 machine's 610.88 driver is older than the community matrix beginning at 616.56. The app gives an explicit advisory and links [NVIDIA's official drivers page](https://www.nvidia.com/en-us/drivers/). That is not a universal minimum requirement or a promise that a newer driver supplies the NR runtime. No driver is silently updated.

## Read-only diagnosis and local import

Use Secure Install → Inspect existing setup for any selected game. For FiveM, use Analyze/Export diagnostics in the embedded wizard. Reports identify local components, version metadata, exports, SHA-256, actual runtime acceptance/refusal and available log evidence. A local path or valid signature is not proof of redistribution permission; report provenance says “local file; origin not attested”. User-selected imports use the normal verifier and only copy an allowed local runtime into AKENO's cache. No working game file is replaced by analysis/import.

Discovery includes NVIDIA DriverStore, system folders, both NGX store layouts, model `.bin` identity checks, extracted driver packages, bounded game subdirectories, documented RHI copies, likely local Swapper libraries and explicit user-selected files. Location never substitutes for identity, signature or legal permission. Some Swapper versions store archives rather than loose DLLs; AKENO does not execute its installers or unpack an unreviewed proprietary archive as an automatic download source.

## Execution evidence

`diagnostics/neural.py` reads ordinary backend logs. Installed, loaded, initialized, running and attributed verified reports are separate. Ordinary DLSS SR/FG creation does not count. A new observation records artifact hashes and log-prefix cursors, so touching old logs does not create new evidence. Rotated/truncated/replaced logs require a new observation. Success stages are not stitched across different logfile streams. “Verified” requires fresh creation plus evaluation in one stream, unchanged observed artifacts, the selected game/target and a matching live executable path. It is normal log-based attribution, not cryptographic proof of GPU instructions; no process injection or protection bypass occurs.

For FiveM start the game first if its build-specific GTAProcess path must be captured, start observation, then exercise/reinitialize the backend through its ordinary supported controls. Creation before the observation does not count as fresh creation. Current builds that do not emit successful evaluation markers remain unverified until a reviewed real-log parser rule is added; do not invent a marker or count the absence of errors as success.

## Remaining physical-PC work

Run the 1.2 build on the RTX 4080 and RTX 5090 PCs, export the read-only report, and review real NVIDIA signatures and NR locations. Supply exact successful game/backend/runtime hashes and fresh creation/evaluation logs. Test FiveM only on the owned Legacy server with documented exact-plugin permission and effective Pure Mode rules. Benchmark baseline and enabled configurations through PresentMon before selecting Performance/Balanced/Quality/Custom settings; no FPS estimate or memory-fit guarantee is fabricated. A signed local copy may still be technically or legally unsuitable for cross-game reuse. Installation remains gated on a reviewed recipe and cannot be enabled with an “ignore security” checkbox.
