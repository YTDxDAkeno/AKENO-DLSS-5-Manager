# AKENO LAN Play — game compatibility (only recorded tests count)

| Game | How it connects | Over AKENO LAN | Evidence | Status |
|---|---|---|---|---|
| OpenTTD 13.4 | Direct IP (discovery query to the host's virtual IP, then TCP) | Discovery answer and game info received over direct P2P and over the relay | `lan-lab.yml` run 9 (Linux kernel WireGuard, NAT lab, dedicated server) — see `AKENO_NETWORK_PROGRESS.md` | **Tested (Linux NAT lab)**; not yet on two Windows PCs |
| OpenTTD (server list by broadcast) | UDP broadcast 3979 | needs discovery forwarding | — | Not released |
| Minecraft Java "Open to LAN" | Multicast 224.0.2.60:4445 announcement; join by IP works | needs discovery forwarding for the list; direct IP expected to work | none | **Untested** |
| Factorio | Broadcast 34197 for LAN list; direct IP | — | none | **Untested** |
| Terraria | Direct IP | — | none | **Untested** |

Rules:
- "Tested" needs a recorded run (CI link or signed-off two-PC test report). A profile in
  `akeno/network/discovery.py` stays `tested=False` until then.
- Games that connect by IP should work in general (routed /24 per room); this is an expectation, not a claim.
- Broadcast/multicast discovery: the room-scoped forwarder core exists (`akeno/network/discovery.py`, 13 tests incl.
  real UDP delivery and unicast answers). Windows capture of the game's broadcast is **not implemented/verified**,
  so `AVAILABLE = False` and the UI says that broadcast search is not forwarded.

Two-PC test plan per game: `AKENO_NETWORK_PRIVATE_TEST_RELEASE.md` → add one row here with date, versions, path
(direct/relay), result.
