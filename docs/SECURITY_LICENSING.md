# License trust model (AKENO Gaming Suite 3.0; module licenses since 3.2.0)

The Windows app accepts a license only if it was signed by the official AKENO license server's private key.
That key exists on the VPS only (the `keys` volume, mode 0600). Nothing the server sends unsigned can unlock the app.

## What is where

| | Windows app (AKENO.exe) | License server (VPS) |
|---|---|---|
| Ed25519 private signing key | never | yes (only there) |
| Ed25519 public verification key(s) | embedded at build time (`akeno/data/licensing.json`) | published at `/api/v1/system/keys` |
| License server address | embedded at build time; **cannot be changed** in a built AKENO.exe | — |
| Passwords, database credentials, GitHub tokens | never | database credentials in its own `.env`, never in Git |

Every Windows build writes `licensing.json` from the repository variables `AKENO_LICENSE_SERVER_URL` (default `https://api.akeno.online`), `AKENO_WEBSITE_URL` (default `https://akeno.online`) and `AKENO_LICENSE_PUBLIC_KEYS` (`kid:public_key`, comma-separated):
- The committed `licensing.json` already holds the production key `f382ecda000888b2` (the one `GET /api/v1/system/keys` publishes). An unset variable keeps the committed keys; a set one replaces them.
- `tools/configure-licensing.py --require` refuses a release build without an address and at least one key.
- It prints the key ids, to compare with `akeno-server keys list` on the VPS.
- `tools/check_build_config.py` then checks the packaged `dist\AKENO` and the portable ZIP: official API and website, at least one license key and one release key, every key id matching its key, license and release keys not the same, no private key material, and (when the CI job has the secret) the release signing key nowhere in the build.

Accounts (sign-up, e-mail confirmation, password reset, sessions and what the server must be configured with) are described in `docs/ACCOUNT_SYSTEM.md`.

## What the app checks

Every signed document is a JWS with EdDSA (Ed25519). It must have:
- **Signature:** by an embedded key, or by a key that an embedded key endorsed (rotation). Endorsements are re-checked every time the keys are used. A key a server merely offers, or one it signs for itself, is never trusted. A build without embedded keys accepts no license at all; there is no "trust on first use".
- **Type** (JWS `typ`): `akeno-validation`, `akeno-lease`, `akeno-offline-license` or `akeno-key-endorsement`. A document of one type never passes as another.
- **Issuer:** `iss = akeno-licensing`.
- **Audience:** `aud = akeno-desktop`.
- **Entitlement:** `ent = akeno-suite`.
- **Device binding:**
  - `dev`: the device id the server assigned to this PC;
  - `dfp`: the SHA-256 fingerprint of this PC's Windows MachineGuid.

  A state file copied to another PC unlocks nothing.
- **Validity window:**
  - lease: `nbf` and `exp`;
  - trial and monthly: the license end `lexp` caps the lease;
  - the offline grace (default 72 h) ends the lease earlier when the app cannot reach the server.

  Turning the clock back is detected and makes time-limited leases untrusted until the next online check.
- **Freshness (online check):**
  - every validation request carries a new random 24-byte nonce;
  - the answer counts only with a signed `akeno-validation` statement and a lease that both carry that nonce;
  - the statement is also bound to this account and PC.

  So a recorded answer, even a genuine "ok" from last month, is rejected as a replay.
