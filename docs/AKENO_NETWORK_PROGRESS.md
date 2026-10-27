# AKENO Network and modular installation — progress

## Current phase
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
