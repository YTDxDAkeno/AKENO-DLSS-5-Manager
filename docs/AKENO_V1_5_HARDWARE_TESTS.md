# AKENO v1.5 physical hardware acceptance

No physical RTX GPU, Borderlands4 installation or proprietary runtime was available in the development environment. CI fixtures and successful compilation are not physical NR evidence. Preserve the current game installation and transaction before testing.

## RTX5090 / Borderlands4 first

Known user configuration: RTX5090 32GB, Windows11, driver617.42, Steam1285190, DX12, `OakGame/Binaries/Win64/Borderlands4.exe`, Skynizz OptiScaler1.3.0, Bandukids NR310.8.0.0 SHA-256 `8270b350cd82de5ce89806872cdd6b6a9249b80836b91bbeb3573470744cc206`. The modified signature is not NVIDIA authentication. Existing evidence says backend/runtime loaded and evaluation attempted; it does not establish a successful NR result.

1. Launch the new AKENO build, scan hardware and select the exact game executable and DX12. Confirm the GPU device ID, driver and runtime hash. Do not replace every `nvngx_*` copy found in Engine/Plugins.
2. Open the existing installation report. Preserve transaction5, its backup chain and effective INI. Start a fresh verification session before launching the eligible offline game. A late-start warning means initialization may have occurred before the cursor.
3. Record the expected proxy and the path-matched actual module snapshot for this executable/PID/birth identity. If it is unavailable, retain **unknown**. Do not elevate/inject or bypass protection to force inspection.
4. Record actual NGX initialization, Feature18 creation attempts/result, evaluation attempts/result and exact errors. Generic SR/FG logs, a model filename and absence of errors do not establish NR success.
5. If normal Skynizz1.3.0 logs lack success evidence, stop the game and preserve the report. This is a diagnostic limitation, not automatic proof of failure and not a reason to repeatedly reinstall the same stock files.

## Standalone feasibility, separate from the game

1. Import the same local runtime with its source/local-use permission. Import an NVIDIA-authenticated SR runtime for the Feeder host contract. Keep Windows protections enabled.
2. In Rendering pipelines → Runtime self-test, explicitly start the test. AKENO creates an isolated private directory, verifies the pinned Feeder host/OptiScaler components and records the actual host adapter/driver and runtime module when observable.
3. Read `dlss_evaluations` separately from `neural_evaluations`. `300/300` SR/DLAA evaluates with no explicit Feature18 result means **inconclusive NR**, not pass. Record init/create errors such as `0xBAD00001` without reinterpretation.
4. To obtain bounded NR counters, build the exact source-identified optional diagnostic backend using the procedure below. The test must report exact build/runtime pins and match the selected GPU, driver and host process lifetime. A pass says **Runtime/consumer self-test successful**, never “DLSS5 works in Borderlands4.”
5. Export the JSON. Preserve setup errors, cancellation, timeout, unmatched adapter/module limitations and failed counts. Do not relabel them as successful compatibility.

## Optional diagnostic backend, explicit local build

