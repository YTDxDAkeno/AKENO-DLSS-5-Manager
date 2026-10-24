# License trust model (AKENO Gaming Suite 3.0)

The Windows app accepts a license only if it was signed by the official AKENO license server's private key.
That key exists on the VPS only (the `keys` volume, mode 0600). Nothing the server sends unsigned can unlock the app.

## What is where

| | Windows app (AKENO.exe) | License server (VPS) |
|---|---|---|
| Ed25519 private signing key | never | yes (only there) |
| Ed25519 public verification key(s) | embedded at build time (`akeno/data/licensing.json`) | published at `/api/v1/system/keys` |
| License server address | embedded at build time; **cannot be changed** in a built AKENO.exe | — |
| Passwords, database credentials, GitHub tokens | never | database credentials in its own `.env`, never in Git |

The release build writes `licensing.json` from the repository variables `AKENO_LICENSE_SERVER_URL` and `AKENO_LICENSE_PUBLIC_KEYS`:
- `tools/configure-licensing.py --require` refuses to build without both.
- It prints the key ids, to compare with `akeno-server keys list` on the VPS.

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
