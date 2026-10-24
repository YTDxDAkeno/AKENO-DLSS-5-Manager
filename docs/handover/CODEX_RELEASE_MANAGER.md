# Hand-over to Codex: AKENO Release Manager ↔ Windows app

> **Superseded (2026-10-24).** The production API already has a Release Manager (`GET /api/v1/releases/stable`,
> `/releases/signing-key`, schema_version 1). The app now uses that contract. See `CODEX_VPS_INTEGRATION.md` §B.
> The static channel files described below remain a supported alternative (`AKENO_UPDATE_SOURCE=akeno`). They are
> not needed for the Release Manager.

**Kurzfassung (Deutsch).**
- Der Windows-Client kann Updates von eurem VPS beziehen. Dafür braucht er **kein neues API**, sondern dieselben
  signierten Kanal-Dokumente wie bisher, nur ausgeliefert über HTTPS unter `akeno.online`.
- Pro Release braucht er ein portables ZIP. Die Setup-EXE ist für die Website gedacht, nicht für den Updater.
- Signiert wird mit dem Release-Schlüssel **außerhalb des Servers**. Der VPS liefert Dateien aus; er kann keine
  Updates fälschen.
- Bis ihr die Adresse bestätigt, bleibt GitHub die Quelle. Das Umschalten ist eine Build-Variable, kein Code.

---

## 1. What the client does (implemented, tested; not yet pointed at production)

```
AKENO.exe ── GET {release_base}/channels/{stable|beta}.json ──▶ signed channel document
          ── verifies: Ed25519 release signature (built-in public keys), app, channel, version newer,
                       never older than the highest version seen (anti-rollback/replay), optional "expires"
          ── shows: version, channel, date, size, notes; "Update now" / "What's new" / "Later"
          ── on "Update now" + confirmation:
               GET {portable zip URL from the manifest} ── SHA-256 and size must match the manifest
               unpack (no absolute paths, no "..", no links) ── Authenticode rule (§5)
               separate updater process: program folder swapped, previous version kept, health check,
               automatic rollback if the new version does not start
```

* Hosts: only `https://` on `akeno.online` or its subdomains, as named in the build (`distribution.json`).
  Redirects are allowed only to those hosts. GitHub stays allowed as the fallback source.
* Automatic checks: on start and then at most every 6 hours. One notification per version; "Later" silences it.
* User data (`%LOCALAPPDATA%\AKENO`: licenses, settings, games, backups) is never part of the program folder and
  is never touched by an update.

## 2. What the Release Manager must serve

All over HTTPS with a valid certificate, without login, cookies or redirects to other domains:

| Path (below `release_base`) | Content | Notes |
|---|---|---|
| `channels/stable.json`, `channels/beta.json` | `{"format": "akeno-release-channel", "token": "<JWS>", ...}` exactly as written by `tools/release_manifest.py sign` | `Content-Type: application/json`, `Cache-Control: no-cache`; 404 if a channel has no release yet |
| the URLs inside the signed manifest | the release files, byte for byte | `Content-Length` correct, Range requests welcome (resume); any host on `akeno.online` |

Example `release_base`: `https://downloads.akeno.online/releases` (or `https://akeno.online/releases`). **You
choose it.** Tell us, and it becomes the repository variable `AKENO_RELEASE_BASE`.

The website may show the newest version by reading the same `channels/stable.json`: its readable fields
`release` and `files` sit next to the token. For anything that counts, use only the signed `token`.

## 3. Release files (one release)

| File | Kind | Who uses it |
|---|---|---|
| `AKENO-Gaming-Suite-<v>-portable-x64.zip` | `portable` | **the in-app updater** (required in every manifest) |
| `AKENO-Gaming-Suite-<v>-Setup-x64.exe` | `installer` | the website's download button (first installation) |
| `AKENO-Gaming-Suite-<v>-source.zip` | `source` | GPL-3.0 corresponding source (must be offered with every release) |
| `SHA256SUMS.txt` | `checksums` | people who verify by hand |

An uploaded `.exe` alone is **not** an update: the updater installs the portable ZIP (the same files the
installer would install), so it can swap the folder and roll back without running an installer. The Windows CI
job builds all four files (`AKENO-Gaming-Suite-windows-x64` artifact, and `git archive` for the source).

