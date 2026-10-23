The app previously centered on diagnosis and single pinned packages. This change opens My Games
and lets the user select a reviewed community version, resolve its complete permitted stack,
preview/install it, change real presets, launch normally, and restore a previous version or the
original game. Existing v1.6 data, backups and optional diagnostics remain intact.

The schema-2 catalog pins actual original release assets and member hashes. A real GitHub API
client adds cached discovery/update proposals without granting remote metadata installation
trust. Complete native/Feeder and locally licensed RenoDX recipes use the existing transactional
engine. FiveM has its own plugin-only facade with effective configuration parsing and native
client/Pure checks; neither GTA files nor protections are modified. The Windows job builds a
licensed ReShade/motion/Feeder dependency bundle with matching sources and notices before
packaging the app.

Validation: production-extractor validation of six actual upstream release archives; source-pinned
motion and Feeder shaders translated by ReShade 6.8 FXC to HLSL for D3D11/D3D12; 490 passing Linux/offscreen
Qt unit/integration tests (3 native-condition skips) and EN/DE GUI fixtures, including 150% scale. Exact final counts are recorded in
AKENO_V2_DELIVERY.md. Windows CI and physical RTX/FiveM execution remain unverified until run.

Baseline: 6f5e876 on codex/akeno-v1.6-playable-nr-fivem. Remote history/fixes could not be checked
because this execution environment currently has no working GitHub Git authentication; API
publishing is separately denied by its network policy. No old branch was overwritten or merged.
