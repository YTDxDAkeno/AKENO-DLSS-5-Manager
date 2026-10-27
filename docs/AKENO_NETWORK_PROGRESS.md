# AKENO Network and modular installation — progress

## Claude continuation (newest first; supersedes the Codex status below where they differ)
- Branch: `claude/inspiring-gauss-i8vjdr`, started from Codex checkpoint `833fec0` (`feature/akeno-network-modular`, confirmed equal to the remote). Every push is confirmed with `git ls-remote`; resolve the newest commit with `git log -1`.
- GitHub Actions is reachable through the GitHub MCP tools in this session (the raw `api.github.com`/artifact blob hosts stay blocked by the proxy, job logs are readable).
- Finding: every Codex implementation push had a red Windows CI. `components-windows.yml` runs 1–8 and `build.yml` runs 221–237 failed; the baseline `5f62d6c` was green. The only Windows failures (full Windows suite: 5 failed, 1271 passed, 11 skipped) were `tests/test_component_transactions.py`: on the runner the running program is the PSF-signed `python.exe`, so the unchanged Authenticode rule demanded a signed fixture `AKENO.exe`. Fixed in the tests (same stub as the Updates-page tests) and in `tools/component_windows_e2e.py` (the rule now compares against the real installed `AKENO.exe`, as in production). The rule itself is unchanged.
- **Real Windows evidence:** `components-windows.yml` run 10 (https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40603762541, commit `b05882a`, windows-2022) passed: component/updater unit tests, real PyInstaller build, real Inno Setup compile (`AKENO-Gaming-Suite-3.1.1-Setup-x64.exe`, 73.7 s), and **47 real Windows checks**: Core-only → Full → Core-only installs via the compiled installer with physical file/marker checks and installed-executable smoke tests, signed same-version module install/remove/install, damaged-module detection and repair, an authentic-but-broken package rejected by the real health check with rollback to the Full install, and uninstall that removes the program and keeps user data.
- Not covered by that run: interactive (non-silent) wizard, upgrade from a published older installer with deselection (covered by `build.yml` update E2E, still pending on this branch), Norton or any third-party antivirus (no runner has it).

## Current continuation status (supersedes the historical entries below)
- Branch: `feature/akeno-network-modular`; latest implementation checkpoint before this final documentation update: `527272e`. Resolve the final commit with `git rev-parse HEAD`; remote pushes have been explicitly confirmed.
- Implemented source: same-version signed Secure Center install/remove/repair with rollback; Full/Core Windows installer matrix; opt-in authenticated/device-bound LAN API and common client UI; explicit Windows WireGuard/UAC backend with DPAPI/journal; bounded STUN and own-relay primitives; VPN Gate catalog parser with all connections disabled.
- Full client run: 81 files, 1,266 passed, 19 skipped, zero failed/errors. Combining the subsequent new marker-refusal/STUN/last UI targeted results gives 1,270 distinct passing client cases and 19 skipped (not a second full-suite claim).
- Full backend regression after fixes: 160 passed, 3 dependency deprecation warnings. Final focused updater/module/profile/provider run: 138 passed, 1 skipped. Last room/client/API checks: 12 passed; last UI/component/translation checks: 16 passed.
- Seven actual Linux userspace WireGuard checks passed, including encrypted traffic over virtual IPs, peer revocation/reconnect and own-relay fallback after blocking the direct path. Local relay policy and STUN tests are separate from Windows/ISP-NAT evidence.
- Final Linux PyInstaller rebuild passed. Both complete and physically Core-only executable smoke tests passed with all 29 page keys, Network UI and 19/19 assets. Security Center remains absent from Core PYZ; full generated bundle was restored after the test. `pip check` passed.
- Required external evidence remains unavailable: real Windows installer/UAC/manager/driver/routing/DNS/IPv6/exit-IP tests, two Windows PCs behind real separate NATs, public endpoint proof/relay allocation/lease integration and real LAN games. Actions API and official VPN Gate terms/catalog are blocked by current proxy policy; required domains are saved in the draft but not observed active. No production deployments/releases/merge occurred.
- Next concrete step: apply reviewed network domain settings and inspect the exact-commit Windows workflow/artifacts, fix actual Windows failures, then finish authenticated LAN transport/endpoint-proof and real NAT tests before public feature activation. No new secret is assumed missing while the API destination itself is denied.
- Complete current Claude handoff: `docs/AKENO_NETWORK_CODEX_HANDOFF.md`. Security review: `docs/AKENO_NETWORK_SECURITY_REVIEW.md`. Full master request remains incomplete.

