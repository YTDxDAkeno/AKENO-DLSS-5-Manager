# v1.6 FiveM Legacy feasibility and implementation

**Status: read-only analysis available; NR deployment remains blocked.** This is a
separate FiveM route, not a GTA base-game proxy install. Modes are offline/singleplayer
development, authorized-server assessment, and unsupported/blocked session.

## Primary Cfx evidence

The docs website was proxy-denied, so the official documentation source was read at
[c43ff1122035c1b417ea67f0fe6c9bdc5b761726](https://github.com/citizenfx/fivem-docs/tree/c43ff1122035c1b417ea67f0fe6c9bdc5b761726):

- [`content/docs/client-manual/_index.md`](https://github.com/citizenfx/fivem-docs/blob/c43ff1122035c1b417ea67f0fe6c9bdc5b761726/content/docs/client-manual/_index.md)
  documents customized FiveM singleplayer and **certain .asi plugins** under the
  application data plugins folder. Servers can disallow plugins. This is not a
  blanket contract for any ReShade proxy DLL/NR add-on.
- [`server-commands.md`, sv_pureLevel](https://github.com/citizenfx/fivem-docs/blob/c43ff1122035c1b417ea67f0fe6c9bdc5b761726/content/docs/server-manual/server-commands.md#sv_purelevel-level):
  level 1 blocks modifications except audio and known graphics mods; level 2 blocks
  all modified client files. Enhanced always enables Pure Mode and its early-access
  graphic-mod restrictions differ from Legacy.

No security configuration, signing policy, memory protection or loader restriction
is changed. Server ownership and operator checkboxes are declarations, not verifiable
client authorization. The older validation-kit output now explicitly marks this
unverified and cannot say a first plugin test is eligible from checkboxes alone.

## Independently checked community integration reference

[kerim0x1/FiveM-DLSS5](https://github.com/kerim0x1/FiveM-DLSS5/tree/24c25313f13acc358f1f018cd2f59186e3576173),
commit `24c25313f13acc358f1f018cd2f59186e3576173`, MIT wrapper license:

- Read `profiles/fivem.json`, `docs/en/architecture.md`, `docs/en/clients.md`,
  `scripts/Verify-FiveMDLSS5.ps1`, install script and public tree.
- Profile declares D3D12 and `plugins/d3d11.dll`, forbids helper mode and assumes
  in-process Feeder + RenoDX + Lumenite Kernel. These are claims, not measured
  game-renderer facts or proof that the plugin loader permits this composition.
- The installer can snapshot an existing local plugins directory. Public source
  does not provide all proprietary binaries its workflow describes. Its MIT license
  cannot license NVIDIA DLLs or the separately licensed motion shader/NR consumer.
- AKENO does not run/copy its installer, overwrite unrelated files, import its
  no-helper assumption, or treat a file-existence verifier as NR success.

## Complete rendered Feeder issue #63, including later replies

[Issue #63](https://github.com/jlrouzies-fr/DLSS5-Feeder/issues/63) reports RTX 4080 SUPER,
Feature-18 creation and at least one successful evaluate, followed by DEVICE_HUNG
`0x887A0006`. Early discussion implicated barriers/allocator retirement; this was
subsequently narrowed rather than confirmed as the root cause.

The later trace explicitly says **cross-API transport with Feeder's own private
D3D12 device**, not D3D12 game rendering. It records a 25029.52-ms NGX evaluate
within a 25128.47-ms feed interval. In the September 14 response the maintainer
locates the stall inside Feature-18/RenoDX/driver work rather than Feeder's own
barriers. This does not identify a particular driver/consumer bug. The September 22
closure was **not planned / lack of feedback**, not a verified fix. The posted
signed-snippet/force-create observations are not adopted as a manager workaround.

AKENO records device removal/reset/hung, allocator retirement warnings, DRED presence,
NGX-evaluate stall time and NR fallback observations. It does not raise TDR delays
or patch barriers/resource states based on historical guesses. First success is not
stable rendering; current exact versions must be retested in repeated sessions.

## Implemented read-only route

Games → FiveM → Secure Install → **Neural Rendering**:

1. Choose one of the three workflows and Analyze FiveM Neural Rendering.
2. Inspector selects exactly one in-root `FiveM[_bNNNN]_GTAProcess.exe`, captures
   PID/birth, actual client executable version/hash/metadata, loaded API modules and
   plugin paths through existing read-only Toolhelp inspection.
3. It distinguishes configured SavedBuildNumber and base GTA API from actual process
   behavior. Loaded d3d11/d3d12 libraries are candidates only. Actual renderer, device
   adapter and helper API stay Unknown without suitable measurements.
4. Show missing loader, transport, licensed consumer, depth/motion and runtime contracts;
   export a redacted report. No guessed DLL path or normal GTA folder is written.
5. Existing reviewed-plan/backup/restore policy remains; no real reviewed FiveM NR
   recipe is inserted into the empty production registry.

Executable updates, process restarts/PID reuse and client/plugin/config fingerprints
invalidate observations. A path observed once does not establish permitted loading
or authority for a future session. Pure Mode remains authoritative; no multiplayer
recipe or verified server allowlist is supplied. Exact per-file client/server
compatibility and lawful loading evidence are required before deployment can be
implemented. Existing GTA files and GTA Online are never targets of this route.
