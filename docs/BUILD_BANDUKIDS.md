# AKENO 1.3.1 Bandukids build verification

Date: 2026-10-21 UTC. Development branch: `codex/akeno-v1.3-bandukids-runtime`. [Draft PR #4](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/pull/4) targets `codex/akeno-v1.3-community-runtime` (base `672cd250a9245fe2488cb394ff66609927dcac4a`). Main, Claude's branches and the v1.3 base were not changed.

## Verified source and Windows artifacts

Frozen executable source: **`a6b039ff5975325c31d073675c5922550a16ffff`**. Later commits contain documentation/screenshots only. Both its [push build](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39652619114) and [PR build](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39652628758) passed. The PR run checked out merge `292df3f84008420ccbd91249d4ae78ed4a2b2b83`; its Git tree `f947700d265554b1c1477c6676a59eca7e2f5fdb` was independently confirmed equal to the frozen source tree using GitHub's commit API and local Git.

The **PR-run** artifacts were downloaded and independently inspected. Use these exact links/checksums together; separate CI builds produce different ZIP/installer bytes and manifests.

- [Windows ZIP + installer + SHA256SUMS](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39652628758/artifacts/12370148887)
- [Windows tests, logs and screenshots](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39652628758/artifacts/12369224019)

| File | Bytes | Independently verified SHA-256 |
|---|---:|---|
| AKENO-DLSS5-Manager-1.3.1-portable-x64.zip | 50,167,523 | `760364315cb196b7c52fe5dba4173b11d270e057fdaf237f8281c3816f97f5e5` |
| AKENO-DLSS5-Manager-1.3.1-Setup-x64.exe | 35,918,431 | `d3dd2f0934e89a4245e06e3d73d21cfe9cc080e397df603f55003d9e997ebe30` |

Local downloaded copy: `/workspace/akeno-bandukids-build-a6b039f-pr`. Both recomputed hashes matched SHA256SUMS and CI logs. ZIP CRC/integrity passed; AKENO.exe and FiveM artwork are present. No nvngx_dlssnr.dll, nvngx_dlss.dll, nvngx_dlssg.dll, ComfyUI worker or caller shim is bundled. The Inno Setup installer was built in CI and its downloaded hash verified; an interactive installation on a user's PC was not performed here.

## Observed validation

- Final local Linux full suite: **306 passed, 1 Windows-only skipped**, 52.26s. Exact source local smoke: AKENO 1.3.1 OK, all eight pages and 19/19 design assets.
- Windows full suite: **307 passed, zero skipped/failures/errors**, confirmed in downloaded JUnit XML. Existing v1.3 tests are preserved.
- Windows source and packaged AKENO.exe smoke: version 1.3.1, all eight pages, 19/19 design assets, real Windows Server 2022 / Microsoft Basic Render Driver inventory, no game/runtime candidates. Runner has no NVIDIA gaming GPU.
- Packaged GUI normal startup remained running for the 20-second check. Portable ZIP and Inno Setup build succeeded.
- Windows named-candidate normal and 150% screenshots were downloaded and visually inspected; labels, guided source buttons and initial unverified status are readable. FiveM/RTX 4080 screenshots explicitly label TEST FIXTURE. Selected Windows captures and EN/DE source captures are in [screenshots](screenshots/v1.3-bandukids/README.md).
- Synthetic tests cover exact-pin/schema-2 local import, HashMismatch/non-official handling, wrong source/hash/review/GPU denial, existing OptiScaler offline install/restore and separately qualified Feeder FiveM plugin-only install/restore. Video-worker success lines are not game evidence. Synthetic binaries/signatures are never evidence about the real supplied DLL.

## Research and hardware limits

The [source/security/licensing investigation](BANDUKIDS_REVIEW.md) records the ComfyUI versus in-game distinction, conflicting native signature/test behavior and unknown proprietary model modifications/permission. No real proprietary DLL was downloaded, executed, PE-inspected or security-cleared here. RTX 4080 is initially experimental/unverified; RTX 20/30 remain unverified. FiveM has a distinct qualified software route, but production REVIEWED_BACKENDS is empty pending genuine offline/client neural evaluation and permitted client/server/plugin evidence. GTA Online/protected multiplayer remain blocked. No real hardware/game compatibility or malware-free claim is made.

Push-run artifact retrieval encountered 403 at productionresultssa6.blob.core.windows.net. Only that observed host was added to the existing reusable environment draft, preserving other hosts; saving did not apply/publish it. The independent PR-run artifact download succeeded through its normal GitHub route. Hugging Face metadata remained inaccessible; no proprietary binary request was made. Saved environment configuration still requires publication.
