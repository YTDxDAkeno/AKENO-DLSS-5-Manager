# AKENO Network / modular installer — private test release guide

For the owner of the repository. Nothing here was executed by the agent: no release, draft, tag, deployment or
production change exists. Every step is a manual decision.

## 0. What a tester gets

- A Windows installer with Full / Core-only / custom selection (Security Center optional), the portable ZIP for
  the in-app updater, and SHA-256 checksums — built by `build.yml` from one exact commit.
- AKENO Network: LAN Play rooms (create / join / invite / remove / delete), LAN sessions over the official WireGuard
  for Windows with direct P2P or relay, member list with path and ping, "allow a game", recovery; own WireGuard VPN
  profiles (import / connect / disconnect / status / exit IP); the VPN Gate catalog view (connections disabled);
  connection diagnostics.
- Not included: public VPN servers, broadcast LAN discovery, a privileged Windows service, any Norton workaround.

## 1. Pick the commit

1. Look at the newest runs on the branch: `components-windows.yml`, `network-windows.yml` (jobs `wireguard` and
   `lan-play`), `lan-lab.yml`, `build.yml`. Use a commit where all four are green on that exact SHA.
2. Read `docs/AKENO_NETWORK_PROGRESS.md` (evidence) and `docs/AKENO_NETWORK_SECURITY_REVIEW.md` (open risks).

## 2. Build the test package (private, not published)

Option A — artifacts of a normal branch run (nothing is published):
- Open the green `build.yml` run of the chosen commit → job "Windows build, tests and smoke test" → artifacts
  (installer, portable ZIP, checksums, release summary). Distribute them privately to testers.

Option B — a draft prerelease in this private repository:
- Actions → "Build and test" → "Run workflow" → branch = the test branch, `draft_release` = true,
  `publish_channel` = **none**. The job "Draft development release from tested Windows artifacts" creates a
  **draft** `v<version>-dev` prerelease containing exactly the tested artifacts. Drafts are not visible to the
  public; publishing it, or choosing `stable`/`beta`, would be a real release — do not do that for a test.

Both builds are signed only with the existing release key through the existing pipeline; no key is changed. The
executable is not Authenticode-signed (unchanged); Windows SmartScreen and security products may warn.

## 3. Test server for LAN Play (staging only)

LAN Play needs the rooms API and the UDP coordinator. Do **not** enable it on the production VPS for this test.

1. Use a separate staging server or a separate compose project with its own database and keys. Generate staging
   license keys as for any staging server (`deploy/README.md`); test builds must trust that staging key, or use
   accounts on a staging license server built for the test (repository variables of the build: license server
   address and public keys).
2. Start with the override:
   `AKENO_LAN_HOST=<public name or IP> docker compose -f docker-compose.yml -f docker-compose.lan-play.yml up -d`
   (it sets `AKENO_NETWORK_ENABLED=true`, the coordinator bind `0.0.0.0:3478`, its key file and a volume).
3. Open **UDP 3478** in the server firewall (WireGuard packets of room members only; nothing else listens there).
4. Check: `POST /api/v1/network/lan/rooms` exists (401 without a session), and the heartbeat of a test room returns
   `"transport": "wireguard"` and a `coordinator` block.

## 4. Tester PCs

Each tester needs Windows 10/11 x64, an AKENO test account with an active (test) license, and:
1. Install the official **WireGuard for Windows** from https://www.wireguard.com/install/ and start it once.
2. Install the AKENO test build (Full, or Core only to check the optional Security Center).
3. Sign in, register the PC.
4. AKENO Network → **Run diagnostics**: every line should be green (sign-in, license, WireGuard signed, manager
   running, Windows Defender Firewall Public profile on).

## 5. Test plan (two PCs on different networks)

| # | Step | Expected |
|---|---|---|
| 1 | PC A: create room; copy the invitation; PC B: join | both appear in Members |
| 2 | Both: **Start LAN session** (one UAC prompt each) | state "Connected"; your virtual IP shown |
| 3 | Members table | the other PC shows "Direct (P2P)" or "Relay", a ping value, a recent handshake |
| 4 | Real game by IP: e.g. OpenTTD (free) — PC A hosts a multiplayer game, PC B "Add server" with A's virtual IP | B joins A's game |
| 5 | Before step 4, PC A **Allow a game…** → the game's .exe | without it, B cannot reach A's game; with it, B can |
| 6 | From PC B try `\\<A's virtual IP>` in Explorer and Remote Desktop to A's virtual IP | both fail (blocked) |
| 7 | PC A (owner): remove PC B | B disappears within ~15 s; B cannot rejoin with the old invitation |
| 8 | Unplug / disable PC A's network for 30 s, reconnect | session recovers by itself |
| 9 | Stop session on both | adapter "AkenoLAN…" gone; `Get-NetFirewallRule -Group 'AKENO LAN Play'` empty |
| 10 | Kill AKENO during a session (Task Manager), start again → **Recover** | adapter and rules removed |
| 11 | Own VPN: import a WireGuard profile from your own server → Connect → Status → Check exit IP → Disconnect | exit IP = your server; routes/DNS back to normal |
| 12 | Standby with a connected VPN or LAN session, resume | record what happens (not tested by the agent) |
| 13 | Settings → Components: remove / install / repair Security Center | restart, state matches, license and data unchanged |

Report per test: pass/fail, both PCs' network type (home router, mobile hotspot, …), the path shown, the AKENO log
(`%LOCALAPPDATA%\AKENO\logs`) and, for WireGuard problems, `"C:\Program Files\WireGuard\wireguard.exe" /dumplog`.

## 6. Norton and other security products

- Ask the affected tester for: product and version, the **exact detection name**, the file path that was flagged
  (installer, `AKENO.exe`, a file under `_internal`, or the Security Center files), and the quarantine/history entry.
- Try the **Core-only** installation: if the detection names a Security Center file, Core-only avoids installing it.
  If Norton blocks the installer or `AKENO.exe` itself, modularity does not help — that needs a false-positive
  submission to the vendor and, ideally, Authenticode code signing of the build.
- Never ask testers to disable protection or add exclusions; AKENO contains no antivirus bypass.

## 7. Before any public release (not part of this test)

- Code signing certificate for installer and executable.
- Production LAN deployment review: capacity, abuse limits, monitoring, separate coordinator process if the API
  ever runs more than one worker.
- Two-PC results from step 5 across several router types; standby/resume; Windows 10 and 11.
- Public VPN providers only after terms/permission, OpenVPN transport review and explicit user consent.
- Merge, tag and publish are separate owner decisions.
