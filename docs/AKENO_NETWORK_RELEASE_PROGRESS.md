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

## Done

1. Imported Codex staging templates and smoke report (`40f243d`).

## In progress

2. "Not Found" bottom-left: root cause found (see below), fix + regression tests.

## Next

3. Server capability endpoint `GET /api/v1/network/lan/status` + client feature flags.
4. Network page premium redesign (dashboard, tooltips/details instead of long texts).
5. VPN provider architecture (only verified locations are connectable).
6. Diagnostics (more checks, redacted export).
7. Component manager / installer review (Secure Center optional), version decision, release-readiness report.

## "Not Found" — root cause

The production license server runs with `network_enabled=False` (`server/akeno_server/config.py`), so
`/api/v1/network/lan/*` does not exist there. Any LAN action in the shipped client (create/join/refresh) gets
Starlette's 404 → `{"error":{"code":"not_found","message":"Not Found"}}` → `ApiError("Not Found")`.
`Controller.run` then emitted `str(exc)` to the main window's status bar (bottom left, 8 s) and the page wrote the
same raw text into its own status line. Fix: see commits after `40f243d`.
