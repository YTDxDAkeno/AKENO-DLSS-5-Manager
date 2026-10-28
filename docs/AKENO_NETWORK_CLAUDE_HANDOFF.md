# AKENO Network / modular installer — handoff after the Claude continuation

Read this first, then `AKENO_NETWORK_PROGRESS.md` (evidence with CI links, newest first) and
`AKENO_NETWORK_SECURITY_REVIEW.md` (controls and open risks). `AKENO_NETWORK_CODEX_HANDOFF.md` is the older Codex
state and is superseded where they differ.

## Where the work is

- Branch `claude/inspiring-gauss-i8vjdr`, started from Codex checkpoint `833fec0` of `feature/akeno-network-modular`.
  Resolve the newest commit with `git log -1` and compare `git ls-remote origin refs/heads/claude/inspiring-gauss-i8vjdr`.
- `main` contains only the initial README. Do not reset to it. No merge, tag, release, deployment or key change has
  been made; those are owner decisions.
- GitHub Actions: in this environment the GitHub MCP tools read runs and job logs; raw `api.github.com` and the
  artifact blob host are blocked by the proxy, so read evidence from job logs.

## What exists now (and how it was proven)

| Area | Code | Real evidence |
|---|---|---|
| Modular installer | `packaging/installer.iss`, `akeno/components`, `akeno/updater` (Codex) | `components-windows.yml`: real Inno compile + 47 Windows checks (Core → Full → Core, signed module install/remove/repair, damaged module, failed-health rollback, uninstall keeps data) |
| Windows test suite | — | `build.yml` Windows unit/integration/UI tests green again (they failed on every Codex commit because the runner's `python.exe` is Authenticode-signed; fixed in the tests only) |
| Own VPN on Windows | `akeno/network/windows.py`, `tunnels.py`, `helper.py`, `profiles.py` | `network-windows.yml` job `wireguard`: 40 checks with the official WireGuard 1.1.1 / WireGuardNT (split + full tunnel, leak probes, route/DNS restore, recovery). Found and fixed the START_PENDING race. |
| LAN coordinator (server) | `server/akeno_server/network/coordinator.py`, `wireguard.py`, `service.py`, `api.py` | 10 unit tests with real Noise handshakes; API test with a real UDP handshake; opt-in (`AKENO_NETWORK_COORDINATOR_BIND`) |
| LAN client | `akeno/network/lan.py` (verified peers, PathController, LanControl), `lan_windows.py` (WireGuardNT backend, firewall, latency probe, session, recovery) | `lan-lab.yml`: 35/35 (now 37 with the OpenTTD check) real NAT checks with Linux kernel WireGuard — direct hole punching, relay fallback, outage, network change, revocation, lease; `network-windows.yml` job `lan-play`: 26 Windows checks incl. SMB/RDP/NetBIOS blocked, game allow, relay, revocation, crash recovery |
| Real game | OpenTTD 13.4 dedicated server in the NAT lab | LAN discovery + game info over AKENO LAN, P2P and relay |
| VPN Gate | `akeno/network/vpngate.py` (+ `reachable()`), `tools/vpngate_report.py` | `vpngate-catalog.yml` read-only review: real catalog (US 1, TR 1 server, both unreachable from the runner), logging/disclosure policy. Connections stay disabled. |
| UI | `akeno/ui/pages/akeno_network.py`, `akeno/ui/components.py`, German in `akeno/data/i18n_de.json` | `tests/test_network_ui.py`; fixture screenshots in `screenshots/network-fixtures/` (synthetic data, labelled) |
| Regression | — | local: client 84 files, 1,291 passed / 19 skipped / 0 failed; server 173 passed |

## How LAN Play works (short)

1. Rooms, invitations, bans and 45 s leases: `RoomStore` + authenticated API (Codex), unchanged semantics.
2. Each member's WireGuard (WireGuardNT) has the coordinator as one peer (`10.203.0.1/32`, keepalive 25 s). That
   handshake proves key possession; its source is the member's NAT-mapped WireGuard endpoint.
3. The heartbeat returns one signed ≤45 s credential per other member, including its proven endpoint.
4. `PathController` adds each member as a `/32` peer: direct first, relay (the coordinator's same UDP port) after
   12 s without a handshake, re-attempts on silence (60 s) or a stale handshake; every attempt re-adds the peer so
   only a fresh handshake counts.
5. Windows: one elevated helper process per session (UAC once) runs `LanSession`; the GUI only writes a stop flag
   or an "allow game" request and reads a DPAPI status file.

## Run the checks yourself

```bash
python -m pytest -q tests/test_network_lan.py tests/test_network_tunnels.py tests/test_network_ui.py tests/test_vpngate.py
(cd server && python -m pytest -q tests/test_network_coordinator.py tests/test_network_api.py)
sudo python tools/network_e2e/nat_lab.py          # Linux, root, iproute2, iptables, conntrack, wireguard-tools (+ openttd)
python tools/network_screenshots.py screenshots/network-fixtures
```

On a Windows machine with WireGuard installed (elevated): `python tools/network_windows_e2e.py --work <dir> --full-tunnel`
and `python tools/lan_windows_e2e.py --work <dir>` — both change the machine's tunnels/firewall temporarily; use a
disposable machine.

## Blockers and open work (nothing here is done)

1. **Two physical Windows PCs behind real ISP NATs** — not available to the agent. The NAT lab and the Windows
   loopback runs are real packet evidence but not ISP routers. Use the tester plan in
   `AKENO_NETWORK_PRIVATE_TEST_RELEASE.md`.
2. **Staging deployment of the coordinator** — not done (no deployment authorised). Use
   `deploy/docker-compose.lan-play.yml` on a staging server only.
3. **Elevated helper launched from the real GUI via UAC** — the session code ran elevated in CI, but the
   ShellExecute/UAC path of the built app is untested; a privileged service with authenticated IPC would be the
   long-term design.
4. **Broadcast/multicast LAN discovery** — not implemented; games that only find servers by broadcast need a
   bounded, room-scoped relay of selected ports. Games that connect by IP work.
5. **Standby/resume, Wi-Fi changes, exit IP with a real server, physical-adapter capture** — untested.
6. **Norton** — no detection name or quarantine log has been supplied. Core-only avoids installing Security Center
   files; it cannot help if the installer or `AKENO.exe` itself is flagged. Code signing is the real lever.
7. **Public VPN servers** — VPN Gate stays disabled (non-commercial project, ≥3-month connection logs, packet-header
   logs, disclosure policy, no OpenVPN transport). USA/Turkey are listed with one server each and were unreachable
   from the runner. No paid provider was contacted or ordered.
8. **Production readiness of the relay** — capacity and per-account abuse limits, monitoring, and a separate
   coordinator process if the API ever runs more than one worker.

## Rules that still apply

Push small commits at least every five minutes of work; never claim an unconfirmed push; no secrets; no production
deployment, release, merge or key change; no antivirus bypass; do not call anything done without evidence.
