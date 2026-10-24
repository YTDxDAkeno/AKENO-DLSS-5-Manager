# Hand-over to Codex: account changes for the production server

**Kurzfassung (Deutsch).**
- Dieser Branch ändert neun Dateien im Server-Paket `server/akeno_server`. Er bringt keine Datenbankmigration und keine Änderung an `deploy/`, Lizenz-Signierung, Updates oder Admin-Rechten mit.
- Nichts davon ist auf dem VPS installiert.
- Bitte **nicht** den Server-Stand dieses Branches komplett neu ausrollen. Stattdessen `accounts-server.patch` gezielt auf den produktiven Quellstand mit euren Sicherheitskorrekturen anwenden, die Tests laufen lassen und erst dann mit `tools/account_acceptance.py` abnehmen.
- Pflicht in der Produktion: `AKENO_PUBLIC_URL=https://akeno.online` und SMTP über checkdomain.

---

## 0. Ground rules

* Nothing in this branch was deployed, and the VPS was not touched. The production hosts were not even reachable
  from the development environment.
* Your production fixes are not in this repository: no branch or commit contains them. So this hand-over:
  * cannot show you a merged result;
  * gives you a **patch against the AKENO 3.0 server (commit `95ac04f`)**;
  * gives you the **intent and the invariants of every hunk**, so it can be re-applied on top of your fixes when a
    hunk does not apply.
* **Your fixes win.** Where a hunk conflicts with a security fix, keep your code and re-create only the behaviour
  listed for that hunk.
* Do not redeploy the server from this branch wholesale. It would undo every production fix made outside Git.

## 1. What to integrate

| File in `docs/handover/` | What |
|---|---|
| `accounts-server.patch` | The runtime changes: `server/akeno_server/` only, 9 files. `git apply --check` and `patch -p1 --dry-run` were verified against `95ac04f`. |
| (in the repository) `server/tests/test_account_mail.py` | 20 tests of the server changes |
| (in the repository) `server/tests/test_client_e2e.py`, section "account flows" | 8 tests: the app's client code against a live server (it needs `akeno/licensing/` of this branch next to `server/`, as in the repository) |
| (in the repository) `server/tests/test_account_acceptance.py` | 4 tests of the acceptance tool against a live server |
| (in the repository) `tools/account_acceptance.py` | The acceptance check against production (§6) |

No migration, no new table, no new column, no new dependency, no new environment variable that is required for the
code to start.

## 2. Procedure (targeted, reversible)

1. **Write down the current state.**
   * Note the image tag or commit the three services run (`docker compose images`).
   * Run `deploy/backup.sh`. The change does not touch the schema, but a backup before any server change is the rule.
2. **Work on a copy of the production source**, never in the running containers. Start a new branch, e.g.
   `integrate/app-accounts`.
3. **Check first, then apply:**
   ```
   git apply --3way --check docs/handover/accounts-server.patch   # with the repository history
   patch -p1 --dry-run < docs/handover/accounts-server.patch      # without history (a bundle)
   ```
   Then apply. For every rejected hunk, use §3. It says what the hunk must achieve and what it must not break.
4. **Copy the tests** listed in §1. Run the whole server suite on SQLite and on PostgreSQL 16:
   ```
   cd server && python -m pytest -q
   AKENO_TEST_DATABASE_URL=postgresql://… python -m pytest -q
   ```
   Your own tests for your fixes must still pass. If one of ours contradicts one of your fixes, the fix wins: adapt
   our test and note why.
5. **Configure** (`.env`, mode 0600; secrets preferably as `*_FILE`), see §5.
6. **Roll out** one image built from that source to `akeno-api`, `akeno-web` and `akeno-admin`. They share the
   code: the admin website's "send reset" uses the same mail functions. No migration step runs.
7. **Accept** with §6. First `check` (read-only), then `flow` with a test mailbox.
8. **Roll back** if needed by starting the previous image tag again. Nothing needs to be undone in the database:
   * the new code only writes rows the old code already writes (tokens, sessions);
   * it sets `expires_at` of superseded links to "now", which the old code reads the same way.

## 3. The changes, hunk by hunk

The conflict risk is our estimate of how likely a production security fix touched the same lines.

### `mail.py`

