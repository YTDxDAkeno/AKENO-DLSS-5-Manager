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
- Client network/UI/providers/modules/discovery: all passed locally. Full client suite: see "CI" below / next entry.

## CI

- `build.yml` runs on every push; earlier runs were cancelled by newer pushes. Waiting for a full run on the final
  commit.

## Next

- Record the full client regression and the final `build.yml` result here and in the readiness report.
- Possible follow-ups: Windows capture for discovery, AKENO exit servers (owner approval), two-PC test.

## "Not Found" — root cause

The production license server runs with `network_enabled=False` (`server/akeno_server/config.py`), so
`/api/v1/network/lan/*` does not exist there. Any LAN action in the shipped client (create/join/refresh) gets
Starlette's 404 → `{"error":{"code":"not_found","message":"Not Found"}}` → `ApiError("Not Found")`.
`Controller.run` then emitted `str(exc)` to the main window's status bar (bottom left, 8 s) and the page wrote the
same raw text into its own status line. Fix: see commits after `40f243d`.
