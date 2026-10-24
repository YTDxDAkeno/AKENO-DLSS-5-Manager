# Production status as seen by the Windows app (read-only check, 2026-10-24)

Source: `.github/workflows/production-check.yml` on GitHub Actions. It calls `tools/account_acceptance.py check`
against `https://api.akeno.online` and `https://akeno.online`. Nothing was written: no account, no e-mail, no
session, no license. Endpoints were called without credentials and with an empty body.

## Live-verified

| Check | Result |
|---|---|
| API over verified HTTPS | OK, server 1.0.0, API v1, not in maintenance, registration open |
| Website pages `/forgot-password`, `/reset-password` | HTTP 200 |
| Website page `/verify-email` | HTTP 400 without a token (expected) |
| `POST /auth/register`, `/auth/login`, `/auth/refresh` | present (422 without body) |
| `POST /auth/logout`, `GET`/`PATCH /auth/me` | present (401 without token) |
| `POST /auth/verify-email` | present (422) |
| `POST /auth/verify-email/resend` | present (401) |
| `POST /auth/password-reset/request`, `/auth/password-reset/confirm` | present (422) |
| `POST /auth/change-password` | present (401) |
| `GET /licenses/current`; `POST /licenses/validate`, `/licenses/redeem`, `/licenses/offline-certificate` | present (401) |
| `GET /devices`, `POST /devices/register` | present (401) |
| `GET /system/keys` | present (200) |

## Missing on production (server work for Codex, see `CODEX_ACCOUNTS_HANDOVER.md`)

| Endpoint / behavior | Status | What the app does meanwhile |
|---|---|---|
| `POST /auth/change-email` | 404, not deployed | Account page: "not offered yet" |
| `POST /auth/sessions/revoke-others` | 404, not deployed | Account page: "not offered yet" |
| `verification_email` in the register/resend response (honest delivery status) | not reported | App says "a verification link was requested" instead of "sent" |

The patch for all three is in `accounts-server.patch`. Apply it as a targeted integration on top of the secured
production code, not by deploying this branch's server.

## Build configuration (2026-10-24, run 4 on `claude/clever-fermi-xykqbw`: 24 passed, 0 failed)

| Item | Status |
|---|---|
| `AKENO_LICENSE_PUBLIC_KEYS` | set by the maintainer: `f382ecda000888b2:XlXm…VmVE`, equal to the server's only active key. Also committed in `akeno/data/licensing.json`. The check reports "server key f382ecda000888b2: embedded in the build". |
| License API / website | `https://api.akeno.online`, `https://akeno.online` (defaults, variables not needed) |
| Release verification key | `d6139235b220a4aa` committed in `akeno/data/release-keys.json`, equal to `GET /api/v1/releases/signing-key` |
| Update source | AKENO Release Manager, `GET /api/v1/releases/stable` (`akeno/data/distribution.json`). Release-related variables are not needed (`CODEX_VPS_INTEGRATION.md` §B). |
| Fixed in this branch | the production check failed because `configure-licensing.py` read the variable as JSON, and CI builds shipped no license key because the license step ran only when `AKENO_LICENSE_SERVER_URL` was set |

## Release Manager on production (read-only, 2026-10-24)

* The OpenAPI document publishes `GET /releases`, `/releases/latest`, `/releases/stable`, `/releases/{version}`
  and `/releases/signing-key`.
* The signing key is `d6139235b220a4aa`.
* No release is published yet: `/releases/stable` returns 404 and `/releases` returns `[]`.
* `GET /system/version`:
  * `download_url: https://akeno.online/downloads/latest`;
  * `latest_client_version` and `min_client_version`: not set.

## Not verifiable without approval

Real registration, the verification e-mail, a password reset, and redeeming a license require an authorized test
address and your OK. Run `tools/account_acceptance.py flow` with that address against production. It creates one
account and sends real e-mails.