## Historical checkpoint records

## Initial phase
B/C plus isolated D/E internals: optional Security Center package, component-aware updates, experimental room store and WireGuard profile import/storage. The complete master request is not finished.

- Branch: `feature/akeno-network-modular`.
- Base commit: `2069ff7c58d2c52473023ce759bdbfe863319713` (`claude/adoring-shannon-2d4qb4`). This integrates the 3.1.1 hotfix. `main` is only the initial README.
- Current commit: use `git rev-parse HEAD` (a document cannot contain its own commit hash).
- Last successful checkpoint: consult `git log` and the remote branch; do not infer a push from a local commit.

## Completed inspection
- Client: Python 3.12, PySide6 6.8.1, application services and SQLite, existing Qt design system.
- Distribution: PyInstaller one-folder build; Inno Setup per-user installer, same AppId since 2.x.
- Authentication/licensing: separate FastAPI/SQLAlchemy server, Ed25519 offline licenses.
- Updates: signed release schema 2 / protocol 2, SHA-256, strict ZIP extraction, staging, health check, journaled rollback.
- Secure Center: Python code in `akeno/system/securitycenter`, UI in `akeno/ui/pages/security.py`; Windows Security Center and Defender integration, no bundled VPN adapter or custom antivirus driver.
- Important coupling: download antivirus hook invokes Secure Center; main window imports and constructs every page. These must tolerate an absent optional component.
- Other modules share application services, Qt and dependencies; do not label UI switches as independent downloads.
- Desktop Studio starts separate host/rendering processes and needs Qt Multimedia and WebEngine.
- CI covers Linux Qt, SQLite/PostgreSQL server, Windows packaging and updater E2E. Pushes to a feature branch do not publish releases (publication requires a tag or explicit dispatch).

## Validation
Pinned client and backend dependencies installed in `.venv`. SQLite backend baseline: 146 passed. First 12 component tests passed (activation, settings-reset survival, missing/corrupt files, exact inventory, Core-only projection, absent-module Qt startup). Linux one-folder package built successfully. Its PYZ contains no Security Center modules; all 8 optional source files are external. Both full and physically Core-only packages passed `--smoke-test` (28 pages, 19/19 design assets). Core-only signed update and refusal of a monolithic update passed. A copied-interpreter test initially failed because this runtime needs an explicit PYTHONHOME after relocation; it passed with that setup correction. The initial single-process client run was interrupted after 900+ outcomes because repeated Qt UI rebuilds became very slow; it is not a passing/full result. A corrected run executes every test file in a fresh process with the required PYTHONHOME, without skipping assertions. Final backend suite including room tests: 153 passed, three dependency deprecation warnings. Windows installer/tunnel checks are not executed.

## First implementation checkpoint
- Security Center is collected as separate source files rather than hidden inside the Core PYZ archive. Inno Setup offers Full/Core/custom selection. Build-generated inventory pins its exact file hashes and version.
- Component activation lives in `components.sqlite3`, outside resettable settings. Settings exposes activation and honest installation states. Missing/disabled components do not fail updater health checks.
- Core-only updates verify the full signed ZIP and original staged content before filtering the copy; selected files are verified again. Monolithic updates without a component inventory fail closed for Core-only installs.
- Other modules are still bundled; separate module downloads, LAN Play and VPN are not claimed as implemented.

## Isolated network internals
- WireGuard import: one peer, strict directive allowlist, no scripts/includes/continuations, key/address/route/endpoint/MTU validation. Profile strings are excluded from repr and parser errors.
- ProfileStore: encrypted payloads in SQLite; Windows DPAPI required in production, no plaintext Linux fallback. Rename/delete and nonsecret metadata provided. 16 tests passed using an authenticated-encryption stand-in (not DPAPI evidence). No connect/disconnect, OpenVPN or UI integration yet.
- `server/akeno_server/network/rooms.py`: isolated transactional SQLite prototype with expiring hashed invitations, Argon2 room passwords, owner-only controls, revoked-member exclusion, peer leases, persistent state, 16-peer capacity and distinct allocated virtual IPs. 7 tests passed, including concurrent joins. It is NOT registered in the production API. Actor identity is a trusted internal input pending existing-session/license/device enforcement in API integration.
- No transport, Windows adapter, firewall, NAT traversal, P2P, relay, discovery or actual packet exchange was implemented or claimed.

