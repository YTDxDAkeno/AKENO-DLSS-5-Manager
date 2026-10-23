# AKENO DLSS 5 Manager 2.0 development

AKENO opens My Games and manages community mod versions, complete dependency stacks, presets,
normal game launch and restoration. Advanced diagnostics and all existing libraries, runtimes,
backups, PresentMon and English/German settings remain available.

- Original GitHub release API with ETags, pagination, cache, stable/prerelease browser and opt-in
  daily update checks. New metadata cannot silently replace reviewed installation versions.
- Pinned native OptiScaler NR 1.3.0 / 1.2.1 / 1.2.0, per-game version selection and complete
  Feeder 1.19 stacks with one eligible NR consumer.
- CI-built full add-on ReShade, GPL motion shader source, required headers, original Feeder
  release, complete corresponding source, license notices and SHA256SUMS in a dependency bundle.
- Exact-version local licensed RenoDX NR import for native/Feeder and scoped FiveM recipes.
- FiveM Legacy plugin-only installation, effective server configuration inspection, actual
  client ReShade restrictions, separate D3D11 renderer / private D3D12 transport and restoration.
- Real presets/custom controls, cancel/progress, upgrade/downgrade/reinstall, previous-version
  restoration and complete uninstall through verified transaction backups.

This is experimental community software. Installation success is separate from Feature 18
execution or visual contribution. No physical RTX 4080/5090 or FiveM success is claimed by CI.
Unsupported Pure/client routes remain unavailable. Generic legacy/x86 bridges, unreconciled
Dagherbou dependency sets and unmatched prerelease stacks are not represented as installable
complete recipes. Proprietary runtimes and protected NR consumers are not bundled.

Build artifacts are valid only for the attached successful Windows build and its SHA256SUMS;
Linux tests alone do not establish that a Windows EXE was built. See COMMUNITY_COMPONENTS.md and
AKENO_V2_FIVEM.md for contracts, licenses, setup and acceptance limitations.
