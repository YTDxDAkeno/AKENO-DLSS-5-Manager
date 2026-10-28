# AKENO Network: VPS staging preparation

Base: `588b21ca64a78f13d937a89abb58d5289b34f7c5` (`claude/inspiring-gauss-i8vjdr`).

Preparation only. No staging deployment, firewall/DNS/Caddy change, release, production database write, or production key copy is authorised by this checkpoint.

The existing LAN override must not be applied to the production compose project. An independent project with an isolated license database, room database, test-only signing keys, bounded coordinator/relay and loopback-only listeners is being prepared here. Public activation remains gated on the owner's explicit approval.

The coordinator uses UDP 3478; it is not an anonymous STUN/TURN or Internet exit service. Current host capacity and implementation security constraints are under review. This checkpoint is not activation approval or a claim that staging is running.


## Checkpoint: isolation and controls

- Standalone project `akeno-network-staging`; own PostgreSQL and room SQLite volumes; no production mounts or networks.
- Default HTTP and UDP binds are loopback-only. Public UDP requires a separate override and explicit IPv4.
- New staging-only secret and synthetic regular-user provisioning helpers are prepared, not executed.
- Resource caps, no host networking/TUN/NET_ADMIN, restricted admin listener, file mail and one-worker coordinator.
- Compose security assertions pass. Caddy fragment parses without changing the running proxy.
- Exact Claude source: 110 fetched files match their Git blob hashes. 26 isolated network backend tests passed.
- Last confirmed remote checkpoint before this update: `63fbe4fc76c28e68cda9938600a2142eb874b0db`.
- Staging is NOT deployed; firewall, DNS, existing Caddy config and production services remain untouched.

## Checkpoint: operational handover

- Private Windows build must embed the staging API and fresh staging license/LAN public keys; packaged clients ignore runtime address overrides.
- Existing release public key stays unchanged; no production private key or SMTP secret enters staging.
- Added manual activation, source-IP/global UDP budget review, monitoring and rollback procedures.
- Additional Internet VPN exits remain a separate future architecture; no server purchased/started or third-party VPN connected.
- Both real-Windows-PC/ISP-NAT test and actual staging deployment remain pending explicit approval.

## Final verification

- All 7 pre-existing container IDs/images/start times/restart counters unchanged.
- All 19 production database tables have identical aggregate contents before/after.
- Production .env/Compose/Caddy/backup-script hashes and all checked firewall rules unchanged.
- Production HTTPS/API health and readiness remain HTTP 200; release public key unchanged.
- No staging containers/networks/volumes/runtime secrets created; no staging DNS/proxy activation.
- Pinned PostgreSQL to the locally verified official digest; app image build/boot remains approval-gated.
- All 7 existing workflows reviewed; branch pushes run CI, no VPS deployment path was invoked.
- Last confirmed remote checkpoint before this final update: `71a69ded0cd2049cc2ffcd91b10efcb90bade358`.

## Nachfolgende lokale Freigabe umgesetzt

Staging läuft ausschließlich auf Loopback. 48/48 Live-Prüfungen bestanden, vier eigene Container gesund; keine öffentliche Freigabe. Aktuelle öffentlichen Testkeys, Betrieb und Windows-Anforderungen: `AKENO_NETWORK_LOCAL_SMOKE_REPORT.md` / `CLAUDE_LOCAL_STAGING_HANDOFF.md`. Historische Angaben über noch nicht erzeugte Staging-Keys sind damit überholt.
