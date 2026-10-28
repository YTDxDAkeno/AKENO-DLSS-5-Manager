# AKENO Network release candidate — live progress (read this first after a hand-over)

Branch: `claude/adoring-cerf-pzowiw` (started from `claude/inspiring-gauss-i8vjdr` @ `bf4c618`).
Every ~5 minutes of work is committed and pushed (WIP commits are marked `WIP:`). Resolve the newest commit with
`git ls-remote origin refs/heads/claude/adoring-cerf-pzowiw`.

Final report: `docs/AKENO_NETWORK_RELEASE_READINESS.md` (written at the end; may not exist yet).

## Starting point (verified 2026-10-28)

- `claude/inspiring-gauss-i8vjdr` = `bf4c618` (docs only after `588b21c`, Windows build run 280 green).
- `codex/akeno-network-staging-20261028` = `325d35c`: differs from `588b21c` only in `deploy/network-staging/`
  (templates, public staging keys, loopback smoke report). Imported as commit `40f243d`; no app code taken.
- `main` holds only the initial README. No merge/tag/release/deployment was made.

## Done (commit)

1. Imported Codex staging templates and smoke report (`40f243d`).
2. "Not Found" fixed + regression tests (`fab1e1a`).
3. Server: heartbeat room name/owner, member display names (`a8615f1`); status route (`fab1e1a`).
4. VPN provider architecture + target-country catalog without servers (`4e57ca5`).
5. Network widgets + redesigned page + DE translations + tests (`7a20254` … `f7528a6`).
6. LAN Play / VPN modules, off by default, Settings → Components (`c851f14`).
7. Provider overview doc (`0092e9f`), version 3.2.0 + release notes (`e054c71`).
8. Discovery forwarding core (not released) + compatibility doc (`f2f5d84`); screenshots (`1e8780d`).
9. `docs/AKENO_NETWORK_RELEASE_READINESS.md` written.

## Tests

- Server (local): 175 passed.
- Client full suite (local): 1326 passed, 19 skipped, 0 failed.

## CI

**`build.yml` Lauf 310 (https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40630526117), Commit
`edc02c6`, vollständig grün:** Linux-Tests, Lizenzserver (SQLite, PostgreSQL 16, Docker-Deployment), Upstream-
Paketverträge, Windows-Unit/Integration/UI-Tests, Smoke-Test, PyInstaller, gepackte App startet, Desktop Studio,
Portable-ZIP, **Inno-Installer 3.2.0**, Release-Manager-Werte, Prüfsummen; **Update-E2E 58/0**, **Upgrade vom
veröffentlichten 3.0.0-Installer 57/0**, **vom 3.1.0-Installer 57/0**, **Repair 15/0**. Zusätzlich auf `f2f5d84`
(gleicher Netzwerk-Code): `network-windows.yml` Lauf 23 (WireGuard/LAN-Session auf Windows) und `lan-lab.yml`
Lauf 13 (NAT-Lab) grün. Lokale Regression: Client 1326 passed / 19 skipped / 0 failed, Server 175 passed.

## Next

- The 5-minute auto-checkpoint script stopped at its 2 h limit; commits since are manual after each step.
- Possible follow-ups: Windows capture for discovery, AKENO exit servers (owner approval), two-PC test.

## "Not Found" — root cause

The production license server runs with `network_enabled=False` (`server/akeno_server/config.py`), so
`/api/v1/network/lan/*` does not exist there. Any LAN action in the shipped client (create/join/refresh) gets
Starlette's 404 → `{"error":{"code":"not_found","message":"Not Found"}}` → `ApiError("Not Found")`.
`Controller.run` then emitted `str(exc)` to the main window's status bar (bottom left, 8 s) and the page wrote the
same raw text into its own status line. Fix: see commits after `40f243d`.
