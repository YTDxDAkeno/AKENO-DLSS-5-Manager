# Accounts in the Windows app: contract, configuration and status

This describes how AKENO (the Windows app) signs people up, confirms their e-mail address, resets passwords and
manages sessions against the AKENO server, which endpoints it uses, and what the production server must be
configured with. It is the hand-over for the production deployment (`api.akeno.online`, `akeno.online`,
`admin.akeno.online`).

## Architecture

```
AKENO.exe ──HTTPS──▶ https://api.akeno.online/api/v1/…   (akeno-api, port 8410)
                          │ creates the token (only its SHA-256 is stored), sends the e-mail
                          ▼
                     SMTP host272.checkdomain.de:465 (SSL) ──▶ the user's inbox
                                                                 │ link
                                                                 ▼
                     https://akeno.online/verify-email?token=…   (akeno-web, port 8411)
                     https://akeno.online/reset-password?token=…
```

* The app never talks to SMTP and holds no mail credentials, API keys or passwords. It asks the API; the API sends.
* The links in e-mails open the website, never the app. The app learns the result from the server
  (`GET /auth/me` → `email_verified`), never from a local flag.
* The app's server address is fixed at build time (`akeno/data/licensing.json`, see `docs/SECURITY_LICENSING.md`).
  Defaults in the repository: API `https://api.akeno.online`, website `https://akeno.online`.

## Endpoints the app uses

All under `https://api.akeno.online/api/v1`. Errors are `{"error": {"code", "message", "details"}}`; the app
decides on `code` (see `akeno/ui/account_ui.py`: `ERRORS`, `SERVICE_ERRORS`, `NETWORK`).

| Purpose | Request | Answer the app relies on | Existed before |
|---|---|---|---|
| Server settings, connection status | `GET /system/version` | `maintenance`, `registration_enabled`, **`require_email_verification`** (new field) | yes (+1 field) |
| Register | `POST /auth/register` `{email, password, display_name, language}` | 201 tokens + `user` + **`verification_email`: `sent` \| `failed` \| `unavailable`** (new field) | yes (+1 field) |
| Sign in | `POST /auth/login` `{email, password, totp_code?}` | tokens; 401 `invalid_credentials` / `totp_required` / `invalid_totp`; 429 `rate_limited`; 403 `account_disabled` | yes |
| Renew the access token | `POST /auth/refresh` `{refresh_token}` | rotated tokens; 401 `invalid_refresh_token` (ended: sign in again); 403 `account_disabled` | yes |
| Sign out | `POST /auth/logout` | 204 | yes |
| Account state | `GET /auth/me` | `email`, `display_name`, `email_verified`, `two_factor`, `created_at` | yes |
| Display name | `PATCH /auth/me` `{display_name}` | user | yes |
| Resend confirmation | `POST /auth/verify-email/resend` | 202 `{status: "sent", email}` only when the mail server accepted it; **503 `email_unavailable` / `email_delivery_failed`** otherwise; 429 with `retry_after` | yes (now honest) |
| Change / correct the address | **`POST /auth/change-email`** `{password, new_email}` | 202 `{status: "sent", email}`; 400 `wrong_password`; 409 `email_taken`; 503 as above | **new** |
| Change password | `POST /auth/change-password` `{current_password, new_password}` | 200; 400 `wrong_password` / `weak_password` (other sessions end, this one stays) | yes |
| Sign out other devices | **`POST /auth/sessions/revoke-others`** | `{revoked: n}` | **new** |
| Forgot password | `POST /auth/password-reset/request` `{email}` | 202 `{status: "sent_if_account_exists"}` for every address; **503 `email_unavailable`** for every address when the server cannot send e-mail at all | yes (now honest) |
| License, PCs | `/licenses/*`, `/devices*` | unchanged (see `docs/SECURITY_LICENSING.md`) | yes |

`POST /auth/password-reset/confirm` and `POST /auth/verify-email` are used by the website (and the tests), not by
the app.

