# AKENO Gaming Suite 3.2.0 — AKENO Network (release candidate, not published)

**New: AKENO Network** (ONLINE → AKENO Network), two modules that are **off until you turn them on**; updates
never switch them on, never install a network adapter or WireGuard, and each module works without the other.

- **LAN Play — private rooms**: create a room (optional password), invite with a 10-minute code, see members,
  remove members, leave or delete the room, copy your virtual IP, measured ping per member, pick your game from
  your AKENO library (nested paths such as `bin/x64/factorio.exe` are found; manual file choice only as fallback);
  only the reviewed ports of that game are opened, and only for room members.
- **LAN Play — public server browser**: search, filter by game and region, minimum players, password-protected
  rooms, join, publish your room, make it private, lock it, report a room. Listings never contain IP addresses.
- **IP privacy (relay-only)**: every room — private and public — runs through the encrypted AKENO relay. Other
  players see only virtual LAN addresses, never your home IP. On Windows an **AKENO-owned Windows Filtering Platform
  lock** lets the LAN tunnel talk only to the relay; it is checked before the room shows "Connected", re-checked while
  it runs, survives a crash until recovery, and is removed only after the tunnel is gone. The relay operator still
  sees transport addresses; this is not anonymity.
- **AKENO VPN — one click**: choose a country tile, click Connect, Windows asks once. AKENO creates the keys and the
  tunnel itself and shows Connected only after the observed IPv4/IPv6 exit, routes and DNS were verified. **No
  AKENO exit server is running yet**, so Turkey, USA, Germany, Netherlands, United Kingdom, France, Japan and Canada
  are shown with that reason and cannot be selected. Your own WireGuard profiles remain under "Expert".
- **Diagnostics**: DNS, AKENO server, LAN service, relay, sign-in, license, WireGuard, firewall, sessions and VPN
  routing; export without passwords, tokens or keys.
- **Network components**: missing WireGuard components are only installed after you agree; the official installer
  is verified (Authenticode with revocation check) before Windows asks for administrator rights.
- Not yet: automatic LAN game discovery (broadcast) is not forwarded on Windows — connect by the host's virtual IP.
  LAN Play and the VPN cannot run at the same time.

**Fixed**
- "Not Found" at the bottom left: server answers are translated into sentences everywhere.
- A rare crash when a System page was closed while it was still measuring (progress reported to a closed page).

**Components**: Settings → Components lists Core, Security Center (optional, removable — e.g. when Norton blocks
it), LAN Play and VPN (on/off). Security Center can still be left out in the installer. LAN Play and VPN are parts of
Core that you switch on; they are not separate downloads.

**Updating**: from 3.0.0, 3.1.0 and 3.1.1 via ACCOUNT → Updates or the installer; license, account, settings,
desktop, components and data stay.

---

# AKENO Gaming Suite 3.1.1 — critical start-up fix

**Fixed**
- 3.1.0 no longer started on a PC where System Doctor → Hardware Health had saved a report and Windows reports no
  CPU temperature (the normal case without a vendor driver): `TypeError: unhashable type: 'dict'` in
  `HardwareTab._show`. The saved note is a translatable message stored as JSON; it is decoded again before it is
  translated. Saved reports of 3.1.0 open unchanged.
- Translating a stored message never raises any more, whatever was saved.

**Protection against start crashes**
- A saved result of the System tools that cannot be shown is logged, stays stored, and the tab says so; it never
  stops the start. A System page that cannot be built is replaced by a notice; games, mods, restores and updates
  keep working.
- Start guard: if a start does not get its window up, the next start runs in **safe mode** (saved System results
  are not shown, nothing is deleted) and opens ACCOUNT → Updates with the update check and the rollback. A crash
  while starting is written to `logs\crash.log` and explained in a message box. Start menu: "AKENO Gaming Suite
  (Safe mode)", or `AKENO.exe --safe-mode`.
- The update's start check also builds the main window with this PC's saved data; an update that cannot show it
  is rolled back automatically.

**Updating**
- From 3.0.0, and from a 3.1.0 that starts: ACCOUNT → Updates → install 3.1.1.
- From a 3.1.0 that no longer starts: download the 3.1.1 installer from akeno.online and run it. It installs over
  3.1.0 without administrator rights. License, settings, games, mods, backups, saved System results and undo
  records stay. Do not delete `system.sqlite3` or `%LOCALAPPDATA%\AKENO`.

