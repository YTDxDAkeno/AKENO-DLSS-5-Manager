# Authenticated LAN room browser

The browser router is part of the regular API only when `AKENO_NETWORK_ENABLED=true`. Every request needs the
existing AKENO bearer session, a verified account, a running suite license and the active device attached to that
exact session. No staging account or extra browser account is required. The browser does not activate a tunnel.

Base path: `/api/v1/network/lan/browser`. The existing private-room endpoints remain compatible.

| Method and path | Request | Result |
|---|---|---|
| `GET /rooms` | `device_id`, optional `search`, `game_id`, `region`, `minimum_players`, `open_only`, `limit`, `offset` query parameters | `rooms`, `offset`, `limit`, nullable `next_offset` |
| `POST /rooms` | Device ID, WireGuard public key, name, game ID, region, optional password, capacity, visibility, locked | Existing create response including the owner's invitation and virtual subnet |
| `PATCH /rooms/{room}` | Device ID, visibility, game ID, region, capacity, locked | Owner-only publication, privacy, capacity or lock change |
| `POST /rooms/{room}/join` | Device ID, WireGuard public key, optional password | Room ID and `transport_policy: relay_only`; stores membership using the same existing room store |
| `POST /rooms/{room}/reports` | Device ID, category (`spam`, `harassment`, `unsafe_content`, `other`) | HTTP 202; never returns moderation data |

The list returns only: room ID, room name, game ID, user-declared region, live player count, capacity,
password-required flag, `open`/`full`/`locked` status, public visibility, `relay_only` policy and nullable latency.
Latency remains null until actually measured. A user-declared room region does not verify a physical player or
relay location. No public peer endpoint, invite, account/device identity, key or e-mail is included in listing rows.
Text entered by users in names is user content and must be displayed as plain text.

Search is literal, case-insensitive and limited to 64 characters; SQL wildcard syntax has no special meaning.
Page size is 1–50, offset 0–1000, minimum players 0–16. Private rooms are never listed, even when a caller knows
their ID. Only owners may publish, lock or change visibility; publication requires a reviewed game and region.
An owner may have at most one public room and three rooms total. Capacity is 2–16 and cannot be reduced below
existing membership. Concurrent joins enforce the capacity in the same SQLite write transaction. Public joins
require a live owner lease, obey the password, capacity, lock and bans, and cannot replace another device's key.

Browser listing prunes expired rooms and omits rooms whose owner has not renewed its 45-second lease. Member
leases protect the data plane independently. A full room may contain offline members retaining their membership;
the displayed live-player count can therefore be below its capacity while status is `full`.

Existing endpoints continue to provide invitation rotation, member removal with ban, leave and owner deletion.
Publication never enables P2P or shares an endpoint: all rooms use the relay-only data-plane policy. Relay and
Windows tunnel behavior require separate tests; a successful browser join alone proves only membership.

Reports retain only room/reporter identifiers, category and timestamp, not IP addresses or free-form content.
Duplicate reports for a room by the same account are idempotent. Limits are ten reports/account/day and 1,000
queued reports globally. Entries older than seven days are removed during report processing. Operators must
process the bounded queue through controlled internal operations; no unauthenticated moderation endpoint exists.
No automatic account punishment is based solely on reports.

Reviewed desktop profiles live in `akeno/network/games.py`: OpenTTD TCP/UDP 3979 (existing Linux relay-lab evidence),
Factorio UDP 34197 and Terraria TCP 7777 (untested game sessions). Profiles never admit SMB, RDP or Windows
discovery-service ports. Windows broadcast discovery remains unverified and is never advertised as available.

Validation: `server/tests/test_network_browser.py` exercises real ASGI requests, privacy, device checks,
publication, password, bans, private-room denial, quotas, bounded filtering, reports, schema migration and
concurrent joins. `tests/test_network_browser.py` checks desktop request contracts and the narrow game profiles.
These tests are not evidence of a Windows connection, public Internet relay, live country exit or two real PCs.
