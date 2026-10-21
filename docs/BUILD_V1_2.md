# AKENO v1.2 integration build and acceptance record

Branch: `codex/akeno-v1.2-fivem-integration`. Draft [PR #2](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/pull/2) targets Claude's development branch. Original `codex/fivem-legacy-support` / PR #1 and Claude's `3f44822519a0871576e110034a9a61975751294c` branch are preserved. No default-branch merge or force push.

## Exact validated source and tests

Application source: `210d6fb203ef3258e18ea7b9f3aa59124b0add31`. Build/CI snapshot: `64cda9d23f477a817a9c33a03ea20523a0f79a96` (only documentation and CI font configuration differ). Subsequent handover commits only update documentation/screenshots; use `git log -1` for their exact head.

- Local full suite: **232 passed, 1 skipped** (Windows-only real Authenticode test).
- Windows JUnit downloaded from the final run: **233 tests, 0 failures, 0 errors, 0 skips**, 77.022s.
- [Final Windows/Linux run](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39524328413): **success**. [Parallel push run](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39524321496): **success**.
- Source and packaged EXE smoke checks, all eight pages, 19/19 original design assets plus FiveM artwork, normal GUI process lifetime, PyInstaller, portable ZIP and Inno Setup passed. Runner GUI checks use Qt offscreen and do not prove gaming-PC rendering or GPU execution.
- Fixed the offscreen runner's missing font directory. Actual Windows images at normal and 150% scale were downloaded and visually inspected. Earlier box-glyph screenshots are superseded.

## Download and independently checked hashes

[Windows build artifact: ZIP, installer, SHA256SUMS](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39524328413/artifacts/12311609372)

[Windows test report and screenshots](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39524328413/artifacts/12311584479)

Artifacts were downloaded to `/workspace/akeno-v1.2-build-64cda9d/verified-packages`. Both hashes were recomputed independently, ZIP CRC passed, and AKENO.exe plus the FiveM asset were found inside. The ZIP contains the executable and its required files; extract the complete ZIP before starting AKENO.exe. Artifacts follow GitHub Actions retention and may require sign-in.

```text
94c1cb4b25cebdf84120062da46bbcd82686fa6082e01b2692c403568e4511e0  AKENO-DLSS5-Manager-1.2.0-portable-x64.zip
c45d0fc948315f800380cfbfe1dba1bbacf54ffd04d41ceac67d8ce037269b89  AKENO-DLSS5-Manager-1.2.0-Setup-x64.exe
```

Contained `AKENO.exe` SHA-256: `7fadcff981f7373612604321c999b7636e5a7198e803c14ada1050ed4c7e317a`. These identify this exact Actions build, not later rebuilds. No proprietary NVIDIA runtimes are bundled.

## Implemented behavior and honest limits

NVIDIA CL-only version labels no longer cause automatic false rejection when PE exports and coherent family metadata establish identity. Large-model resource parsing is bounded independently of file size. Actual verified signer identity, exact NVIDIA publisher, hashes, architecture and runtime-family checks remain mandatory. No real NVIDIA sample was available here; Windows exercises the real API with python.org's embedded signature, while NVIDIA-specific fixtures explicitly inject a test verifier. See [runtime review](RTX_RUNTIME_REVIEW.md).

RTX 5090 readiness still requires an authentic local NR runtime and local execution evidence. Driver 610.88 receives an advisory relative to the cited community matrix; an update is neither performed automatically nor promised to supply NR. RTX 4080 is not categorically excluded. The reported Swapper success is recorded, but its exact runtime/backend/game/driver and fresh NR creation/evaluation remain unknown. Ordinary SR/RR/FG capability and working DLL swaps do not establish neural evaluation. No NR/FPS/VRAM success on either owner's PC is claimed.

FiveM Legacy is integrated into Library/Secure Install: discovery separate from GTA V/Online, inventory, persistent owned-server policy, evidence stages, guarded plans, backup/restore and diagnostic exports. Candidate community consumers remain experimental. No production FiveM recipe is approved (`REVIEWED_BACKENDS = ()`), so unsupported deployment stays disabled. Synthetic transaction tests are not real installation acceptance.

An owned Legacy server with documented exact-plugin permission is a controlled experimental candidate. Ownership does not override client protection, Pure Mode or plugin loading restrictions, and does not establish an operational NR route. No server security changes, bypasses or patched NVIDIA runtime downloads are added.

## Screenshots and physical-PC next steps

[Real integrated launcher screenshots](screenshots/v1.2-fivem/README.md): English, German 150%, and actual Windows runner images. All GPU/server fixtures are labelled.

On the real RTX 4080/5090 PCs: export read-only existing-setup diagnostics; establish legal source and NVIDIA signatures/hashes; record exact game/backend/driver/runtime; collect fresh neural creation and successful evaluation with installation attribution; establish exact FiveM server/plugin permission; review a pinned recipe; then test install/restore and measure baseline/enabled frame times and VRAM through PresentMon. Never count fixture screenshots or log absence of errors as hardware success.

Cloud setup scripts/start instructions and required observed artifact hosts are saved as an environment configuration draft. Review and save it in environment settings, then publish to activate that configuration; no publication is claimed here.
