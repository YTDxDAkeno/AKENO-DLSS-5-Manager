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

## Build configuration that is still missing

| Repository variable | Status | Effect |
|---|---|---|
| `AKENO_LICENSE_PUBLIC_KEYS` | **not set** | A release build would accept **no** license. This is the only failed check. Set it to the server's active key(s) from `GET /system/keys` (`<kid>:<public key>`, comma-separated). The check lists them. |
| `AKENO_LICENSE_SERVER_URL`, `AKENO_WEBSITE_URL` | not set | Defaults apply: `https://api.akeno.online`, `https://akeno.online` (correct) |
| `AKENO_RELEASE_PUBLIC_KEYS`, secret `AKENO_RELEASE_SIGNING_KEY` | check in Settings → Variables | Without them the updater only shows the download page and never installs |
| `AKENO_UPDATE_SOURCE`, `AKENO_RELEASE_BASE` (+ `AKENO_RELEASE_PAGE`, `AKENO_DOWNLOAD_HOSTS`) | not set | Updates stay on GitHub Releases until the Release Manager path is confirmed (`CODEX_RELEASE_MANAGER.md`) |

Rerun the check under Actions → "Production read-only check" → "Run workflow" after setting them.

## Not verifiable without approval

Real registration, the verification e-mail, a password reset, and redeeming a license require an authorized test
address and your OK. Run `tools/account_acceptance.py flow` with that address against production. It creates one
account and sends real e-mails.