- **Unsigned fields have no effect:** `result`, `license_valid`, `certificate_revoked`, `server_time` and the license summary in the JSON are ignored. What the app does (unlock, lock, remove the offline certificate, learn the server's time) comes from the signed statement only.
- **Transport:**
  - HTTPS only, certificate and host name verified against the Windows certificate store, TLS 1.2 or later;
  - no HTTP redirects are followed;
  - plain HTTP to `127.0.0.1` exists only when running from source, for tests.

A failed check never deletes data. The saved lease or certificate stays exactly as it was, and the app reports "The license server's answer could not be verified".

## Lifetime licenses

Activating a lifetime license gives the PC an `akeno-offline-license` certificate:
- signed, with no end date, bound to this PC;
- AKENO then runs without internet for good, including after years offline and after clock changes.

Two things replace the certificate:
- a newer certificate for the same PC;
- a signed `crev` (certificate revoked) statement from the official server, for example when the PC is removed from the account or the license is revoked.

## Tests

The tests are in `tests/test_licensing_client.py` (app logic with a signing fake server) and `server/tests/test_client_e2e.py` (the app against a real running server).

- **Attacker VPS:** the real server software with its own keys and its own lifetime codes cannot unlock the app, and its keys are never learned.
- **Fake server:** its own keys, a self-endorsed key, `license_valid: true` and `result: ok` are all rejected.
- **Bad tokens:** unsigned answers, tampered payloads with the old signature, and tokens with the wrong `aud`, `iss` or `dev` are rejected.
- **Replays:** a genuinely signed old "ok" answer cannot re-unlock an expired license.
- **Forged lifetime certificates** are rejected: signed by another key, wrong audience, not lifetime, another PC's fingerprint.
- **Authentic lifetime certificates** work offline three years later and after the clock is turned back.
- **Expiry and grace:** monthly runs offline inside the 72 h grace and locks after it; a 24 h trial ends after 24 h, also offline.
- **Revocation:** an unsigned or foreign "certificate revoked" does not delete the lifetime certificate.
- **Built program:** it ignores the address environment variable and the user file, refuses plain HTTP and refuses address changes.
- **No keys:** a build without keys accepts no license and pins no server key.
- **Rotation:** a key endorsed by the old key is learned (`test_rotated_key_is_learned_through_endorsement`).

## Module licenses (AKENO 3.2.0)

An account can hold **AKENO Full** (`akeno-suite`, the existing `licenses` row, unchanged) and **single modules**
next to it (`feature_grants`: `akeno-vpn` = AKENO VPN, `akeno-lan-play` = AKENO LAN Play; the catalog already maps
Desktop Studio, DLSS Manager, Performance Tools and Gaming Tools, which are not offered yet). AKENO Full includes
every module, also modules added later. A module grant never stands for AKENO Full.

**Two document families.** The original family (`akeno-lease`, `akeno-offline-license`, `akeno-validation`,
`ent = akeno-suite`) is issued exactly as before and **only for a running AKENO Full license**, so AKENO 3.1.1 and
older keep working and can never mistake a module grant for the suite: for a VPN-only account they get a signed
`no_license` answer. AKENO 3.2.0 sends `access: 1` and receives the access family:

| Document (JWS `typ`) | `ent` | What it adds |
|---|---|---|
| `akeno-access-lease` | `akeno-access` | `grants`: every running entitlement with its type and own expiry; `nonce`, `dev`, `dfp`, `exp` as before |
| `akeno-access-offline` | `akeno-access` | `ents` + `grants`: the account's **lifetime** entitlements on this PC (no end date) |
| `akeno-access-validation` | `akeno-access` | the signed answer: result, `crev`, grants; bound to nonce, account and PC |

The app checks issuer, audience, `ent`, signature (embedded or endorsed key), device binding and validity exactly as
for the original family; in addition each entitlement stops at its own `exp` even while the lease runs (AKENO Full
for 30 days ends on time also offline, AKENO VPN Lifetime stays). Entitlement ids this build does not know are
ignored. A certificate whose `ents` and `grants` differ is not accepted. An access document never passes as an
original document or the other way round (different `typ` and `ent`).

**Updating from 3.1.1:** the original lifetime certificate stays valid after the update (AKENO starts offline as
before) and is replaced by an access certificate at the next online check. A server without module licenses
refuses the `access` field (`validation_error`); the app then asks the original way (suite only) for that run.

**Where the license is enforced** (not only in the window):

| Boundary | Check |
|---|---|
| Main window | pages of modules outside the license are not built at all (a lock page stands in); start-up scans, downloads check, compatibility updates, Cloud Sync and Desktop Studio run only with their module |
| Desktop Studio host (own process) | starts and keeps running only with `allows("desktop")` |
| Elevated network helper | free VPN, managed VPN and own WireGuard connections need `vpn`; LAN Play needs `lan_play`; installing the network or VPN components needs a network module; checked before anything native happens. Disconnect and recovery always work |
| License server, LAN Play API and coordinator | `entitlements.has(user, akeno-lan-play)` (AKENO Full or AKENO LAN Play) for rooms, members and every WireGuard handshake |
| License server, AKENO VPN API and exit agents | `entitlements.has(user, akeno-vpn)` |

**Codes.** Unchanged format (`AKENO-XXXX-XXXX-XXXX-XXXX`, 80 random bits, only an HMAC verifier stored, single use
under concurrency). New: `entitlement` (what it unlocks) and `duration_days` (a time code's own length: 30, 90,
365 or custom days). The unused code's expiry (`expires_at`) stays separate from the length of the license it gives.
Stacking: time adds up from the later of now and the current expiry; a lifetime grant is never shortened (a time code
for it is refused and stays unused); a module code is refused while a lifetime AKENO Full already includes it.
An app older than 3.2.0 is told to update instead of using up a module code. AKENO VPN licenses (codes and grants)
stay switched off (`vpn_module_licenses`) until a super admin switches them on after the use of VPN Gate in AKENO is
cleared.

**Tests:** `server/tests/test_feature_licenses.py` (server), `server/tests/test_client_modules_e2e.py` (the app's
licensing code against a real server: VPN only, upgrade, Full ending, 3.1.1 update, old server, revocation,
tampering), `tests/test_feature_licenses_client.py` (documents, offline expiry, forged/foreign/unknown grants, the
main window), `tests/test_network_helper.py` (helper boundaries), `server/tests/test_cli_migrations.py` (migration
0002 with existing data and back) and the Windows run `.github/workflows/feature-licenses-windows.yml` (a VPN-only
license from code to a real VPN connection through the real helper).