## Next steps
1. Finish cloud installation, run baseline client and server suites and application smoke test.
2. Implement persistent module selection and isolate Secure Center from Core startup/downloads.
3. Test component selection through installation and updates without weakening signature checks.
4. Implement isolated LAN/VPN internals only with honest capability/status reporting.
5. Document Windows driver, routing, NAT and hardware tests as outstanding until executed.

## Checkpoint policy
Review and scan changed files for secrets before each commit; push after each small implementation step and at most five minutes of development with unsaved changes. No empty commits, production deployment, merge, signing-key change or release. WIP commits identify incomplete validation. Interval checks are performed between work steps, not by a background scheduler.

## Known limitations
Linux cannot establish Windows driver/service/installer readiness or real LAN game compatibility. No Norton detection name, quarantine log or affected executable was supplied; the exact cause remains unconfirmed. A modular installer cannot guarantee avoidance of a detection against the Core installer itself.

## Reusable cloud configuration
The tested install script and startup instructions have been saved in the environment draft (`install_script`, `start_skill`); publication remains a user action. Setup refresh was executed twice: pinned dependencies installed, `pip check` passed, Qt 6.8.1 and native library loads verified. No network-policy or secret changes were required. Final source snapshot/checkout commit will be saved after the final checkpoint.

## Final validation of this checkpoint
- Full client regression: all **77 test files** finished with exit 0 in fresh processes; **1,251 passed, 17 skipped, 0 failed/errors** (1,268 collected outcomes). Reports: `/tmp/akeno-regression/`.
- After adding a guard against disabling Security Center during a running task: affected component, SYSTEM UI and translation suites rerun: **38 passed** (includes one new UI test). Thus 1,252 distinct client tests have passing results and 17 are skipped; the last small change was validated by the affected suites rather than repeating the complete regression.
- Final backend regression: **153 passed**, 3 third-party deprecation warnings, SQLite and local HTTP tests. No PostgreSQL/Docker deployment run was performed.
- Final Linux PyInstaller rebuild succeeded. Verified that no Security Center module is in the Core PYZ and all 8 source files are separately collected and inventoried.
- Both final full-package and physically Core-only `--smoke-test` runs exited successfully: 19/19 design assets, all 28 page keys, Core-only reports `not_installed`. Generated output was restored to the complete bundle afterwards. This is Linux packaging evidence, not a compiled Windows installer.
- Pinned setup refresh repeated successfully; `pip check`, Qt 6.8.1 and required native library loads passed. Installation/startup instructions saved as an environment draft; not published by the agent.
- No new credentials/network-policy changes required, no production request/deployment, no releases, no merge.

## Remaining scope / next concrete step
The full master request is **not completed**. Only Security Center has a real physical package boundary; all other current suite modules remain bundled. Separate module downloads/removal transactions and broader module separation are pending. Inno Setup/Windows matrix, real DPAPI, Windows services/adapters, tunnel transport, P2P/NAT/own relay/discovery, actual gaming traffic, authenticated LAN API/device integration, OpenVPN, VPN Gate/legal/consent checks, own exit inventory, routing/DNS/IPv6/kill switch and full Network UI are unimplemented/unverified. No public country list is presented.

Next: use a disposable Windows runner to compile and validate the Full/Core installer and component-aware update/rollback matrix before treating the component split as release-ready; continue signed module install/remove transactions, then authenticated LAN API integration. Do not deploy or publish networking features from this WIP branch.

## Continuation from 609e883 — signed module transactions / Windows matrix
Remote branch was checked explicitly and matched 609e883 (no incoming changes). This machine is Linux; the GitHub Actions API request returned Forbidden although scoped HTTPS Git read/push works. No Windows result is claimed from that failure.

Implemented same-version install/repair/remove using the existing signed full portable package. Ed25519/expiry/anti-replay and complete ZIP/hash/content checks precede the directory swap. The new selection marker is written after payload verification while the old marker stays in the rollback copy. Component version must exactly match Core. UI actions prepare the signed change and use the existing restart/health/rollback flow. Initial transaction/component/translation tests: 23 passed; broader updater regression running.