All changes to v1 are additive: older apps keep working against the new server, and the new app works against an
older server (it then shows "A verification link was requested" instead of "We've sent", and the "Continue without
verifying" link when it cannot know whether verification is required).

## Server changes in this branch (reviewable, not deployed)

`server/akeno_server/…`:

* `mail.py`
  * `send()` returns whether the message was accepted (SMTP, the file sink, or the log).
  * `deliverable()`: only SMTP with a host reaches people in production.
  * A production log never contains a message body (links carry secrets).
  * The Message-ID uses the sender's domain.
  * A new `email_changed` notice goes to the previous address.
* `auth/service.py`
  * A new verification or reset link ends older unused ones, but only once the new e-mail was accepted (a failed
    send keeps the last working link).
  * A password change ends every open reset link.
  * Changing the address notifies the old address.
  * A reset request fails with 503 `email_unavailable` for every address (before the lookup) when no e-mail can be
    delivered.
* `api/auth.py`
  * `verification_email` on registration.
  * Honest `resend`.
  * New `change-email` and `sessions/revoke-others`.
* `api/system.py`: `require_email_verification`.
* `web/public.py`, `web/account.py`: the website also says when a confirmation e-mail could not be sent.

No database migration, no new table or column. Tests: `server/tests/test_account_mail.py` (20) and the account
section of `server/tests/test_client_e2e.py` (8, the app's code against a running server).

**Integrating into production:** see `docs/handover/CODEX_ACCOUNTS_HANDOVER.md`. It covers the targeted patch `accounts-server.patch`, a hunk-by-hunk intent list for conflicts with production fixes, the rollout and rollback, and the acceptance check `tools/account_acceptance.py`.

## Production configuration this needs (for the VPS)

1. **`AKENO_PUBLIC_URL=https://akeno.online`** in the environment of *akeno-api* (and akeno-web).
   * E-mail links are built from it.
   * Without it, the API builds them from its own address (`https://api.akeno.online/reset-password?…`). The API
     container does not serve those pages, so every link in every e-mail would be broken.
2. Caddy:
   * `api.akeno.online` → `127.0.0.1:8410`. The app calls `/api/v1/…` on that host.
   * `akeno.online` → `127.0.0.1:8411`. This host serves `/verify-email`, `/reset-password`, `/forgot-password`
     and `/account`.
   * `admin.akeno.online` → `127.0.0.1:8412`.
3. E-mail through checkdomain (the credentials go only into the server's `.env` or a secret file, never into Git
   or the app):

   ```
   AKENO_MAIL_BACKEND=smtp
   AKENO_SMTP_HOST=host272.checkdomain.de
   AKENO_SMTP_PORT=465
   AKENO_SMTP_SECURITY=ssl
   AKENO_SMTP_USER=<the mailbox, e.g. no-reply@akeno.online>
   AKENO_SMTP_PASSWORD_FILE=/run/secrets/smtp_password     # or AKENO_SMTP_PASSWORD in .env (mode 0600)
   AKENO_MAIL_FROM=AKENO <no-reply@akeno.online>             # the authenticated mailbox, or checkdomain may refuse it
   ```

   Until this is set, production (`AKENO_ENVIRONMENT=production` with `file` or `console`) behaves as follows:
   * Registration reports `verification_email: "unavailable"`, and the app says so.
   * A reset request answers 503 `email_unavailable`, and the app says e-mails cannot be sent right now.
   * Nothing claims an e-mail was sent.
4. DNS for deliverability: SPF for checkdomain's servers, DKIM if checkdomain offers it, and a DMARC record for
   `akeno.online`.
5. Optional: `require_email_verification` (admin website → Settings).
   * When on, codes can only be redeemed with a confirmed address.
   * The app then has no "continue without verifying" link.

## Security behaviour

| Topic | How |
|---|---|
| Reset and verification tokens | 256-bit random (`secrets.token_urlsafe(32)`), only SHA-256 stored, reset 1 h / verification 48 h, single use, older ones end when a newer one was sent, all reset links end on any password change |
| Enumeration | A reset request answers the same for every address. A per-message SMTP failure is not reported for resets. Only "no e-mail can reach anybody" is, and then for every address. |
| Rate limits (server) | sign-in 10/min per IP and per address, registration 5/h per IP, reset 5/h per IP and 3/h per address, resend and address change 3/h per account. The app shows the server's `retry_after` and counts it down. |
| Sessions | The access token (15 min) is kept in memory only. The refresh token (30 days, rotated on every use, reuse ends the session) is stored DPAPI-encrypted for the Windows user. "Keep me signed in" off: it is never written to disk. |
| Passwords | Never stored, logged or put into URLs. Argon2id on the server. The app checks the server's exact policy before sending and the server checks again. |
| Password change / reset | All other sessions end. On reset, this PC is asked to sign in again; the signed license keeps running its course offline. |
| Transport | HTTPS only, the system certificate store, TLS 1.2+, no redirects followed (unchanged). |
| Retries | Only `GET` is repeated (once, after a dropped connection). Registration, reset, password and address changes are never re-sent automatically. |
| Logs | No tokens, passwords or e-mail bodies (tested: `tests/test_account_client.py::test_no_credentials_in_the_log`, `server/tests/test_account_mail.py::test_production_log_never_contains_links`). |

## What the app shows

* **Welcome screen** (`akeno/ui/license_gate.py`):
  * sign-in (show/hide password, "keep me signed in", forgot password, authenticator code when needed);
  * registration (display name optional, e-mail, password with the server's rules and a strength meter, confirmation);
  * "Check your inbox" (masked address, what the server said about the e-mail, resend with countdown, change the
    address, "I've verified", automatic check every 15 s);
  * forgot password and its confirmation (same text for every address, send again after a minute);
  * activation, device limit;
  * a connection status (connected, offline, maintenance) at the bottom.
* **My Account** (`akeno/ui/pages/account.py`):
  * profile (name, e-mail, verified, member since, two-factor);
  * security (change password, change e-mail, this session, sign out of other devices, sign out);
  * the license at a glance;
  * the account's PCs.
  * When the session ended, a banner offers "Sign in again" without closing the suite.
* The user's display name is called "Display name": the server has no username, and sign-in is by e-mail address.

## Status

| | |
|---|---|
| Implemented and tested locally | everything above (app: `tests/test_account_*.py`; server: `server/tests/test_account_mail.py`, `server/tests/test_client_e2e.py`) |
| Verified against production | **no**: the production hosts were not reachable from the development environment |
| Waiting for deployment | the server changes of this branch (until then the production API reports neither delivery status nor `change-email` / `revoke-others`; the app handles their absence) |
| Waiting for SMTP credentials | real e-mail delivery through checkdomain; no e-mail was sent to a real inbox in testing |
