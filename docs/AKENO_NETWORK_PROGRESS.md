# AKENO Network and modular installation — progress

## Current phase
A: repository inspection and cloud development setup. Implementation is not complete.

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
Dependencies are being installed from the pinned requirements. No passing suite or Windows/tunnel evidence claimed yet.

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
