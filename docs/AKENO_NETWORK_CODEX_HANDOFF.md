# AKENO Network / modular installer — Claude handoff

## Current status

Continue the existing `feature/akeno-network-modular` checkout. Original continuation point: `609e883`; original application base: `2069ff7`. Resolve the current exact commit with `git rev-parse HEAD` and compare `git ls-remote origin refs/heads/feature/akeno-network-modular`. Do not create a worktree, reset to `main` (README only), merge, tag, publish a release or deploy to the production VPS. Progress/checkpoint evidence: `AKENO_NETWORK_PROGRESS.md`.

This branch is **WIP, not the completed master request**. It now has signed module operations, authenticated LAN APIs, Windows WireGuard/UAC code, Network UI, official-format VPN Gate catalog preparation, STUN discovery and a bounded own-relay primitive. Real Linux WireGuard traffic was verified. Actual Windows installer/driver results and public NAT/end-to-end product integration remain required. No production service or release key was changed.

## Actual module boundaries

| Area | Implementation / packaging |
|---|---|
| Core / launcher / library | Existing `app.py`, `services.py`, `core`, `games`; retained |
| Accounts / license | Existing `licensing` and FastAPI account/device/license services; retained |
| Update trust / rollback | Existing Ed25519 release schema 2, protocol 2, SHA-256, content aggregate, staging, journal, health/rollback; extended for module selection |
| Security Center | Actually separable eight-file group: `system/securitycenter/*.py`, `ui/pages/security.py` |
| Other existing suite features | DLSS/FiveM/install engine, FPS/benchmark, Doctor/Cleaner/Network Lab/mod scanner, Desktop Studio/dock/widgets, community/sync remain bundled/shared. Do not claim independent package downloads for them. |
| AKENO Network | `network` client/native/protocol modules plus `ui/pages/akeno_network.py`; currently bundled with Core, no adapter or tunnel installed on startup |
| LAN backend | `server/akeno_server/network`; API opt-in, relay not publicly exposed |

## Installer / signed module transactions

PyInstaller collects Security Center as external source (`module_collection_mode='py'`), never Core PYZ fallback. `tools/build_components.py` emits `akeno-components.json` after collection, before the complete portable ZIP is signed: schema 1, exact Core version, fixed eight-file ownership, size/SHA-256. Inno Setup Full/Core/custom selects actual files and writes `[components] security_center=0|1` to `akeno-components.ini`; deselection removes older optional files. Core still includes other bundled suite features; its label says so. AppId/per-user install and license/update security remain unchanged.

`ComponentService` distinguishes active/disabled/not_installed/error/incompatible and verifies real files, hashes, version and non-link paths. Activation uses per-user `components.sqlite3`, independent of factory-reset settings. Expected absent/disabled pages use a supported placeholder rather than a failed updater health check. Optional antivirus integration is skipped when unavailable; Windows antivirus and cryptographic download verification are not disabled.

Settings → Components now has Install/Repair/Remove plus Enable/Disable. File changes download/reuse the **whole signed package for exactly the installed Core version**; this is not a standalone unsigned module feed. `UpdateService.check(include_current=True)` retains signatures, expiry, sequence/replay and downgrade protections. `prepare_component` accepts only install/repair/remove and refuses a newer/older or missing current-version package. A feature build cannot use an old monolithic production package lacking the inventory; update/release coordination is still necessary before players can use these controls.

The updater verifies the full original staged payload, validates the module inventory against its content list, projects the chosen optional subset and verifies the copy. It writes the new local selection marker only afterwards, leaving the old selection in the old installation for rollback. Failed health, interrupted switching and explicit rollback reuse the existing journal. Archives carrying any case variant of the local selection INI are refused. Ordinary Core-only updates/rollback cannot silently install old monolithic Security Center code.

The previous installed copy remains rollback-capable. Module enable/disable preference is kept independently; reinstalling a previously disabled module does not silently enable it. Licenses, accounts and user data remain outside the replaced program folder. Separate module-sized downloads, independent versioning of other suite modules and broad packaging splits are still open.

## Windows verification infrastructure

`.github/workflows/components-windows.yml` builds the real Windows application, compiles the actual Inno installer and runs `tools/component_windows_e2e.py` on a disposable Windows runner. It covers real Full/Core/reselection files and executable smoke, signed same-version module install/remove/repair, damaged-module detection, actual failed-health rollback and uninstall with retained user data. It also includes real DPAPI/protected-DACL tests. Local package delivery in that test checks signed bytes/hashes; only a throwaway in-memory test signing key is used, never the production release key.

**No successful run of this Windows matrix has been observed in this task.** The executor is Linux with no Windows VM/KVM. Git read/push works; Actions API requests to `api.github.com` fail at proxy CONNECT with 403. Network additions were saved to the environment draft, but current enforced custom allowlist remains empty. Do not call a CLI authentication warning proof of missing credentials while the destination itself is blocked. Enable the saved domain through environment settings, retry the actual API operation, and inspect the newest exact-commit run/artifacts before making a Windows claim. No public release is configured in the new workflow.