| # | Change | Must hold afterwards | Conflict risk |
|---|---|---|---|
| M1 | `send()` returns `bool`: True when SMTP, the file sink or the log accepted the message; False on any exception or a refused recipient. It never raises. | Every caller that ignores the result keeps working. No password or SMTP response is logged beyond the exception text. | **high** if you changed SMTP handling (TLS context, port 465, timeouts): keep your connection code and only add `return True`/`return False`. |
| M2 | `deliverable()`: SMTP with a host → True. `file`/`console` → False in production, True in development and test. | Production never claims delivery through the file sink or the log. | low |
| M3 | The `console` backend logs only recipient and subject in production (the body holds links with tokens). | No token in production logs. | low (keep yours if you already removed bodies) |
| M4 | Message-ID uses the domain of `AKENO_MAIL_FROM` (`@akeno.online`) instead of `akeno.invalid`. | Better deliverability. | low |
| M5 | New text `email_changed` (en/de), sent to the previous address. | `tests/test_web_i18n.py` requires both languages. | low |

### `auth/service.py`

| # | Change | Must hold afterwards | Conflict risk |
|---|---|---|---|
| A1 | `_retire(model, user_id)` sets `expires_at = now` on the account's unused links. | Only that account's rows. | low |
| A2 | `send_verification()` returns whether the e-mail was accepted, and only then replaces older links. | A failed send leaves the last working link valid. | medium |
| A3 | `request_reset()`: if not `mail.deliverable()`, raise 503 `email_unavailable` **before** the address is looked up. Older reset links end only after a new one was accepted. | Same answer for every address (no enumeration). A per-message SMTP error stays invisible. | **high** if you hardened reset (timing, limits): keep your hardening, add the deliverable check at the very start. |
| A4 | `set_password()` also ends every unused reset link. | A reset link from before a password change can no longer change the password. | medium |
| A5 | `verify_email()` sends `email_changed` to the old address when the address changes. | — | low |
| A6 | `change_email()` returns the send result. | — | low |
| A7 | `email_unavailable()`, `delivery()` helpers. | `delivery()` returns `sent`, `failed` or `unavailable`. | low |

### `api/auth.py`, `api/schemas.py`, `api/system.py`

| # | Change | Must hold afterwards | Conflict risk |
|---|---|---|---|
| P1 | `POST /auth/register` adds `verification_email` (`TokensOut.verification_email`, optional, default `null`). | Login and refresh answers are unchanged (`null`). | low |
| P2 | `POST /auth/verify-email/resend`: `already_verified` first (not rate-limited). Then rate limit, then 503 `email_unavailable` / `email_delivery_failed`, or 202 `{status: "sent", email}`. | Same rate-limit key as before: `reset_email`, `verify:<user id>`. | medium |
| P3 | **New** `POST /auth/change-email` `{password, new_email}`. Rate limit `reset_email` with key `email-change:<user id>` (as the website). 503 when e-mail is not deliverable. 400 `wrong_password`, 409 `email_taken`, 202 `{status: "sent", email}`. | The address changes only when the link is opened. | low (new route) |
| P4 | **New** `POST /auth/sessions/revoke-others` → `{revoked: n}`. | The calling session stays. | low (new route) |
| P5 | `GET /system/version` adds `require_email_verification`. | — | low |

### Website (`web/public.py`, `web/account.py`, `web/i18n.py`, `web/de.json`)

| # | Change | Conflict risk |
|---|---|---|
| W1 | Registration flash: "…could not be sent…" when the confirmation e-mail was not sent. | low |
| W2 | Resend and address change on the account page: an honest error when not deliverable or not sent. | medium, if you changed CSRF or limits there: keep yours |
| W3 | Four new German strings. | low |

## 4. The contract the app relies on

Unchanged endpoints and the app's error codes are in `docs/ACCOUNT_SYSTEM.md`. The app checks errors by `code`, so
keep these codes even if you reword the messages:

```
POST /api/v1/auth/register              201 {..., "verification_email": "sent"|"failed"|"unavailable"}
POST /api/v1/auth/verify-email/resend   202 {"status": "sent"|"already_verified", "email": "..."}
                                        503 {"error": {"code": "email_unavailable"|"email_delivery_failed"}}
                                        429 {"error": {"code": "rate_limited", "details": {"retry_after": s}}}
POST /api/v1/auth/change-email          202 {"status": "sent", "email": "..."}   400 wrong_password   409 email_taken
POST /api/v1/auth/sessions/revoke-others 200 {"revoked": n}
POST /api/v1/auth/password-reset/request 202 {"status": "sent_if_account_exists"}  503 email_unavailable (any address)
GET  /api/v1/system/version             {..., "require_email_verification": bool}
```

