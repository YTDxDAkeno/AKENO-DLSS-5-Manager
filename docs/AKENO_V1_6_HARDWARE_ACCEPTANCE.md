# v1.6 local hardware acceptance

No physical RTX/GTA/FiveM session was performed in CI or this executor. The original
full user logs and NVIDIA binaries were not attached to this task; the supplied
hashes/results are recorded observations, not newly verified files.

## Minimal first diagnostic on RTX 5090 / driver 617.42

1. Keep existing transaction 5/history/backups. Do not reinstall unchanged components.
   Open Rendering pipelines → Runtime self-test. Confirm the imported official SR
   310.9.1.0 hash starts `3975567b8943c53a`.
2. If legally available, import an intact NVIDIA-signed `nvngx_dlssnr.dll` through
   Official DLL management with permission for local reuse. Select its exact hash
   in the neural-runtime list. A version or filename is insufficient. If none is
   available, record **Missing authenticated NR runtime**; do not download random
   mirrors or modify NVIDIA signature verification.
3. For exact counts, click **Build optional diagnostic backend locally**. Prerequisites:
   Git for Windows, Visual Studio 2022 C++ Build Tools/v143, Windows 10/11 D3D12 SDK.
   Import the generated private `diagnostic-manifest.json` via Import diagnostic source
   build. This optional local compilation changes no game files.
4. Explicitly start the isolated test. Compare the unchanged community candidate and
   a signed candidate only as separate user-selected runs; there is no automatic
   loader fallback. Record JSON/ZIP under `%LOCALAPPDATA%/AKENO/self-tests/<id>/`.
5. Check matched GPU PCI/LUID/driver, process PID/birth, consumer binary pins, runtime
   signature, secure-loader result, capability query, create and evaluate return
   counts. SR 300/300 alone is **not Milestone A**. GPU-completion/display fields must
   remain unconfirmed unless an independently suitable measurement exists.

If secure loading rejects the community file, use a permitted signed candidate;
reinstalling the same bytes cannot repair its signature. If the capability query
returns NotImplemented, inspect that exact dispatcher/runtime/driver contract. Do
not force support. If counts cannot be obtained, export the inconclusive result.

## Track A: Borderlands 4, Steam 1285190, DX12

Use the original exact executable and an eligible offline context. Preserve the
current transaction; restore explicitly before changing the installed consumer.
Preview only the evidenced native OptiScaler route and exact files. Use the signed
candidate if legitimately available; keep runtime/consumer/driver changes separate.

In Execution evidence choose **Launch offline game test** after setting offline mode
in the game/launcher. Monitoring starts before normal launch. Play a repeatable
scene for at least 10 minutes, exit normally, repeat from a cold process at least
three times. These durations are an acceptance protocol, not an automatic stable
badge. Record fresh game-specific Feature-18 create/evaluate results, errors,
fallback, freeze/device-removal observations and session-specific PresentMon data.
A launch request, loaded DLL or standalone result is not **Milestone B**.

Capture matched real NR-off/on images/video with settings, resolution, camera,
transaction and component identities. Static comparisons alone do not establish
output provenance or temporal quality. Keep application FPS, generated frames and
displayed FPS separate. NR may lower FPS. Restore with the existing backup workflow
if a test fails; never overwrite foreign mods or every duplicate DLSS DLL.

## Track B: FiveM Legacy

First run only the **read-only Neural Rendering analysis** in documented singleplayer
or a lawful isolated development setup. Export exact client executable/version,
plugin folder/module observations, GPU/driver and policy. Actual game renderer or
loader Unknown is a blocker, even when D3D12 modules appear.

Do not deploy until an independently reviewed permitted plugin method, renderer
transport, complete licensed consumer and motion/depth inputs exist. Current AKENO
ships no such recipe. It therefore cannot reach **Milestone C** on the evidence
available here. When a future recipe is available, repeat the game tests above and
inspect stalls/DRED without changing TDR or Pure Mode. One evaluate followed by
DEVICE_HUNG is a failed stability test.

**Milestone D** additionally needs independently verifiable authorization and exact
client/component/server policy support. Operator ownership/checkboxes are insufficient.
Pure level 2 and unsupported level-1 graphics modifications remain blocked. Existing
restoration and ordinary supported game graphics settings are the permitted fallback.
