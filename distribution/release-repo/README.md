# AKENO Gaming Suite — downloads

The Windows downloads of the AKENO Gaming Suite are under [Releases](../../releases): the installer, the portable
package, `SHA256SUMS.txt` and the corresponding source code of each version (GPL-3.0-or-later). No GitHub account
is needed. AKENO's built-in updater reads `channels/stable.json` (or `channels/beta.json`), checks its release
signature and the SHA-256 of every file, and installs only what matches.

AKENO needs a license (24 hours, 72 hours, 30 days or Lifetime) from the AKENO license server; the program works
the same whichever way it was downloaded.

| Folder | Content |
|---|---|
| `channels/` | signed release manifests (written by the release workflow only) |
| `compatibility/` | the compatibility database the app downloads |
| `community/index.json` | the reviewed community index; add a package by pull request, report one with an issue |

This repository holds published files only. The source code is developed in a private repository; the source of
every released version is attached to its release.