Added a dedicated Windows workflow and real-executable E2E script: Inno Full/Core/reselection, physical files, installed smoke tests, signed module addition/removal/repair, actual failed-health rollback and uninstall with retained user data. This is runnable CI infrastructure, not yet a confirmed Windows execution. API access remains unavailable for monitoring runs in this cloud task. No production release or deployment is configured by the new workflow.

## Continuation — authenticated LAN API / updater regression
Added opt-in LAN endpoints (`AKENO_NETWORK_ENABLED=false` by default), using existing bearer sessions, verified email, active suite entitlement and the registered device bound to that session. Membership now binds the WireGuard public key to that device; another device/session cannot take it over. Short-lived Ed25519 peer credentials have a separate token type/audience and are room/device/key/IP bound. Revoked devices/accounts/licenses are filtered out of peer lists. No API deployment was performed, and credentials do not imply a working tunnel.

Broader updater/module regression: 115 passed, 1 skipped after fixing the existing release verification CLI's handling of a leading '-' in a base64url signature. A deterministic regression test covers that case; signature validation itself is unchanged. LAN HTTP authorization/device/revocation tests are being run separately from tunnel tests.

## Actual WireGuard data-plane check (Linux containers)
Kernel WireGuard was unavailable (`Unknown device type`), so the isolated image uses the official Debian `wireguard-go` and `wireguard-tools` packages with package-signature/TLS checks retained. Two separate containers on an internal Docker network each have only their own TUN/NET_ADMIN access; no host routes or public ports are changed. Five real checks passed: 65,536-byte TCP echo over virtual IPs, WireGuard handshake, encrypted transfer counters, peer removal blocks traffic, peer restoration reconnects. This is actual userspace WireGuard traffic, not a simulated socket tunnel. It does not establish Windows-driver, distinct-NAT, public P2P, relay or LAN-game compatibility. Report: `/tmp/akeno-wireguard-e2e.json`; repeatable harness: `tools/network_e2e/`.

LAN API tests: 11 passed (ASGI control-plane tests including authenticated/device-bound access). Network allowlist additions for `api.github.com`, `www.vpngate.net` and `vpngate.net` saved as a draft; not applied to the running proxy. The existing package-manager presets were preserved.

## Continuation — Windows WireGuard connection backend and UAC boundary
Added native Windows adapter using the documented signed `wireguard.exe` tunnel-service and `wg.exe` status commands. It requires an already-installed official manager, rejects unknown tunnel names/unsigned binaries, creates transient import files with SYSTEM/current-user-only DACLs before writing, waits for manager DPAPI encryption and removes plaintext on every failure path. No arbitrary scripts/commands or automatic driver installation. An explicit UAC helper accepts only expiring DPAPI-protected connect/disconnect/status/exit-IP requests. Profiles remain user-DPAPI protected; the helper does not run the GUI elevated.

Tunnel state is journaled for recovery, connected requires a recent real handshake, and an exit address is fetched only explicitly after that handshake (normal verified HTTPS; not a DNS/IPv6 leak guarantee). Full Internet tunnels conservatively require both address families and explicit DNS. Connection/cleanup/restart contracts: 20 profile/tunnel tests passed with native-backend stand-ins. Actual Windows DPAPI/DACL tests added to the Windows job; driver, manager-import, routes/DNS and cross-user UAC tests have not been executed here. GUI integration follows; no auto-connect or startup network changes were enabled.

## Continuation — common client UI and official-format provider catalog
Added AKENO Network alongside the existing Network Lab (which remains unchanged). It exposes authenticated room create/join/member refresh/leave, own WireGuard import/rename/removal and explicit elevated connect/disconnect/recovery/status/exit-IP actions in built Windows applications. No networking starts on page construction. The room UI explicitly states that it does not yet establish a peer tunnel; profile import does not imply connected status. LAN public keys use the existing login/refresh/device path, private keys remain in per-user DPAPI identity storage, and peer credentials are checked against existing trusted/endorsed license public keys.

One live HTTP integration test passed with two real desktop licensing/LAN clients, distinct registered device identities, a real local API server, signed peer credentials and two allocated addresses. This is HTTP control-plane evidence; the separate container harness is the actual WireGuard-packet evidence. Source smoke passed with all 29 page keys and 19/19 design assets; affected UI/component/translation/provider checks: 18 passed.

