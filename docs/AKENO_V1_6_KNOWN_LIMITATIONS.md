# v1.6 delivery limits

Implemented software: execution schema 2, existing real OptiScaler source diagnostic
v3 (rect evaluation, CPU stalls and dispatch counters), guided optional local build,
official NR inventory selection/revalidation, SR/NR self-test phases and local JSON/ZIP,
normal offline game launch after observation, latched GPU-failure diagnostics, separate
FiveM read-only route/three modes and EN/DE UI. Backup/restore, official SR hotfix,
PresentMon separation and automatic no-UAC policy are preserved.

Still not established:

- **Milestones A–D**: none newly achieved on physical hardware in this task.
- No supplied intact authenticated NR runtime, verified `sl.dlss_nr` plugin, measured
  RTX 5090/617.42 Feature-18 execution, or local BL4/GTA/FiveM files/hardware.
- Streamline 2.14.16 inspected ZIP lacks NR runtime/plugin. Official DLSS news pages
  were proxy-denied; their release claims were not independently confirmed here.
- Exact NR creation error codes are unavailable on pointer-only forwarder create
  paths. Normal DX12 successful calls may remain silent without optional diagnostics.
- V3 covers the pinned DX12 forwarder/driver-proxy backend, not unreviewed RenoDX,
  arbitrary newer forks, DX11 dispatch counters or every Vulkan path.
- CPU evaluate duration is not a GPU timestamp/fence. GPU-completion, correct graphics
  device in game, image contribution, genuine rendering freezes/VRAM pressure and
  stable playability require additional permitted backend/hardware measurements.
- No completed corresponding-source/dependency/license binary release for the optional
  derivative. The local build is provided instead. Git/MSVC/SDK/network are required;
  no reproducible byte-identical PE claim is made.
- No reviewed FiveM ReShade/NR plugin loading method or complete D3D11/private-D3D12
  transport recipe. Module inspection intentionally leaves the actual renderer Unknown.
  FiveM deployment and multiplayer authorization remain blocked, including owner-only
  assertions. The diagnostic view is not an installation recipe.
- No RenoDX/Chicken consumer package, speculative bridge, missing shader/provider,
  NVIDIA verification bypass, protection bypass, TDR edit or base GTA/Online modification.
- No automatic game screenshot/video hook or objectively verified display-contribution
  badge. Existing user-imported A/B workflow remains an observational comparison.

CI proves software tests, source compilation and packaging only. Final build/run
links are provided in the delivery report. The next hardware action is the minimal
signed-runtime/optional-counter comparison in [hardware acceptance](AKENO_V1_6_HARDWARE_ACCEPTANCE.md),
not repeated installation of the same bytes. The exact NVIDIA/Cfx licensing and
platform restrictions remain in force.
