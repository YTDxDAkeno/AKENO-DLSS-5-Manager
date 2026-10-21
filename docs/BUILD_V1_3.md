# AKENO v1.3 community runtime build and acceptance record

Branch: `codex/akeno-v1.3-community-runtime`. Draft [PR #3](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/pull/3) targets v1.2. Claude's `3f44822`, v1.2 `43fd104` and PR #1/#2 are preserved; no main merge or force push.

## Exact validated snapshot

Application/build source: `5dbc03e56b3d8a899bf51ad8fc6ee2993203014f`. Later commits only add handover documentation/screenshots. Use `git log -1` for the exact latest documentation head.

- Full local suite: **277 passed, 1 Windows-only Authenticode skip**, 52.15s.
- Downloaded Windows JUnit: **278 tests, zero failures, errors or skips**, 106.076s. All 233 previous Windows tests remain, plus 45 community/policy/layout tests.
- [Windows/Linux workflow](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39615697165): every job step, including Complete job, reports success; GitHub aggregate run/job status was still in_progress during artifact review. Final aggregate status is recorded in COMMUNITY_RUNTIME_PROGRESS.md when available. [Parallel exact-commit run](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39615705926).
- Source/packaged EXE smoke tests, eight original pages, 19/19 original design assets plus FiveM artwork, normal packaged GUI lifetime, PyInstaller, portable ZIP, Inno Setup and checksums passed. Windows runner uses Microsoft Basic Render Driver, not a real NVIDIA gaming GPU; GUI checks use Qt offscreen with system fonts.
- EN/DE screenshots at normal and 150% were rendered and inspected. Actual Windows community screenshots were downloaded/inspected; no missing fonts or clipped manager controls. Secure Install now stacks its existing actions on narrow windows; EN/DE assertions require zero hidden horizontal overflow.

## Download and independently verified hashes

[Windows build: portable ZIP, installer, SHA256SUMS](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39615697165/artifacts/12351249695)

[Windows JUnit, smoke logs and screenshots](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39615697165/artifacts/12352431875)

Downloaded to `/workspace/akeno-v1.3-build-5dbc03e`. Recomputed both hashes independently; ZIP CRC passed; AKENO.exe and FiveM artwork present. Archive contains no `nvngx_dlssnr.dll` or `nvngx_dlss.dll`. Extract the complete portable ZIP before launching AKENO.exe. Artifacts follow GitHub retention and may require sign-in. Builds are not code-signed.

```text
924f9731a7757b93a61fbbd1b217953fd244363a53c76404a62ab148a8924e46  AKENO-DLSS5-Manager-1.3.0-portable-x64.zip
4d98a8bbb3ee53291dfbe23a6ec3c9584cd7f34b78b955a951eb7faca9f1707c  AKENO-DLSS5-Manager-1.3.0-Setup-x64.exe
```

Contained AKENO.exe SHA-256: `bd8ac3bc030eb64296e49c71a25a4fd103c8de3fbebfab4fec35392bb66b54c1`. These hashes identify this exact build, not later rebuilds.

## Implemented trust and experiment scope

Four separate categories: NVIDIA-authenticated (A); explicitly user-reviewed community, not NVIDIA-authenticated (B); unreviewed/unknown (C); rejected (D). Community files require exact extracted DLL hash/version, PE architecture/exports, source/release/commit, dated security/build/licensing review, limitations, explicit local-use consent and matching generation/driver/backend/API targets. Category B never passes the official signature gate or global runtime table; category C/D cannot deploy. A local review is user expert/operator evidence, not maintainer endorsement, remote provenance proof or a malware-free certificate.

The manager reuses existing adapters/package hashes and transaction backup/rollback. Dry-run does not change game files; apply rechecks plan/file/hardware metadata and consent. Foreign files/conflicts and changed sources block; post-copy failures roll back. Normal installer/configuration cannot modify an active community experiment. Restore remains available. Detailed guide: [COMMUNITY_RUNTIME_GUIDE.md](COMMUNITY_RUNTIME_GUIDE.md).

RTX 4080/16 GB is eligible for controlled offline experiments only when a supplied runtime has a complete matching review and prerequisites; no real NR creation/evaluation is established. RTX 5090 has the same missing-model/local-evidence requirement. The reported 617.42/57-games/41 signed SR/zero NR smoke result is the user's observation, not a reproduction here. No RHI binary is pre-approved; SF-v2 cross-generation support, exact extracted hashes, modifications and licensing remain unresolved in the inspected metadata. Published ZIP digests do not prove safety. See [source review and matrix](COMMUNITY_RUNTIME_REVIEW.md).

Supported candidate paths: existing OptiScaler NR on x64 D3D12 with DLSS/FSR2+/XeSS input, supported D3D11-on-12 or Vulkan-on-12 bridges, and Feeder with its existing ReShade/input/one-consumer requirements. API labels or DLL presence alone are not successful game integration. No universal game/GPU support claim.

FiveM's existing read-only wizard, server profiles and guarded signed-runtime recipes remain intact. The new community deployment route hard-blocks FiveM, GTA Online/ambiguous GTA and anti-cheat/blocked multiplayer. Owning a server cannot override client protection; no multiplayer override, stealth injection, signature spoofing, antivirus exclusion, licence bypass or protection disabling is added. No runtime repack download/redistribution or upstream script execution occurs.

## Real hardware and remaining acceptance

Installed, loaded, created, evaluated and attributed working stay separate. Fresh observations bind hashes/executable/log-prefix cursors; old/cross-stream logs and file installation cannot prove success. A process exit does not fabricate a crash; historical PresentMon sessions are not automatically tied to the selected runtime hash. No FPS, VRAM-fit or stability results are invented.

On the real RTX 4080/5090 PCs: inspect legitimate local models; complete an honest expert source/security/licensing review where needed; record exact GPU/driver/runtime hash/game/backend/API; test a permitted offline backend with backup, fresh NR creation/evaluation, launch/stability and PresentMon baseline/enabled sessions; exercise restore and confirm original hashes. FiveM needs its own permitted exact-plugin recipe/client evidence and remains outside this community route. No physical installation/restore or neural execution acceptance has occurred here.

[Actual launcher screenshots with labelled fixtures](screenshots/v1.3-community/README.md). [Handover/checkpoints](COMMUNITY_RUNTIME_PROGRESS.md). Saved cloud instructions now point to v1.3/PR #3; configuration draft persistence is confirmed, publication is not. Review/save/publish that draft in environment settings to activate it for future environments.
