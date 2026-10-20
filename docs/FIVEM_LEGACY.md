# FiveM Legacy integration (experimental, opt-in)

The FiveM-specific development pipeline is implemented under `akeno/fivem`. **No FiveM neural-rendering backend is currently approved for automatic installation. RTX 4080 / 16 GB has not been tested.** This is a deliberate result of the evidence review, not a claim that a generic DLSS package will work in FiveM.

## What is implemented

- Read-only discovery from `%LOCALAPPDATA%/FiveM`, standard Windows uninstall metadata, explicit portable locations and known running FiveM executable paths. A candidate needs `FiveM.exe` and `FiveM.app`; a GTA directory alone is never accepted.
- `CitizenFX.ini` / `IVPath` inspection, positive Legacy/Enhanced separation, executable PE architecture/API inspection, and bounded inventories of the application, plugins/mods and base-game graphics files. Ambiguous/missing editions stay unknown. `SavedBuildNumber` is only a configured preference.
- FiveM process classification independent from GTA V. `GTA5.exe` alone cannot distinguish story mode from GTA Online; the result explicitly says session unknown. Neither session is an installation target.
- Explicit operator permission, effective server-policy and experimental-consent gates; GPU, driver, artifact and signed-runtime evidence checks. No driver minimum, VRAM fit or performance figure is invented.
- An opt-in wrapper for the existing `dlss5_feeder` and `optiscaler_nr` BackendAdapters; exact source/output pins and a fixed `FiveM.app/plugins` target. The default registry, shared models, launcher, design and dependency installer are untouched.
- Plans staged with AKENO's existing downloader/archive verification, then applied by its Transaction Installer. The facade rechecks permissions, processes, client fingerprint, source hashes and plan integrity before applying. It supports rollback, post-copy validation, restore conflicts and snapshots.
- Symlinks, Windows junctions, unknown plugin binaries, foreign transaction temporary files, edited plans and changed runtime files are refused. Foreign plugins are never automatically disabled or overwritten.
- A read-only CLI, plus synthetic filesystem/PE/transaction tests. No upstream installers, FiveM binaries or proprietary runtime downloads are executed by this integration.

## Development and inspection

Python 3.11+ is required; the cloud instance was tested with Python 3.12. The repository's pinned development dependencies are sufficient:

```bash
cd /workspace/AKENO-DLSS-5-Manager
python3 -m venv /workspace/akeno-venv
/workspace/akeno-venv/bin/python -m pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen /workspace/akeno-venv/bin/python -m pytest -q
/workspace/akeno-venv/bin/python -m akeno.fivem
```

For portable Windows installations:

```powershell
python -m akeno.fivem 'D:\Games\FiveM'
```

The JSON includes edition, separate client/GTA paths, detected API/architecture, configured build/channel, inventory hashes and inspection errors. `approved_backends` is empty. Local paths can identify a user; review a diagnostic report before sharing it. The command does not install, fetch packages, change server settings or launch a game.

## Is an owned server suitable?

**Conditionally suitable for a controlled compatibility experiment; not sufficient to establish neural-rendering support or ban safety.** The operator must explicitly allow the exact graphics stack and verify the effective server settings, including included configuration files and server-side protection rules. AKENO does not silently inspect arbitrary server endpoints or treat a public `info.json` response as permission.

The official FiveM Legacy client manual describes a `plugins` directory inside the application data directory and says servers may disallow plugins. Server documentation says:

- `sv_pureLevel 1`: blocks modified client files except audio and *known* graphics mods. An arbitrary DLSS/NGX injector is not automatically a known graphics mod. The gate requires exact approved executable hashes.
- `sv_pureLevel 2`: blocks modified client files; the integration blocks deployment.
- `sv_scriptHookAllowed`: default false; true permits clients with Script Hook V. The official documentation warns that enabling it creates server security exposure. It is **not** a blanket graphics-plugin permission. A recipe requiring Script Hook needs explicit permission, but AKENO does not change this setting or tell the client to bypass it.
- The Enhanced migration document says pure mode is always enabled, and graphics mods cannot be used during Enhanced early access. Enhanced remains outside this Legacy integration.

An owned, isolated server with explicit permission and a compatible effective policy addresses only the server-policy prerequisite. It cannot make an unsupported signed NVIDIA runtime create/evaluate a neural feature on an RTX 4080. If a server declines the plugin, restore/remove it before connecting. No protection, signature or loading restriction is bypassed.

## Backend disposition

See [FIVEM_RESEARCH.md](FIVEM_RESEARCH.md) for pinned source links and limitations. `projects.py` contains the same candidate dispositions for later launcher presentation.

- `dlss5_feeder`: candidate frame feeder for games without a native temporal-upscaler contract. It needs an independently installed/reviewed ReShade build with full add-on support, useful depth/motion inputs, legal NVIDIA runtimes and **exactly one** neural consumer. Feeder success or DLAA output alone does not prove neural rendering.
- `optiscaler_nr`: candidate consumer. A Legacy installation does not gain native DLSS inputs by dropping a DLL into a directory. A measured input/feeder route and FiveM plugin-loading behavior must be reviewed first. The adapter preserves existing backend requirements and refuses unreviewed proxy-name fallbacks.
- FiveM-specific repacks/installers: none accepted into the automatic package catalog. Several explicitly rely on patched runtimes or publish insufficient implementation/provenance evidence.

