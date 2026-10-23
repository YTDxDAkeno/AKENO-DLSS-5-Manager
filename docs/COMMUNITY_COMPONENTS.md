# Community components in AKENO 2.0

AKENO now opens **My Games**. Choose a game and a reviewed version, confirm the eligible
session and experimental component use, and click **Install DLSS 5**. The worker resolves
permitted local runtimes, bundled dependencies and original release downloads, stages the
complete stack, and presents the exact file list. Only confirmation applies the transaction.
No 300-frame test or diagnostic source compilation is required. Installation and Feature 18
execution are separate results.

**Delivery status:** source implementation and local synthetic tests are available. The v2
Windows bundle, EXE and installer require the updated Windows Actions job to run. This session
cannot publish them: Git HTTPS authentication is unavailable and `api.github.com` is denied by
the execution environment's network policy. These are not v2 artifacts already released.

## Reviewed version catalog

Authority: `akeno/data/community/catalog.json` (schema 2). It retains original repositories,
release tags/dates/assets, published archive SHA-256, used member SHA-256, source identity,
license, distribution policy, architecture, API, dependencies, presets and known limitations.
Remote API discovery cannot grant installation authority or replace a pin. Versions are
selected per game; another game's selection does not replace a working version.

| Original project | Reviewed versions | Use |
| --- | --- | --- |
| Skynizz/optiscaler-dlss5 | 1.3.0, 1.2.1, 1.2.0 | Complete native upscaler + NR consumer/forwarder/configuration; exact local NR runtime required |
| jlrouzies-fr/DLSS5-Feeder | 1.19.0 | Complete x64 Feeder recipe with ReShade, motion/depth, authenticated SR and one NR consumer |
| jlrouzies-fr/DLSS5-Feeder | 1.18.0-beta.2, beta.1 | Reviewed archive/member pins, visible in prerelease browser; no complete matching install recipe yet |
| Dagherbou/OptiScaler_DLSSNR | 0.2.0-dlssnr, 0.2.0-patch1, 0.1.2-dIssnr | Visible versions; large AMD/Intel/D3D12 dependency set still needs complete deployment review |
| Local licensed RenoDX DLSS5 consumer | Exact user-imported version and SHA-256, ConfigVersion 7 | Native D3D12, Feeder D3D11/D3D12 and FiveM Legacy recipes; format checks and declarations do not authenticate the binary |

OptiScaler 1.3.0 / 1.2.1 / 1.2.0 maintainer `.zip.sha256` sidecars were fetched separately
and matched the original archives. Feeder published GitHub asset digests matched the downloaded
archives. The validator re-extracted all selected members using the production safe extractor.
Scripts in upstream archives are never executed or copied into the game.

Other catalog projects are release-discovery/research sources, not interchangeable NR
consumers: upstream OptiScaler, ReShade, RenoDX, RHI / rhi-repo, FiveM-DLSS5 and DLSS-Enabler.
An HDR add-on, an SR evaluate or an installer EXE does not establish Feature 18 support.

## Complete dependency bundle

Windows Actions checks out **ReShade v6.8.0, commit
18deaa52de0c425a78b329e9cb3c497281cd00ec**, including its pinned submodules. The ordinary Release
x64 configuration has `RESHADE_ADDON=2`. Its real DLL metadata and full add-on exports are
checked before packaging. ReShade keeps its own name, ProductName, attribution and BSD notice.

`tools/build_community_bundle.py` creates the versioned dependency archive and the app's
read-only `community/dependencies` data. It includes:

- ReShade64.dll, BSD-3-Clause, built in CI; complete corresponding source tree and submodule
  sources, license files and generated build identity. No user needs Visual Studio.
- AlucardDH `dh_uber_motion.fx` 0.2.0, source commit
  `0783b8fde77df48412d79070be7de4bc3743b898`, GPL-2.0, unchanged source plus full license.
  It estimates motion; it does not provide the game's geometric motion vectors.
- Required `ReShade.fxh`, source commit `fd0022170615ce0d8162d219bff07232fa6dd84f`, explicitly
  CC0-1.0 in its header. No unrelated shader library or restricted motion package is bundled.
- The original pinned Feeder 1.19.0 archive, its matching source archive at
  `3c09eabcd138a68d144b6cc396a23e6b493eea4d`, MIT notices including the bridge's notice.