This requires a Windows C++ build environment compatible with upstream (MSBuild/v143 and upstream's pinned dependencies). It is not needed to run the normal AKENO app. Do not execute upstream installers, signature-policy registry scripts or arbitrary mirrors.

```powershell
git clone https://github.com/Skynizz/optiscaler-dlss5.git optiscaler-akeno-diagnostic
cd optiscaler-akeno-diagnostic
git checkout fc702f8ca22f97b01bd6a02f3386bffb3484fc35
git submodule update --init --recursive
cd ..
# From a Visual Studio developer shell; point this at the AKENO source checkout.
powershell -File AKENO-DLSS-5-Manager/tools/build-nr-diagnostic.ps1 -Source optiscaler-akeno-diagnostic
```

The script adds only the published GPL counter instrumentation, builds the backend and creates a private import directory containing the two compiled backend files and a diagnostic manifest. It never includes/changes `nvngx_dlssnr.dll`, installs into a game or changes signature policy. Retain the exact modified source, manifest, upstream license/notices and submodule revisions. Public redistribution of compiled dependencies requires its own source/license compliance review.

Import `akeno-private-diagnostic/diagnostic-manifest.json` in Rendering pipelines with explicit local experimental consent. The imported source declaration is not independent security certification. A new prepared installation preview shows the exact diagnostic DLL hashes. If a required staged backend/runtime/component changes, prepare again; do not force an overwrite of unmanaged mods.

To test the game with it, first finish/export the existing stock session. Keep its restore point. Preview the diagnostic route only after closing the game. Start a **new** verification session after installation. The backend must publish `AKENO_NR_V1` summaries with its exact build, selected PID and Windows birth identity. AKENO must observe the path-matched managed module and reject old/replayed/mismatched summaries. Counters represent NR API calls, possibly multiple passes/probes, and periodic observed lower bounds; they do not count distinct displayed frames or prove visual quality/GPU completion.

## Repeatable baseline and performance

1. Capture an unmodified baseline through restore, or explicitly attest a known NR-disabled configuration. A baseline label by itself is not independent proof that NR was disabled.
2. Enter game version, exact scene/camera path, gameplay/menu/loading classification, resolution, renderer, graphics settings, DLSS mode and FG mode. Repeat the same scene after the experimental route is active.
3. Start FPS capture explicitly. Automatic monitoring must never show UAC. If ETW permission is unavailable, leave FPS off and continue log diagnostics. An administrator can independently configure Performance Log Users; AKENO does not modify membership or Windows security. Any manual UAC request is per-capture opt-in only.
4. Select the intended swapchain when ambiguity is reported. Inspect application-rendered FPS, displayed FPS and generated share separately. Do not use an unvalidated legacy median as gameplay evidence.
5. Ensure the capture is attached to this transaction/session/process birth and its configuration remains unchanged through the end. Old captures predating transaction5 remain historical. Stop monitoring or exit the selected game and confirm no stale capture remains.
6. The desktop HUD displays recent NR status and **last completed capture** metrics/timestamps. It does not claim live instantaneous FPS or guaranteed exclusive-fullscreen visibility.

## Visual comparison

Use the same viewpoint/settings/resolution/renderer with NR off/on. Import real matched screenshots into Visual A/B comparison and retain their hashes, conditions and local HTML. Report sharpness/detail/HUD observations as observations; use a matched video for temporal flicker/ghosting. No AI-generated image or FPS improvement proves image quality. Current screenshot labels/conditions are user attestations and do not independently establish capture-time NR state.

## RTX4080 second

Repeat the standalone procedure with RTX4080's exact device ID/driver/runtime/consumer/backend/API. The known Bandukids hash may be a local experimental candidate when its profile permits it; ComfyUI provenance does not establish game support. Run an eligible offline game only after recording standalone results. Keep exact init/create/evaluate counts/errors, GPU/VRAM/capture conditions and stability evidence. A result on one RTX4080 does not validate all RTX40; a result on RTX5090 does not validate RTX4080, or vice versa. RTX20/30 need applicable validated profiles before an experimental deployment.

## FiveM

Use the preserved FiveM development/policy workflow. A privately owned server is not, by itself, permission for a graphics route. Record client version, supported loader/plugin interface and server Pure Mode. Do not bypass anti-cheat, integrity checks or Pure Mode. Offline development evidence is separate from multiplayer deployment. No FiveM NR success is claimed by this release.

## Acceptance record

Save exact hardware/driver IDs, executable SHA, game/renderer/config, runtime/consumer/backend versions and hashes, selected/observed proxy, process birth, host/game scope, init/create/evaluate results, separate SR/NR counts, error codes, capture conditions, measurements, real-image comparisons and restore outcome. Missing stages stay unknown. An ordinary process exit is not a verified crash.
