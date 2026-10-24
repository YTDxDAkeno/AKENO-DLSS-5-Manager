# AKENO Gaming Suite 3.0.0

AKENO DLSS 5 Manager becomes the AKENO Gaming Suite. Everything of 2.1 stays as it was: the community DLSS 5 engine, FiveM Legacy, the RTX 4080/5090 profiles, the downloader, 32-bit support, configs, launcher, backups, game records, cache, settings, localization and diagnostics. It is now the DLSS 5 Manager group in GAMING.

## New

- **Account and license.**
  - One account and one license for every module: 24 hours, 72 hours, 30 days or Lifetime.
  - Activation codes stack; each code works once and is stored on the server only as a verifier.
  - Leases are Ed25519-signed and bound to the PC, with an offline grace.
  - A Lifetime license gets an offline certificate that never needs internet again.
  - The license server is the VPS (see `deploy/`), with an account website, a staff admin website with two-factor sign-in, and an audit log.
- **FPS Booster.**
  - Seven profiles of real Windows settings, each previewed with its current value, recorded before it changes and restorable with one click.
  - Session settings apply while a game runs.
  - Background programs can be lowered or paused.
  - Intelligent game mode.
- **Benchmark comparison** of baseline, boost and DLSS 5 captures (PresentMon), with a verdict only from measured values.
- **Game Hub** on each game's page: favourites, categories, notes, launch options, playtime, and Recently Played.
- **Desktop Studio.**
  - Wallpapers behind the desktop icons: pictures, GIFs, videos, HTML/WebGL in a sandbox. They support playlists, schedules, virtual desktops, pause rules, a frame limit, audio-reactive and interactive web wallpapers, and one crash-isolated process per monitor.
  - The AKENO Dock.
  - 19 widgets.
  - The Theme Workshop (`.akenotheme`).
- **Community.** DLSS 5 presets, FPS Booster profiles and widget layouts as data-only `.akenopack` packages, and a reviewed index with SHA-256 checks, a block list and reporting.
- **Cloud Sync.** Your AKENO data between your PCs through a folder you choose, with per-field merge, conflicts you decide, a quota and optional encryption. Never licenses, tokens, DLLs or game files.
- **Updates.**
  - A signed release manifest from the public release repository; no GitHub account needed.
  - Every file is SHA-256-checked.
  - A separate updater process keeps the previous version and rolls back if the new one does not start.
  - Stable and Beta channels.
- **Notifications** in categories you can switch off.

## Security

- **Endpoint:** built programs accept licenses only from the license server they were built with, over HTTPS without redirects.
- **Keys:** the app trusts only the public keys built into it, plus keys those keys endorse.
- **Validation:** every validation is bound to a fresh nonce, the issuer, the audience, the entitlement, the account and the PC.
- **Forged and replayed answers:** fake servers, forged and replayed answers change nothing (`docs/SECURITY_LICENSING.md`).
- **Credentials:** no passwords, private keys, database credentials or GitHub tokens are in the Windows program.

## Upgrading from 2.1

- **Install over 2.1:** the installer keeps the same application id and upgrades in place. The portable package can also be unpacked over it.
- **What stays:** user data in `%LOCALAPPDATA%\AKENO` (settings, game records, install records, backups of game files) is kept, and mods AKENO installed stay recognised and restorable.
- **The license gate:** 3.0 asks for a sign-in and a license on the first start. Until then the suite stays locked, but nothing is removed or changed in your games.

## Not measured here

- **Performance:** no FPS gain is claimed. FPS Booster and Benchmark measure on your PC.
- **Windows desktop behaviour:** placing wallpapers behind the icons, the audio meter, acrylic and the virtual desktop id are exercised on the Windows CI runner (`tools/desktop_check.py`), not on a physical gaming PC. See `docs/DESKTOP_STUDIO.md`.
