# Distribution, updates and the private source repository

## Architecture

| Place | Holds | Who can read it |
|---|---|---|
| **Private source repository** (`AKENO-DLSS-5-Manager`) | source code of the app and the license server, CI, development history | the owner and invited collaborators |
| **Public release repository** (`AKENO-Releases`, variable `AKENO_RELEASE_REPO`) | installers, portable packages, `SHA256SUMS.txt`, the corresponding source archive of each release, signed channel manifests (`channels/stable.json`, `channels/beta.json`), the compatibility database, the community index | everyone, without a GitHub account |
| **VPS** | license server only: sign-in, accounts, the license database, activation codes, validation, devices, signed offline licenses, the admin website, audit logs | the public API and website over HTTPS; the database never listens publicly |

The Windows app contains:
- no GitHub token, deploy key or repository credential;
- no license signing key;
- no database credential.

It holds only public verification keys: the license keys and the release keys.

## Publishing a release

The `publish-release` job of `.github/workflows/build.yml` runs on a `v*` tag or a manual run with a channel. It works from the Windows artifacts tested in the same workflow run:
1. checks that the tag equals `__version__` (`v3.0.0` for `3.0.0`);
2. adds the **corresponding source archive** `AKENO-Gaming-Suite-<v>-source.zip` (`git archive` of the tagged commit) and writes `SHA256SUMS.txt`;
3. signs the channel manifest with the release key (`tools/release_manifest.py sign`): versions, SHA-256 and sizes of every file;
4. creates the GitHub release in the public release repository with all files, and commits `channels/<channel>.json` and the compatibility database there.

One-time setup in the **private source repository**, under Settings → Secrets and variables → Actions:

| Kind | Name | Value |
|---|---|---|
| Variable | `AKENO_RELEASE_REPO` | `YTDxDAkeno/AKENO-Releases` |
| Variable | `AKENO_RELEASE_PUBLIC_KEYS` | the `kid:public_key` line printed by `python tools/release_manifest.py keygen --private-out release.key` |
| Secret | `AKENO_RELEASE_SIGNING_KEY` | the content of `release.key` (then delete the file; keep an offline copy in a password manager) |
| Secret | `AKENO_RELEASE_REPO_TOKEN` | a fine-grained personal access token with **only** the release repository selected and **Contents: Read and write** (no other permission) |
| Variable | `AKENO_LICENSE_SERVER_URL`, `AKENO_LICENSE_PUBLIC_KEYS` | as before (see `SECURITY_LICENSING.md`) |

Release builds (tags and published channels) refuse to build without the license address, the license keys and the release keys.

## How the updater works

The updater lives on ACCOUNT → Updates.

1. It reads `https://raw.githubusercontent.com/<release repo>/main/channels/<channel>.json`. The channel is Stable or Beta. No login is needed.
2. It verifies the manifest's Ed25519 signature against the release keys built into the app. It refuses:
   - another app or another channel;
   - a version older than one already seen (no freeze or replay);
   - file addresses outside GitHub over HTTPS.
3. It downloads the portable package and checks it against the manifest's SHA-256 and size.
4. It unpacks into `%LOCALAPPDATA%\AKENO\updates\staging`, refusing `..`, absolute paths and links.
5. It starts the **new version's own AKENO.exe** as a separate updater process (`--apply-update`), and the running AKENO quits. The updater process:
   1. waits for the old process to end;
   2. moves the program folder aside as the backup of the previous version;
   3. copies the new version in, carrying over a portable data folder if there is one;
   4. starts the new version and waits until it reports a good start;
   5. otherwise restores the previous version and starts that.
6. **Rollback:** "Roll back to <previous version>" swaps the kept previous version back in the same way.

This works for installed and portable copies, because the installer installs per user into `%LOCALAPPDATA%\Programs` and needs no administrator rights. The installer removes the whole program folder on uninstall.

What an update never touches:
- `%LOCALAPPDATA%\AKENO`: license state (DPAPI), settings, game records, backups of game files, wallpapers, themes, sync state;
- the game folders.

The license check stays exactly as it was. The new version reads the same protected state, the same PC fingerprint and the same (or endorsed) public keys.

## Corresponding source code (GPL)

AKENO is licensed GPL-3.0-or-later.
- **AKENO itself:** every public release carries `AKENO-Gaming-Suite-<v>-source.zip`, the exact source of that version, next to the binaries. That is the "equivalent access" GPL-3.0 section 6(d) asks for when object code is offered for download. The development history and work in progress stay private.
- **Bundled components:** their sources and license texts are listed in `THIRD_PARTY_NOTICES.md` and shipped as the project already does.
  - Qt and PySide6 are LGPL-3.0, linked dynamically and replaceable in the program folder.
  - FFmpeg comes as LGPL builds inside PySide6.
  - The ReShade, Feeder and community bundles ship with their sources.

Changing the license of future versions is a decision only the copyright holder(s) can make, and only for code they own. It is not done here.

## Effects of making the source repository private

- **GitHub Actions:** runs keep working. On the Free plan, private repositories get 2,000 Actions minutes per month, and Windows minutes count twice. The Windows job of this workflow takes about 25–40 minutes, so expect roughly 30 full runs per month before the minutes run out. Pushes to every branch trigger a run; limit the `push` trigger to `main` and pull requests if the minutes get tight.
- **The `release` environment** of the publish job: on GitHub Free, environments in private repositories can't be configured, and protection rules (such as required reviewers) and environment secrets reportedly no longer apply. That is why the publish secrets are repository secrets: publishing keeps working, just without an approval step. On a paid plan, add required reviewers to the `release` environment for that step.
- **Releases:** existing releases of the source repository can no longer be downloaded without access. Public downloads move to the release repository.
- **Older installed versions:** AKENO 2.x checks `api.github.com/repos/YTDxDAkeno/AKENO-DLSS-5-Manager/releases/latest` and its compatibility database URL in that repository. Both return 404 once it is private, and those versions say "Update check failed". Their users install 3.0 from the release repository once; from then on, updates come from there.
- **The VPS** needs no GitHub access (see `deploy/README.md`).
- **Development:** it continues unchanged with the owner's GitHub account (and Claude's GitHub app on the repository), which keep full access.