## 4. Signing (required) and where the key lives

* The manifest is signed with the **release private key** (Ed25519 seed).
  * It is created once with `python tools/release_manifest.py keygen`.
  * It is kept as the GitHub Actions secret `AKENO_RELEASE_SIGNING_KEY` or offline by the maintainer.
  * **It must not be stored on the VPS.** The VPS only delivers signed files; that is what makes it unable to
    push a forged update.
* The app trusts only the public keys built into it (repository variable `AKENO_RELEASE_PUBLIC_KEYS` →
  `akeno/data/release-keys.json`). Release keys and license keys are separate; neither can stand in for the other.
* Signing for the VPS:
  ```
  python tools/release_manifest.py sign --channel stable --release 3.1.0 --tag v3.1.0 \
      --base-url https://downloads.akeno.online/releases/3.1.0 --notes-url https://akeno.online/download \
      --expires-days 30 --notes "…" --out channels/stable.json \
      AKENO-Gaming-Suite-3.1.0-portable-x64.zip AKENO-Gaming-Suite-3.1.0-Setup-x64.exe \
      AKENO-Gaming-Suite-3.1.0-source.zip SHA256SUMS.txt
  python tools/release_manifest.py verify channels/stable.json --keys akeno/data/release-keys.json \
      --release-base https://downloads.akeno.online/releases
  ```
  Upload the four files to `…/releases/3.1.0/` first, then replace `channels/stable.json` (atomic rename), so no
  client ever sees a manifest whose files are missing.
* `--expires-days` (recommended: 30): a manifest that is not re-signed in time stops being offered. This protects
  against a server that keeps serving an old manifest. Re-sign the current release before it expires (same files,
  new date).
* **Never** sign an older version for a channel that already offered a newer one. Clients refuse it (replay
  protection) and keep the newer version.

## 5. Code signing (optional, recommended)

If `AKENO.exe` is Authenticode-signed (an OV/EV code-signing certificate), every later update must be signed by the
same signer, otherwise the client refuses it. Unsigned builds rely on the release signature and SHA-256 (as today).
Signing would also remove Windows SmartScreen warnings for the installer. The signing step would go before
"Portable ZIP" in `.github/workflows/build.yml`.

## 6. Switching a build to the Release Manager

Repository variables. Nothing is secret here:

```
AKENO_UPDATE_SOURCE=akeno
AKENO_RELEASE_BASE=https://downloads.akeno.online/releases      # your choice
AKENO_RELEASE_PAGE=https://akeno.online/download                # where "Download page" leads
AKENO_DOWNLOAD_HOSTS=downloads.akeno.online                     # other akeno.online hosts the manifest uses
AKENO_RELEASE_PUBLIC_KEYS=<kid>:<public key>                    # already used today
```

CI then writes `distribution.json` with `tools/release_manifest.py distribution`, which refuses anything outside
`akeno.online`. The packaged smoke test prints the result ("Updates: …; manifest …"). Builds made before the
switch keep updating from GitHub. So a bridge release must be published on GitHub once (with the switch inside),
or the website must offer the installer for a manual reinstall.

## 7. Acceptance tests for the Release Manager

1. `production-check.yml` (Actions) with `AKENO_RELEASE_BASE` set:
   * both channels are fetched over HTTPS;
   * `release_manifest.py verify` must print "verified: stable 3.x.y, signed by <kid>".
2. A beta first:
   1. publish 3.0.1-beta.1 to `channels/beta.json`;
   2. a 3.0.0 test PC on the beta channel (Updates → channel "Beta") shows "AKENO Gaming Suite 3.0.1-beta.1 is
      available.";
   3. "Update now" downloads, verifies and restarts;
   4. "Roll back" returns to 3.0.0.
3. Negative:
   * a changed byte in the ZIP → "The update was not installed. Nothing was changed";
   * a manifest signed with another key → "could not be verified";
   * an older manifest after a newer one → refused.
4. Data: license, settings and My Games are unchanged after the update and after the rollback.

## 8. Not done here (on purpose)

* No production upload, no release signed, nothing published: that needs your approval and the release key.
* No Release Manager endpoints were invented: the client needs only the static files above. If the Release Manager
  offers more (an admin upload API, statistics), the app does not depend on it.