- A generated component manifest, per-file SHA256SUMS, source hashes and source identities.

ReShade FXC compiles the selected motion effect and original Feeder effect for D3D11/D3D12
before packaging. Runtime depth-buffer selection and optical-flow quality still need checking
in the game. Compilation cannot establish input quality or successful NR execution.

No NVIDIA runtime, Streamline runtime, proprietary RenoDX consumer, Lumenite/AGNYA payload,
unknown Discord binary or complete uncertain upstream snapshot is included in public Git or
this bundle. Skynizz binaries download from the original release inside AKENO rather than
being repackaged without the complete GPL distribution review.

A legally held SR/NR runtime can be discovered and imported once after consent. Official SR
is independently checked for NVIDIA Authenticode identity. An unsigned/modified NR file stays
experimental and source-declared; it never satisfies the official SR check. Windows signing,
NGX secure loading, driver requirements and client protections are unchanged.

## Recipes and settings

Native OptiScaler recipes require an established engine upscaler input contract and a free,
evidenced supported proxy. A versioned game profile or existing engine import is input evidence,
not proof that the current game evaluated NR. There is no generic `winmm.dll` assumption.

Feeder + OptiScaler uses ReShade through an evidenced DXGI hook and a **different evidenced
OptiScaler loader**. Feeder's D3D11 game-device path uses a private D3D12 consumer device;
OptiScaler is configured for that D3D12 consumer with Pre-SR off. The game renderer remains
D3D11. A complete Feeder + local RenoDX recipe needs only the reviewed ReShade hook and its
add-on consumer. Mutually exclusive consumers cannot be mixed.

OptiScaler presets use real per-version `DlssNr` keys. Custom controls derive from the backend's
ParamSpecs; unsupported keys/ranges are rejected. RenoDX ConfigVersion 7 presets use actual
`NeuralUplift`, `NRResolutionScale`, `NRPasses`, `NRStyle` and `NREnableUpscaling` keys from the
reference configuration. Output upscaling remains off to preserve the frame dimensions.
Feeder's `preset` field is an SR hint, not an NR quality setting. No FPS is estimated.

Seven initial game profiles cover Borderlands 4 (Steam 1285190), Cyberpunk 2077, RDR2
(1174180), Control, The Witcher 3, GTA V Legacy offline and FiveM Legacy. Exact executable
paths/APIs are required. Additional existing non-DLSS games can use a complete reviewed Feeder
route. Profiles are research/input contracts, not blanket compatibility badges.

The dedicated father/RTX 4080/FiveM profile selects Performance, explicitly eligible local
community runtimes and reversible tests. The RTX 5090 profile starts with Quality and considers
authenticated compatible NR runtimes. Related RTX 4080 Super issue #63 includes evaluate stalls
and device removal; its closure is not proof of a fix or proof on the father's RTX 4080.

## Version management and recovery

The version browser shows stable releases, selected prereleases, cached API releases, exact
source/license/API/dependencies/checksum and known issues. **Use selected version** chooses a
complete reviewed recipe. Unknown/new versions remain review proposals. API requests use
pagination, ETags, bounded reads, strict original-repository asset URLs and a persistent cache.
Daily notifications are opt-in. Removed/altered assets are flagged; a valid cached pinned copy
can still be reinstalled offline. No automatic replacement of a working mod occurs.

Upgrade, downgrade and reinstall use the existing transaction journal and atomic backups.
**Restore previous version** writes a new reversible transaction from verified prior backups,
including removal of version-only files. **Remove mod completely** follows the whole ownership
chain back to original files. Changed foreign/managed bytes and damaged backups stop mutations.
Preset changes are journalled in the same chain. Interrupted transactions remain recoverable.

Maintainer commands:

```bash
python tools/sync-community-releases.py --out release-proposal.json --include-prereleases
python tools/validate-community-catalog.py
python tools/validate-community-catalog.py --archive-dir /path/to/original-release-zips
```

The sync command calls the real GitHub release API and outputs a proposal. It preserves trusted
versions, reports missing/altered assets and requires a reviewed catalog change to approve a new
release. It never runs an installer or edits installed games.

Imported component versions are retained in the local library by SHA-256. The version browser
can select an exact imported NR consumer for one game. Importing a newer consumer preserves
the previous version, and previous-version restoration also restores the selected consumer pin.