There is no automatic fallback from neural rendering to ordinary DLSS SR, frame generation, ReShade sharpening or a patched model with a "supported" label. The RTX 4080 has relevant hardware capabilities, but that is separate from this runtime/model and FiveM route being supported. The retrieved NVIDIA developer DLSS page describes DLSS 4/4.5 features; it does not establish official FiveM DLSS 5 support.

## Integration contract for the launcher follow-up

Use `FiveMService(app_services)` from `akeno.fivem.service`; it reuses the existing registry, catalog, compatibility database, runtime import and transactional storage. Keep the existing checkout: cloud tasks are already isolated, and no Git worktree is needed. Coordinate UI/service wiring with the current launcher branch before changing shared entry points.

Read-only integration example:

```python
from akeno.fivem import ServerPolicy
from akeno.fivem.service import FiveMService

five_m = FiveMService(app_services)
for installation in five_m.discover():
    adapter = five_m.adapter(installation, 'dlss5_feeder', ServerPolicy())
    result = five_m.compatibility(adapter)
    # Default is blocked, with specific missing-permission/evidence findings.
```

Future UI work must preserve the `FiveMInstallation` alongside its shared `Game` and keep the FiveM identity out of the generic GTA/manual discovery path. `as_raw_game` is the Game Discovery bridge; generic reanalysis alone loses FiveM-specific evidence and target rules. Do not call the generic UI install path for these plans: use `FiveMService.prepare`, `install(prepared, current_policy)` and `restore`. Do not register a permissive adapter globally or change generic safety defaults to make FiveM pass.

`BackendReview` is **maintainer-reviewed local evidence**, not a user-supplied JSON preference or an upstream claim. It pins the backend package, client/configuration/inventory fingerprint, relevant upstream commit, API, exact PCI GPU identity/generation, tested dedicated VRAM, tested driver versions, signed-runtime hashes, exact pre-existing plugin hashes and exact output file layout. It requires successful creation **and evaluation** of the neural feature with an unmodified NVIDIA-signed runtime. This version deliberately ships `REVIEWED_BACKENDS = ()`. Do not populate it from the synthetic test fixture.

The initial review is deliberately bound to an exact inspected client snapshot. Any client/configuration/plugin update invalidates that review and requires a fresh plan/review. This is not a fleet-wide compatibility database. Adding a general reusable support entry requires a separate review of version/build ranges. File restore remains available without renewed server/backend approval so that a modification can always be removed safely.

The only writable application target is `FiveM.app/plugins`. A backend target view uses a clearly labelled planning-only path to adapt the old `Game.target_dir` interface; no such executable is created or launched. The real Game continues to refer to FiveM.exe and the complete FiveM process scope. Runtime imports come from the user's legally obtained local copy through AKENO's existing signature-verifying import path; imports are rehashed and signatures rechecked before installation. Existing runtimes do not gain trust from their names.

`ServerPolicy` is operator evidence, not live remote attestation. Callers must present the current policy again at apply time. The implementation cannot prevent the user from changing servers after installation. A future UI must keep that limitation visible and offer restore before joining any other server. Application-level preflight checks do not defend against a concurrent hostile administrator swapping filesystem objects during the final write; close FiveM and other mod managers for the transaction.

## Real-machine acceptance protocol (not executed)

1. Use an isolated owned **Legacy** server. Record server artifact/version, effective pure/Script Hook settings and operator permission for the exact plugin hashes. Do not weaken a public server or install into GTA V/Online.
2. On the RTX 4080 machine, record Windows/build, PCI GPU identity, 16 GB dedicated VRAM detection, NVIDIA driver version, FiveM channel/build, GTA Legacy binary/API evidence and client fingerprints. Record the actual FiveM process/build rather than relying on SavedBuildNumber.
3. Audit all pre-existing hooks. Obtain runtimes legally, verify NVIDIA Authenticode, record hashes/versions, and establish feature creation/evaluation with that signed runtime on the exact GPU/driver. Stop if the neural feature refuses to initialize. Do not substitute a patched DLL.
4. Only after that evidence is independently reviewed, pin a backend recipe and exact plugin-loading layout. Verify licensing/provenance of ReShade, consumer, shader/input provider and every executable artifact. Keep upstream installers and broad overwrite instructions out of the AKENO transaction.
5. Close FiveM and mod managers; create a snapshot, inspect the plan, install, and keep transaction logs. Confirm the real FiveM process loads the intended plugin/runtime and that the neural pass actually evaluates. Observe depth/motion quality, HUD/text artifacts, stability, frame times and VRAM under representative scenes. Ordinary SR/FG success is insufficient.
6. Verify behavior under the permitted server policy, then restore and compare all original file hashes. Confirm GTX/GTA base-game files remained unchanged and test crash recovery/restore conflicts. Never claim guaranteed protection from bans.
7. Attach logs and measured results, including failures, before considering a supported recipe or final launcher UI integration. There are currently **no real-hardware acceptance results** in this branch.