## Authenticated LAN API and client

Default `AKENO_NETWORK_ENABLED=false` leaves current production routes unchanged. Explicit local/test enablement mounts `/api/v1/network/lan`; `AKENO_NETWORK_DATABASE_PATH` selects an isolated SQLite store. New endpoints use existing bearer-session authentication, verified email, active suite entitlement, an active registered device and exact session/device binding. Request schemas forbid extra actor fields. Rate limits cover account/IP and joins. Never derive actor authority from caller JSON.

Routes: POST `/rooms`, POST `/join`, GET `/rooms/{id}/members?device_id=...`, POST `/rooms/{id}/heartbeat`, POST `/rooms/{id}/invite`, DELETE `/rooms/{id}/members/{member}?device_id=...`, POST `/rooms/{id}/leave`, DELETE `/rooms/{id}?device_id=...`. Bodies carry device ID and bounded room/name/password/public-key/invite fields as appropriate.

`RoomStore` serializes joins, limits rooms/participants, hashes 192-bit invitations with ten-minute expiry, hashes passwords with Argon2, allocates distinct `10.203.<slot>.<host>` addresses, binds memberships to account/device/public key, prohibits device takeover and retains exclusions per room. Peer registrations expire after 45 seconds. Peer lists exclude revoked users/devices or inactive licenses. Addresses are allocations, not proof of connectivity; inspect local routing collisions before any real LAN adapter use.

Heartbeat issues an Ed25519 credential signed by the existing license-server key, with distinct `typ=akeno-lan-peer`, `iss=akeno-lan`, audience, room, device, public key, virtual IP, nonce, issue/expiry (45 seconds). This token cannot substitute for a license. `network/credentials.py` verifies signature/type/audience/room/time/IP/key structure. `LanClient` uses the existing license manager's refresh/session/device and trusted/endorsed public keys; only the public WireGuard key leaves the PC. Private X25519 identity storage requires Windows DPAPI; tests use explicit in-memory identity stand-ins, not plaintext fallback.

Network UI has room create/join/refresh/leave, invitation rotation/copy, member removal and room deletion. It explicitly does not connect an actual LAN tunnel. There is no implicit API request on page construction and no production deployment.

## WireGuard transport, NAT discovery and relay

`tools/network_e2e/` uses two actual isolated Linux clients, official Debian `wireguard-go`/tools and TUN devices on an internal Docker network. Kernel WireGuard was unavailable. Seven real checks passed: 65,536-byte TCP echo over virtual IPs, actual handshake/counters, peer removal blocks traffic, peer restoration reconnects, direct UDP blocked, then encrypted echo through an own UDP relay. No host routes, public ports or Internet exit are changed. Private keys stay in containers. This is real packet evidence, not Windows/ISP NAT/game evidence.

`PairedUDPRelay` forwards opaque WireGuard packets only between two trusted/proven endpoint tuples, with a mandatory live authoriser, at most 45-second allocation, source binding, format/size checks, rate bounds and no amplification. Constructor defaults bind loopback and nothing listens on import. The container fixture supplies known endpoints; **production endpoint-proof/allocation API, lease renewal, capacity operations and remote relay service integration are still open**. Never expose arbitrary destination tuples from request JSON or run a generic public UDP forwarder.

`network/stun.py` provides bounded RFC 5389 discovery for explicitly configured coordinators: random transaction ID, connected UDP source, XOR address/size/padding/fingerprint checks. Actual local UDP discovery was tested. No third-party STUN destination is hardcoded. Public NAT mappings, port handoff to WireGuard, endpoint ownership challenges, symmetric NAT, network changes, a secure automatic P2P/relay selector and reconnect are not integrated. LAN configuration generation (multiple peers), peer lease enforcement in the native service, route collision checks and strict game-port firewall policy remain required. WireGuard is Layer 3; broadcast/multicast discovery needs a separate bounded/allowlisted design and real game checks. Do not expose SMB/RDP automatically.

## Own VPN / Windows privilege boundary

`network/profiles.py` supports a strict single-peer WireGuard subset: bounded input, keys/IPs/routes/DNS/endpoint/MTU validation, no hooks/includes/scripts/unknown directives/continuations. Profile text is omitted from repr/errors. OpenVPN remains unsupported. `ProfileStore` encrypts name/profile payloads using user Windows DPAPI in SQLite; Linux has no plaintext fallback. Removal does not promise physical erasure from old backups/disk sectors.

`WindowsWireGuard` requires an already-installed official signed WireGuard manager. Executables are selected from Windows' known Program Files directory (not user environment), checked with Authenticode and restricted to the WireGuard LLC signer. Fixed, validated AKENO tunnel names and supported CLI verbs only; no shell. The helper creates transient config data with SYSTEM/current-user-only protected DACLs **before** writing, atomically submits it to the manager and waits for its `.conf.dpapi` conversion; all plaintext is removed on failure. The official tunnel service owns its adapter/routes/DNS; no host firewall, DNS, VPN registry or driver-signature bypass is implemented.

