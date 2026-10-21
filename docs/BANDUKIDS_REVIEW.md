# Bandukids runtime investigation and integration — AKENO 1.3.1

## Identity and scope

Named candidate: **Bandukids Ada Neural Rendering — RTX 4080 experimental**.

- [Pinned source repository](https://github.com/Bandukids/ComfyUI-DLSS5-VideoSuite/tree/d7ed7bb0bfa3e5ff53bee977f431b92a54a0557e), inspected commit `d7ed7bb0bfa3e5ff53bee977f431b92a54a0557e`.
- [User-guided runtime source page](https://huggingface.co/Bandukids/DLSS-Runtimes/blob/main/nvngx_dlssnr.dll). This is a mutable main-branch page; the file hash, not the URL alone, identifies the candidate. AKENO opens it only on an explicit click and does not download the DLL.
- Exact referenced **DLL** SHA-256: `8270b350cd82de5ce89806872cdd6b6a9249b80836b91bbeb3573470744cc206`.
- The user independently matched this hash to their local file and reports Windows **HashMismatch**. Those are supplied observations, not tests repeated against their actual DLL in this cloud. No proprietary runtime, upstream worker, shim or installer binary was downloaded/executed here.

A hash match establishes byte identity relative to the supplied pin. It does not establish safety, NVIDIA authenticity, permission, exact binary modifications or game compatibility. Actual version, architecture, family, exports, signer and signature result are inspected on the local file. No version is guessed from a release name.

## RTX claims versus the actual source

README Core Highlights advertises RTX 40/30/20 compatibility, an Ada/Ampere route and 72+ FPS on RTX 4070 Ti SUPER. The runtime table calls NR an **Ada Patched** model. These are upstream claims about ComfyUI IMAGE/VIDEO processing, not independent RTX 4080 game measurements. No RTX 20/30 success is inferred.

The inspected code is an out-of-process **video/image worker**: Python nodes send frames to `dlss5_worker.exe`; `native/worker_main.cpp` creates its own NVIDIA D3D12 device, uploads/downloads image resources and calls NGX Feature 18 through its own caller shim. CUDA/NVOF and Depth Anything support temporal/depth guidance for that workflow. Its isolation is crash containment for the Python host, not a proof of malicious-code containment. It has no FiveM loading contract or in-game depth/motion integration. AKENO does not install it, its shim or ComfyUI dependencies as game plugins.

Important source contradictions:

1. `NgxSession::Initialize` verifies Authenticode on NR/SR/FG and requires `310.8.x` versions **before loading**; `VerifyAuthenticode` rejects any WinVerifyTrust error. The published modified file's reported HashMismatch would fail that source path. We neither remove that check nor assume a prebuilt worker differs in a useful way.
2. `install_local.ps1` rejects non-Valid signatures before copying. It is not executed by AKENO.
3. `tests/test_worker_integration.py` expects `CreateFeature(18)` to fail with `0xBAD00001` on its Ada validation host and tests unsigned-runtime rejection. Those tests require a local Windows runtime and were **not run here**.
4. `runtime/README.txt` says the worker does not load RenoDX/Streamline. README marketing mentions a RenoDX bridge and hosting the add-on; these do not prove the current worker loads that add-on or that the DLL works in a game.

Do not reinterpret the worker's `nvngx_dlssnr.dll Feature 18 Evaluate succeeded` status as selected-game evidence. AKENO does not add that line to its game-success parser; regression coverage verifies it is not a game success marker. ComfyUI FPS is neither in-game FPS nor a PresentMon comparison.

## Modifications, security, licensing and reproducibility

The project LICENSE is MIT for its code. THIRD_PARTY_NOTICES explicitly excludes NVIDIA proprietary runtimes from that MIT grant and says they are not redistributed by project source/releases. A separately hosted Hugging Face file is not evidence of NVIDIA redistribution permission or local-use terms. Hugging Face **metadata-only** API access was denied by this environment's network policy; no model-card licence was independently retrieved. The observed host was added to the reusable configuration draft, but saving does not apply/publish it. No binary request was attempted.

`build_native.ps1` documents an MSVC/Windows SDK source build for the worker/shim using a local ComfyUI toolchain. This is not a reproducible build of the modified NVIDIA model and does not establish a source-to-binary match for the bundled prebuilt worker. The pinned tree does not provide a model patch/diff or reproducible NR-model build establishing the changes in the supplied DLL. Modification scope, independent binary audit, trusted hash-specific antivirus results and model redistribution/local-use permission remain unresolved here. HashMismatch is expected evidence of altered signed bytes, not proof of malware by itself; it is never reclassified as valid NVIDIA authentication.

AKENO therefore does not pre-approve this binary. A local expert/operator review must honestly establish source, modifications/build limitations, security risks and permission for local reuse. Unresolved evidence must be recorded as limitations; do not fabricate review assertions to enable a button. User-selected local review is not an AKENO maintainer endorsement or malware-free certificate. No download, mirroring, binary patching, signature spoofing, security exclusion, upstream script execution or licence/protection bypass is introduced.

## Hardware and game matrix

| Hardware / route | Initial status | Meaning |
|---|---|---|
| RTX 4080 desktop, NVIDIA PCI 0x2704, 16 GB target | Experimental / **unverified** | Named local review can authorize a controlled experiment for an exact driver/API/backend. 16 GB describes target inventory, not guaranteed memory fit. |
| RTX 20 / RTX 30 | **Unverified** | Named profile does not enable these from marketing claims; independent GPU evidence/profile review is needed. |
| Other GPUs / RTX 50 | **Unverified for this candidate** | No extrapolation from NVIDIA-signed models or other community results. |
| Existing OptiScaler NR | Candidate in-game consumer | Existing x64 D3D12 input contract or supported D3D11-on-12/Vulkan-on-12 bridge, dependency/conflict and exact-review gates still apply. |
| Existing DLSS5-Feeder | Candidate frame/input provider | Existing ReShade/add-on/input requirements and exactly one compatible neural consumer still apply. |
| ComfyUI worker | **Not an AKENO game adapter/plugin** | Video/image evidence is separate; worker/shim are not deployed. |
| Permitted FiveM Legacy server | Separately qualified / **unverified** | Needs a maintainer-reviewed exact client/plugin/runtime recipe, not an offline checkbox or a hash match. No production recipe is shipped. |
| GTA Online / ambiguous GTA sessions / protected multiplayer | **Blocked** | No new override or protection bypass. |

## Local import and controlled offline testing

1. Select the GPU/game in the existing launcher. Open **Secure Install → Community Runtime Manager**, choose **Bandukids Ada NR — RTX 4080 experimental, unverified**. Source buttons open the pinned code page and referenced runtime page, never an automatic binary resolver.
2. Select the legitimately obtained local DLL. Inspect: exact expected hash plus x64 PE/DLL, NGX family/exports, version and fresh Windows signature inspection are required. A different file is rejected. The known modified pin can never be labelled officially NVIDIA-signed, even if a contradictory verifier result appears.
3. **Save review template** creates schema 2 with the candidate ID/hash/code commit/runtime source and actual PE version, but empty reviewer/security/build/licence/evidence and permission false. Complete an honest review and exact RTX 4080 PCI/driver/backend/API target before **Load review record** and explicit local-use/risk consent. No fictitious GitHub release is invented for the Hugging Face file. Unrelated schema-1 reviews retain their existing workflow.
4. Import into the separate community cache. It never enters the official NVIDIA runtime table. Then use the existing offline dry-run, dependency preview, explicit consent, verified backup/transaction/install/restore and fresh observation workflow. Generic installation cannot modify an active community experiment. Successful installation is not successful neural evaluation.
5. Test baseline/enabled sessions through PresentMon, collect fresh game-attributed creation/evaluation and real stability/VRAM results. Do not infer these from SR swapping, an overlay, historical logs, no errors, a ComfyUI result or synthetic tests.

## FiveM: a separate recipe path, not a blanket ban or override

The FiveM panel now opens the named Bandukids runtime review and exports candidate/qualification details. After a complete local runtime review, a **maintainer-reviewed** `BackendReview` may explicitly select `runtime_policy="reviewed_bandukids"`, `signed_runtime=False`, the exact candidate/runtime set and canonical local-review SHA-256. The context looks up only that NR hash from the community cache; SR retains its official signature gate. PE/hash/signature/review policy is inspected again, not trusted from a cached flag.

Additional required recipe evidence: successful offline **and exact FiveM-client** neural creation/evaluation, verified allowed plugin loading, and compatible client protections. Existing client/configuration fingerprint, exact GPU PCI/generation/VRAM/driver, measured D3D11 route, backend/package/member/output pins, prerequisite plugin hashes, written operator permission, effective Pure Mode and process/target/transaction guards all remain. Pure level 2, denied/unknown permission, detected protection, wrong/changed file or review and fake signed flags block deployment. Pure level 1 still requires the exact allowed artifact set. Only FiveM.app/plugins is writable; GTA files remain outside the target.

Production `REVIEWED_BACKENDS` remains empty because the required real client/server evidence is absent. These recipe fields are not user checkboxes or trusted downloaded JSON. A synthetic fully qualified Feeder test proves the software can plan/install/restore through this distinct path; it proves no real FiveM compatibility. Before adding a real recipe, independently test a permitted offline game, then the exact owned Legacy server/client/plugin set without disabling protection, capture genuine evaluation and restore evidence, and review the results. Restore remains available without renewed runtime approval.
