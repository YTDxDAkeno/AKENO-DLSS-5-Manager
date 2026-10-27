# AKENO Network / components — technical handoff

## Status and source baseline

Development branch: `feature/akeno-network-modular`. Base: `2069ff7c58d2c52473023ce759bdbfe863319713` on `claude/adoring-shannon-2d4qb4`, integrating the 3.1.1 hotfix. `main` contains only the initial README. Resolve the current commit with `git rev-parse HEAD` and compare `git ls-remote origin refs/heads/feature/akeno-network-modular`; never infer a push from a local commit. Checkpoints are pushed after small steps; progress is in `AKENO_NETWORK_PROGRESS.md`.

This is a development checkpoint, not the completed master request or a release. No production API/VPS, release key, license, firewall or routing change was made. No Windows driver/installer/tunnel result is claimed.

## Actual component boundaries

| Existing area | Code | Current packaging |
|---|---|---|
| Core, game library, configuration | `akeno/app.py`, `services.py`, `core/`, `games/` | Required/shared |
| Account, licensing | `akeno/licensing/`, `ui/pages/account.py` | Required; existing license checks unchanged |
| Signed updates and rollback | `akeno/updater/` | Required; signature, hash, archive limits and health checks retained |
| DLSS, FiveM, mod installs | `stack/`, `backends/`, `install/`, `fivem/` | Bundled; share Core facade, inventory and UI |
| FPS optimization, benchmarks | `performance/`, `benchmark/` | Bundled; not independent packages |
| Doctor, Cleaner, Network Lab, mod scanner | `system/{doctor,cleaner,network,mods}` | Bundled, lazy services |
| Security Center | `system/securitycenter/`, `ui/pages/security.py` | **Actually separate eight-file optional group** |
| Desktop Studio, wallpapers, dock, widgets | `desktop/`, `ui/pages/desktop.py` | Bundled; host/renderers share Qt Multimedia/WebEngine |
| Community, sync | `community/`, `sync/` | Bundled; no VPN-profile sync added |
| LAN control-plane prototype | `server/akeno_server/network/rooms.py` | Testable internal API only; no public API registration |
| WireGuard profile import/storage | `akeno/network/profiles.py` | Internal API only; no UI/tunnel service |

Do not describe the remaining bundled features as separately installable/downloadable. No VPN adapter is installed by this build, including Full installation.

## Security Center installation and activation

`packaging/akeno.spec` uses PyInstaller `module_collection_mode='py'` for the Security Center package and page. Those modules are excluded from the Core PYZ and collected as eight source files under `_internal/akeno/...`. `tools/build_components.py` creates `akeno-components.json` after collection and before the release ZIP is signed. It records schema 1, suite version, exact file set, sizes and SHA-256. Do not enable `.pyc`/PYZ fallback for these modules or include extra security code in Core.

Inno Setup offers Full, Core without Security Center, and Custom; the Core label explicitly includes other still-bundled suite features. Deselecting Security Center excludes/removes its external files. The installer writes `[components] security_center=0|1` in `akeno-components.ini`. Existing installations without the marker retain their old enabled behavior. The installer still uses the existing AppId and per-user permissions. Inno compilation and Windows install/uninstall paths remain to be tested.

`ComponentService` checks inventory version, fixed file ownership, non-link paths, size and SHA-256. It distinguishes active, disabled, not_installed, incompatible and error. Activation lives in the separate per-user `components.sqlite3`; factory-reset settings do not erase it. No additional license grant is implied. Settings → Components permits activation/deactivation of the installed Security Center; changes rebuild that page. Adding/removing files still requires rerunning the installer. **Separate module downloads and transactional in-app removal are pending**, not represented as completed buttons.

Core no longer imports the Security Center page before checking availability. An expected disabled/missing component uses `ComponentUnavailablePage`, not `UnavailablePage`, because the latter records a failed updater health check. The optional antivirus hook checks component availability before loading its service; release authentication/hash checks remain required. Existing Windows antivirus stays active independently of this UI component.

## Component-aware signed updates

No new network/download trust source, release schema or signing key is introduced. The entire portable ZIP remains signed with the existing Ed25519 release key and SHA-256; protocol/schema 2 content aggregate and limits are validated by existing `UpdateService.stage`. `apply._run` verifies the original staged content, then projects the copy onto the installed selection. It never mutates the original verified inventory. The copied subset is verified again. Component inventory paths/hashes/version must match the complete verified release; only the fixed eight optional paths may be omitted.

The selection INI is carried through switching and rollback using the updater's existing kept-file mechanism. A Core-only installation refuses a monolithic update/rollback without the new inventory before switching folders. This intentionally limits rollback to old packages that understand component selection; present a repair-installer path rather than silently reactivating old code. Existing full-install updates and journaled rollback retain their prior path. Component activation DB is outside the program folder and unaffected by the updater.

## LAN internals and remaining integration