---

# AKENO Gaming Suite 3.1.0

A new **SYSTEM** section between GAMING and DESKTOP with four modules, and a new mod library. Everything of 3.0.0
stays: DLSS 5 Manager, FPS Booster, Benchmark, Desktop Studio, Community, Cloud Sync, account, license and updates.
AKENO measures before it claims anything, changes nothing without your confirmation and keeps every result on this
PC.

## New

- **System Doctor.** Health dashboard (processor, memory, drives, graphics; temperatures only from real sensors),
  an optional health indicator that shows every deduction, Slow PC Detective (what is measured, what is a possible
  cause, what only happened at the same time), Startup Analyzer (disable and re-enable like Task Manager, with undo),
  Crash Detective (event log, crash dumps, stop codes), Hardware Health (SMART/NVMe health of every drive) and a
  guided repair with Windows' own DISM and SFC, only when they are needed.
- **Smart Cleaner.** Junk categories with the reason, the risk and what you may notice afterwards; a preview of the
  exact files before anything happens; the Recycle Bin by default; links, junctions, files in use and Windows'
  own folders are never touched; browser caches only, never cookies, sign-ins or profiles; shader caches never
  preselected; disk space analyser with duplicates; Smart Maintenance (off by default, never removes anything).
- **Network Lab.** Connection dashboard; ping and stability to the router and three internet targets with loss,
  jitter and spikes (a server that ignores ping is not an outage); bufferbloat test with your consent and a
  data limit; bandwidth per adapter and the programs that are online; network gaming mode with a measurement
  before and after; DNS benchmark (your DNS is changed only when you choose it, with a restore); connection
  repair. No registry or TCP "tweaks". Your public IP address is looked up only when you ask.
- **Universal Mod Scanner.** Installed Mods becomes the mod library for every game: mods installed by AKENO and by
  you, Minecraft Java instances (official launcher, Prism/MultiMC, CurseForge, Modrinth, ATLauncher; Fabric,
  Quilt, Forge, NeoForge), FiveM, BepInEx, MelonLoader, UE4SS, ASI loaders, ReShade, OptiScaler. Conflicts are
  reported only when the mods' own data shows them. Mods AKENO did not install are shown read-only; AKENO's
  installations keep their one-click restore.
- **Security Center.** The protection status Windows Security reports (antivirus, firewall, definitions, Tamper
  Protection), Microsoft Defender's own quick, full and folder scans, the threat history, a security check for mods,
  downloads and tools (type, SHA-256, vendor, signature, origin, programs inside archives and Defender's verdict),
  and a review of running programs that warns only with a concrete reason. AKENO is not an antivirus: it never
  switches protection off, adds no exclusions, deletes nothing and respects another antivirus.
- **Download protection.** Components AKENO downloads are checked by Microsoft Defender after their SHA-256 check;
  a detection stops the installation.
- **Restore game files without a license.** The welcome screen can put back the original game files, FPS Booster
  settings and paused programs also when no license is active. It unlocks nothing else.
- **Export report** in System Doctor, Network Lab and Security Center, without file paths, addresses, names or
  serial numbers.

## Fixed since the 3.1 preview

- Hardware Health showed an empty drive table on Windows: the drive query was refused by Windows PowerShell 5.1.
  The same fix applies to the memory/processor counters in System Doctor and to gateway and DNS in Network Lab. When
  Windows Storage does not answer, the drives are still listed with their SMART status.

## Updating from 3.0.0

ACCOUNT → Updates offers 3.1.0. The update keeps your license, settings, games, mod backups, themes and wallpapers;
the new version checks itself after the switch and the previous version comes back automatically if it does not
start correctly.

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
- **Updates (AKENO – Updates).**
  - From the AKENO Release Manager on akeno.online (release schema 2): Ed25519-signed release information with
    a sequence number and an expiry date; a replayed, older or expired release is refused.
  - The portable package must match its signed SHA-256 and size and is unpacked only within its signed limits.
  - Nothing is installed without your confirmation. Downloads run in the background and can be cancelled.
  - A separate updater process switches the program folder, checks that the new version really works (its own
    start check, not only a started process), keeps the previous version and rolls back otherwise. An update
    interrupted by a power cut or a restart is finished at the next sign-in or start.
  - Your license, settings, games, themes and wallpapers are never touched; the uninstaller keeps working.
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