What the new app does when these are missing (tested against the unmodified 3.0 server: 18/18):
* It says "a verification link was requested" instead of "we've sent".
* Address change and "sign out other devices" show "the AKENO server does not offer this yet". Nobody is signed out.

## 5. Production configuration

```
AKENO_PUBLIC_URL=https://akeno.online        # REQUIRED for akeno-api (and akeno-web)
AKENO_ADMIN_URL=https://admin.akeno.online
AKENO_MAIL_BACKEND=smtp
AKENO_SMTP_HOST=host272.checkdomain.de
AKENO_SMTP_PORT=465
AKENO_SMTP_SECURITY=ssl
AKENO_SMTP_USER=<the mailbox, e.g. no-reply@akeno.online>
AKENO_SMTP_PASSWORD_FILE=/run/secrets/smtp_password
AKENO_MAIL_FROM=AKENO <no-reply@akeno.online>  # the authenticated mailbox
```

* **Without `AKENO_PUBLIC_URL`**, the API builds e-mail links from its own host (`https://api.akeno.online/...`).
  The API container does not serve `/verify-email` or `/reset-password`, so every link would be dead.
  `tools/account_acceptance.py flow` fails exactly on that (`… link opens the website: FAIL`).
* **As long as the mail backend is `file` or `console` in production, after the integration:**
  * registration reports `unavailable`;
  * resend and address change answer 503 `email_unavailable`;
  * a password reset answers 503 `email_unavailable` for every address (app, website, and the admin website's
    "send reset").

  This is intended: nobody is told an e-mail was sent when it was not. Set up SMTP before or together with the
  integration.
* DNS: SPF covering checkdomain's servers, DKIM if checkdomain signs, a DMARC record.

## 6. Acceptance after the rollout

```
python tools/account_acceptance.py check
python tools/account_acceptance.py flow --email <a test mailbox you read> --confirm-real-account
```

* `check` only reads:
  * health and version over verified HTTPS;
  * whether the account changes are live;
  * the website pages `/forgot-password`, `/reset-password`, `/verify-email`.
* `flow` uses a real account. Run it against the test mailbox; the admin website can disable the account
  afterwards (it cannot delete accounts). It goes through:
  1. register (or sign in);
  2. "confirmation e-mail: sent";
  3. the confirmation link points to `https://akeno.online/verify-email` (you open it in the browser);
  4. the server confirms the address;
  5. a reset request;
  6. the reset link points to `https://akeno.online/reset-password` (you set a new password on the website);
  7. sign-in with the new password;
  8. the previous sign-in has ended.
* It never prints passwords or tokens. Exit code 0 means everything passed.

Then sign in once with the built Windows app (a build with the production keys): register, confirm, reset,
sign in.

## 7. The VPS-based release and update system

This branch does not touch the updater (`akeno/updater/`), `akeno/distribution.py`, `tools/release_manifest.py`,
the release keys, `deploy/` or `packaging/`. The server's `download_url` is unchanged. Overlaps to expect:

* `.github/workflows/build.yml`: the step "License server address and public keys" passes one more optional
  variable, `AKENO_WEBSITE_URL` (`--website-url=…`). If you rewrite the release steps, keep that one argument.
* `akeno/data/licensing.json` now defaults to `https://api.akeno.online` and `https://akeno.online`. Release
  builds still need `AKENO_LICENSE_SERVER_URL` and `AKENO_LICENSE_PUBLIC_KEYS`.
* When the welcome screen opens, the app reads `GET /api/v1/system/version` once for its connection status. This
  includes CI's "Packaged GUI starts normally" step: a read, no state change.

## 8. Tested here, and not

| | Result |
|---|---|
| Server tests, SQLite / PostgreSQL 16 | 144 / 144 (incl. 20 + 8 + 4 new) |
| AKENO 3.0 app and all 3.0 server tests against the new server | 112 / 112 |
| New app against the unmodified 3.0 server | 18 / 18 |
| App tests (Linux, offscreen Qt) | 727 passed, 11 skipped (the Windows-only tests, among them two with the real DPAPI, run in the Windows CI job; see the pull request for its result) |
| Against production, a real SMTP send, your production fixes | **not possible from here** |