`RoomStore(path, clock=...)` is isolated from the ASGI app. It provides create/join/list/heartbeat/rotate-invite/remove/leave/delete. Invitations have 192 random bits, only SHA-256 is stored, and expire after ten minutes; rooms expire after one day. Passwords use Argon2. Transactions serialize joins, enforce sixteen participants and prevent duplicate IP/key assignment. An owner may create at most three live rooms. Removed accounts remain banned for that room. Peer registrations expire after 45 seconds and may renew; expired means registration expiry, not a measured network disconnection. IPv4 allocations use `10.203.<slot>.0/24`, which must be checked for collisions before any future route installation.

**Authentication is a pending boundary:** `actor` is currently a trusted internal argument. Before exposing any endpoint, derive it from existing `api.deps.current`, require a verified account/active suite entitlement, bind the registered device and public key, apply account/IP rate limits, bounded schemas, short-lived authenticated peer credentials and membership/revocation checks on every operation. Never accept `actor` from request JSON. No new backend route is registered now, so existing account/license services are unaffected.

No UDP/STUN/P2P, WireGuard handshake, AKENO relay, discovery forwarding or tunnel packets exist yet. Every returned transport says `not_implemented`. Distinct allocated IPs in tests are not evidence of actual IP connectivity. WireGuard is Layer 3; plan explicit-IP gaming first. Broadcast/multicast game discovery requires a separate bounded, per-room/port allowlisted forwarder and real game checks. Never expose SMB/RDP/local services to peers by default. Do not add public-VPN exits to the licensing VPS.

## VPN profiles and provider architecture

`parse_wireguard` supports exactly one Interface and Peer, bounded input, explicit directives, validated keys/IPs/routes/endpoints/MTU/keepalive, and rejects includes, hooks, duplicate fields, unknown directives and continuation lines. It strips comments and excludes the profile text from repr/errors. Routing classifications describe the imported configuration only; there are no leak-prevention or tunnel guarantees. OpenVPN is currently rejected and needs a separately reviewed inline-certificate/directive policy before support.

`ProfileStore` stores all profile/name data as a DPAPI-protected SQLite payload on Windows, with an application-format tag. Linux fails closed; tests inject AES-GCM transforms solely to verify the storage contract. It supports add/rename/remove and metadata with `country=None`, `connectable=False`, `state='imported'`. It has no networking side effects and never uploads keys. Removing a SQLite record does not promise secure physical erasure of backups or disk sectors. Real Windows DPAPI round-trips, wrong-user/tamper handling and UI integration still need execution.

No VPN Gate or commercial provider adapter/API is implemented or activated. No countries/servers are fabricated. Before a VPN Gate implementation, verify official CSV/profile formats and integration terms; default to disabled until reviewed and explicit user consent. Owned exit-server inventory must distinguish registered/healthy/connectable and publish only real verified locations. No accounts/servers may be purchased or provisioned by this work.

## Windows services and data-plane work

No Windows networking service, driver or management endpoint has been installed. Use official signed adapters and supported WireGuard embedding; UI should remain unelevated. A future privileged service needs authenticated per-user local IPC, a fixed operation vocabulary, key protection, route/DNS transaction journal, safe rollback after crashes/network changes and coexistence with LAN Play. OpenVPN scripts/plugins and arbitrary UI shell commands must never reach it. Full/split routing, DNS/IPv6 leak checks, exit-IP observation, standby/network-change recovery, optional explicit kill switch and two-machine/NAT/relay/game tests all remain open.

## Environment, tests and continuation

No new secret or runtime-variable binding is needed for the implemented local workflow. Existing Git HTTPS proxy authentication reads and pushes the feature branch. Cloud Python 3.12 / pinned client and server requirements are in `.venv`. The saved setup script is also available at `/workspace/akeno-cloud-setup/install.sh`; it does not deploy or change source. Use this isolated checkout, not a new worktree.

```bash
cd /workspace/AKENO-DLSS-5-Manager
export QT_QPA_PLATFORM=offscreen XDG_CACHE_HOME=/workspace/.cache
# The cloud Python build needs its stdlib path when tests copy the interpreter.
PYTHONHOME="$(.venv/bin/python -c 'import sys; print(sys.base_prefix)')" .venv/bin/python -m pytest -q tests/test_components.py tests/test_network_profiles.py tests/test_updater_v2.py
.venv/bin/python /workspace/akeno-cloud-setup/run-regression.py
(cd server && ../.venv/bin/python -m pytest -q)
AKENO_HOME=/tmp/akeno-handoff-smoke .venv/bin/python -m akeno --smoke-test
```

The regression helper runs every client test file in a fresh process (three workers), preserving exit codes and per-file XML under `/tmp/akeno-regression`; it does not skip failed assertions. See the progress file for final results and pending Windows/real-network checks. The final Linux PyInstaller rebuild and both full/Core-only packaged smoke tests passed. Network internals are source-only APIs and are not yet exposed by the GUI or production backend; repeat integration/packaging checks when they become reachable. Existing GitHub Actions run on feature pushes; no successful Windows CI run has been verified in this session. Do not merge or tag the WIP branch as a release.

Next concrete work: inspect regression failures, execute the new Windows Full/Core install/update/rollback matrix on a disposable runner, then complete module download/removal transactions before exposing authenticated LAN endpoints or enabling any tunnel/provider UI. The main installer is unsigned; Norton logs/recognition names and Authenticode signing require separate investigation.