VPN Gate: strict bounded official-format CSV adapter, public-IP filtering, unsafe directive rejection, actual-row-only country list and a fetch-only UI. All connections remain unconditionally disabled pending terms/operator privacy/OpenVPN review. Catalog fixtures: passed; official live catalog/terms fetch remains unavailable through the unapplied network-policy draft. No guaranteed USA/Turkey/server availability or no-logs claim is made.

## Continuation — real own-relay fallback
Added a bounded paired UDP relay primitive: fixed/proven source tuples, mandatory live membership authoriser, maximum 45-second allocation, packet-size/format checks, bounded rate and no packet amplification. No relay API or public listener starts by default. Production allocation must prove endpoint ownership before supplying tuples; that public allocation/NAT integration remains open.

The isolated WireGuard harness now blocks direct UDP and switches both real WireGuard endpoints to this own relay. Seven actual userspace WireGuard checks passed, including encrypted TCP echo through the relay fallback. Two additional real local UDP-socket tests passed for source binding, arbitrary-packet refusal, revocation and lease bounds. This is still an isolated topology, not real ISP NAT/Windows or game-discovery evidence.

The final server regression found four missing German API messages; translations added. Signed application packages are now forbidden from carrying the local component-selection INI, preventing an archive from overriding this PC's installed choice. Focused tests cover the refusal; complete regression is in progress.

## Continuation — bounded NAT discovery / regression
Added RFC 5389 binding discovery for explicitly configured coordinators, with random transaction IDs, connected-UDP source binding, size/padding/XOR-address validation and optional fingerprint verification. No third-party STUN host is hardcoded or contacted on startup. Actual local UDP mapping plus malformed-response tests passed (22 combined STUN/profile/tunnel contract checks). This does not prove ISP NAT traversal or preserve a mapping across a later WireGuard socket handoff.

Elevated WireGuard executable selection now uses Windows' known Program Files directory, never a user-controlled ProgramFiles environment value. Final client regression: all 81 files exited zero (counts will be aggregated in the handoff); affected additional tests passed. Final server regression: 160 passed, 3 dependency deprecation warnings. Focused updater/module/profile/provider checks: 138 passed, 1 skipped. Real Windows CI execution remains unconfirmed because the Actions API is blocked by the running proxy; the new workflow and actual-executable test matrix are pushed, not represented as passed.

## Continuation — room administration / final handoff
Network UI now includes invitation rotation/copy, member removal and owner room deletion using the authenticated endpoints. LAN identities also fail closed for a non-DPAPI production store. Last targeted UI/translation/component checks: 16 passed; live-client/API/room checks: 12 passed. Updated Claude handoff describes the current integrated architecture and explicitly distinguishes real Linux traffic, ASGI/live HTTP tests, native-backend stand-ins and unrun Windows/ISP-NAT checks. Final Linux bundle rebuild/smoke validation is being completed; no Windows success or completed master request is claimed.


Final privilege-boundary tightening: native removal derives the local profile public key from its private key and compares it to the actual tunnel public key before uninstalling/deleting. A forged local tunnel name cannot remove another profile without that key. Orphan encrypted profiles require removal through the official manager before recovery; do not silently delete an unproven profile. The connector refuses WireGuard DangerousScriptExecution registry overrides without modifying them. 23 profile/tunnel/STUN checks passed, including a stand-in check that foreign-key removal runs no uninstall command. Actual Windows validation remains required.

Final helper entitlement check: connecting through the hidden elevated entry point also requires the existing signed AKENO entitlement, preventing CLI access from bypassing the normal UI gate. Disconnect/status/recovery remain available without an active license so network state can always be cleaned up. No license format, signing key or offline entitlement rule was changed. Native execution is still pending real Windows verification.

Final post-entitlement/ownership build check: Linux PyInstaller succeeded again, and both full and physically Core-only packaged smoke tests passed with the Network page, 29 page keys and 19/19 assets. The optional files were restored to the full generated bundle afterwards. The checkout was clean and the remote branch matched the implementation checkpoint. Startup guidance and the exact checkout snapshot were saved to the environment draft; publication/fresh-task restoration are not claimed.
