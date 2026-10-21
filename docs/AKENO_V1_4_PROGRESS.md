# AKENO v1.4 implementation progress

Base: `ae28fdf`, branch `codex/akeno-v1.3-bandukids-runtime`. Existing official verification, v1.2 FiveM architecture and v1.3 expert-review records remain supported. No proprietary NVIDIA DLL is present in this checkout.

## Implemented

- Separate schema-3 experimental provenance profiles, with exact hash/version, source, local-use permission, GPU claims, tested/validated/incompatible generations, driver minimum, backend/API mapping and execution-evidence inventory. A profile is never a malware certificate or automatic execution verification.
- Recognized community and advanced local modes alongside the unchanged NVIDIA signature path. Modified signatures are disclosed. RTX 40/50 can perform an eligible first offline experiment without exact driver/device expert-review pins. Older GPUs require an independently validated applicable profile. Concrete incompatibilities, invalid PE identity/architecture, denied hashes, unsafe paths, anti-cheat and missing dependencies remain blockers.
- Local offline manifest inventory and backend archive selection before any network fetch. Candidate selection keeps variants separate, imports only with consent, and rechecks hashes before existing transactional installation and restore.
- Personal Windows bundle assembler and portable ZIP/Inno installer script. Public workflow rejects binary inputs under `runtimes`; no private bundle is uploaded by CI.
- Secure Install entry point to experimental installation; automatic import of a selected bundled candidate, compatibility preview and confirmation without requiring expert-review JSON.
- Exact Borderlands 4 Steam/executable DX12 evidence, manual API override, bounded Unreal plugin/Streamline discovery.
- Opt-in periodic fresh diagnostics and automatic 30-second PresentMon captures with exact executable selection, runtime/backend attribution, optional NVIDIA sensor snapshot, cancellation and bounded automatic capture retention. Unavailable measurements stay unavailable; process exit is not classified as crash.
- Benchmark capture-condition inputs, uncontrolled-comparison warnings, local HTML/JSON/CSV exports and path redaction by default.
- FiveM development validation kit, separate from production recipes. Prior successful NR evaluation is a test outcome, not a first-test prerequisite. No new production plugin recipe is invented.

## Evidence and remaining validation

Initial inherited suite: 305 passed, 1 skipped, one new-string translation failure (subsequently addressed). New synthetic tests and final full-suite/build results are recorded below as they complete.

The OptiScaler Borderlands 4 wiki was read on 2026-10-21: version 0.9, Windows 11 24H2, RX 9070 XT, DLSS/FSR3.1/XeSS inputs and `dxgi.dll`. This is generic OptiScaler evidence, not proof for the separate Skynizz NR backend or Feature 18 evaluation. The experimental backend keeps its own pinned package and conflict checks; no game-execution claim follows from this wiki.

Windows builds require the existing GitHub Actions Windows runner or a local Windows toolchain. This Linux environment cannot execute NVIDIA/Windows/FiveM hardware tests. No RTX 5090/4080 success, malware audit, proprietary binary inclusion or permitted FiveM plugin contract has been established here.

FiveM: production deployment still requires an actual permitted exact client/plugin/protection configuration. The development kit exports inventory, policy blockers and a first-test plan, and the existing observation workflow can gather fresh evidence independently of a production recipe. It does not inject files or evade Pure Mode. No genuinely supported new loading contract has been found, so v1.4 does not claim a working FiveM NR deployment.

## Checkpoint validation

- Local full suite after the implementation refinements: **338 passed, 1 skipped**, Linux/offscreen Qt; the skipped test requires a Windows facility. These are synthetic/integration/UI checks, not RTX execution.
- Linux source GUI smoke: **AKENO 1.4.0 smoke test OK**, all eight application pages constructed, 19/19 design assets present; no Windows hardware detection claimed.
- First pushed checkpoint `90150d8`: GitHub Actions [run 39676367733](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/39676367733) succeeded, including Windows tests, packaged EXE smoke, portable ZIP and Inno installer. GitHub displayed two public artifacts. Public Windows package artifact digest: `07d83f2fad09648b557d44bbf73eddb8295f31c786f966b85b9c7d9dbdd16bfd`. This is the outer artifact digest, not an individual EXE/ZIP checksum. Later refinements require their own Windows run.
- The GitHub API is currently blocked with `Forbidden`; Git fetch/push and public workflow pages work. A narrow environment draft adding `api.github.com` was saved but is not active until the user reviews/saves/publishes it. Draft-PR creation and authenticated artifact download remain pending API access; no merge or force-push occurred.

## Practical limitations

Automatic monitoring runs while the community dialog/application remains open, starts only after opt-in, uses the selected live PID, and stops on disable, dialog close or an observed game exit. Sensor series are optional device-level NVIDIA readings, not per-process VRAM attribution. A missing/ambiguous sensor remains unavailable. Reports retain the newest bounded automatic captures and one current diagnostic snapshot per game. Backend-owned logs are bounded when read; AKENO does not rotate or rewrite a running game's foreign log files.

Baseline/experimental captures use a guided repeatable-scene workflow and user-entered game version, scene, resolution and settings. Missing or differing conditions are flagged uncontrolled. Game launch and visual-quality assessment remain user actions. The application does not presently correlate Windows crash events; ordinary exits and NR errors are reported without inventing a verified crash.

The proprietary Bandukids binary was not supplied. Personal packaging was tested with inert PE/archive fixtures and fake signature providers; the complete private PowerShell build with your real DLL still needs local Windows validation. Public Windows EXE/installer smoke tests do not establish real Neural Rendering or FiveM compatibility.

Final local rerun for `02f0d65`: **339 passed, 1 skipped**; 60.91 seconds. Subsequent focused checks cover resetting a new observation after a previous exit, preventing monitoring before installation, excluding bundled EXEs from public inputs, and fitting German benchmark export controls in a 1000-pixel content area. The final public Windows CI run is linked in the delivery message when complete.