`network/helper.py` runs one explicit UAC operation via the built executable before Qt startup. It accepts only an expiring DPAPI request ID with connect/disconnect/status/exit_ip, never arbitrary paths/commands. Profiles and journal live under `%LOCALAPPDATA%/AKENO/network`. The GUI does not remain elevated. Alternate-admin-account UAC cannot decrypt the original user's DPAPI profile and must fail closed; a proper privileged service/IPC design is still needed if that scenario must work.

`TunnelManager` journals connecting/awaiting/disconnecting/recovery-required state, prevents overlapping profiles, cleans up start failures and can recover after process interruption. Connected requires a recent native handshake, not merely a service/PID. Full tunnels conservatively require IPv4+IPv6 default routes, both address families and explicit DNS. UI connection approval explains route/DNS changes and persistence until disconnect. Manual exit-IP observation uses normal verified HTTPS to `api.ipify.org` after a fresh handshake; this is not proof of all-process routing/DNS/IPv6 leak protection. Real Windows manager import, UAC, service lifecycle, routes/DNS before/after, exit IP, standby/network changes and leaks have **not** been verified. Never advertise these as completed just because backend stand-ins pass.

## VPN Gate / owned exits / future providers

`network/vpngate.py` reads the official HTTPS `/api/iphone/` CSV format with bounded schema/rows/profile sizes, valid global addresses/countries/numbers, unsafe profile-directive rejection, no redirects and no executable downloads. Countries come only from actual rows. UI offers catalog retrieval; every server remains non-connectable and `connect()` unconditionally refuses. No US/Turkey/global availability or no-logs claim is made.

Live catalog and official terms could not be read through the current proxy. `api.github.com`, `www.vpngate.net`, `vpngate.net` were saved additively as custom-domain requirements; they are not yet observed active. Technical OpenVPN validation, terms/integration rights, operator privacy/logging and explicit experimental user consent must be cleared before enabling any connection. No commercial provider API, paid account, invented endpoint or owned international exit server is provisioned. Those remain separate integration work.

## Validation and safe continuation

Current evidence is in the progress file. Full client run: all 81 files exit 0, 1,266 passed and 19 skipped. Additional STUN/new marker-refusal and last UI/control changes were validated separately (not a claim of another complete run). Full server regression: 160 passed, 3 third-party deprecation warnings. Real own-relay WireGuard harness: seven checks passed; local relay policy: two tests passed. Windows DPAPI/DACL tests are skipped on Linux. Source/package smoke must show 29 pages and 19/19 assets. Inspect each current-run XML, not stale results.

```bash
cd /workspace/AKENO-DLSS-5-Manager
export QT_QPA_PLATFORM=offscreen XDG_CACHE_HOME=/workspace/.cache
PYTHONHOME="$(.venv/bin/python -c 'import sys;print(sys.base_prefix)')" .venv/bin/python -m pytest -q tests/test_component_transactions.py tests/test_updater_v2.py tests/test_network_profiles.py tests/test_network_tunnels.py tests/test_network_stun.py tests/test_vpngate.py
.venv/bin/python /workspace/akeno-cloud-setup/run-regression.py
(cd server && ../.venv/bin/python -m pytest -q)
AKENO_HOME=/tmp/akeno-network-smoke .venv/bin/python -m akeno --smoke-test
```

For isolated actual WireGuard tests, read the cloud Docker/proxy/CA instructions first. Use the managed local socket, unchanged Docker credentials/proxy, a writable `BUILDX_CONFIG`, the combined CA build secret and TLS/package verification. Build `tools/network_e2e/Dockerfile` as `akeno-network-e2e`, then run `.venv/bin/python tools/network_e2e/run.py`. It cleans up only its own named containers/network. Do not install a VPN client or change routes in the cloud executor.

Next priorities: obtain and inspect the exact-commit Windows workflow evidence, fix genuine Windows failures, test supported official WireGuard/UAC/DNS restoration on disposable PCs, then finish endpoint proof/NAT/lease enforcement and an actual authenticated two-Windows-client room tunnel. Complete private modular package acceptance before any release. Add public VPN providers only after review/consent; keep production deployment and releases outside this task's authorization.


Final privilege-boundary tightening: native removal derives the local profile public key from its private key and compares it to the actual tunnel public key before uninstalling/deleting. A forged local tunnel name cannot remove another profile without that key. Orphan encrypted profiles require removal through the official manager before recovery; do not silently delete an unproven profile. The connector refuses WireGuard DangerousScriptExecution registry overrides without modifying them. 23 profile/tunnel/STUN checks passed, including a stand-in check that foreign-key removal runs no uninstall command. Actual Windows validation remains required.
