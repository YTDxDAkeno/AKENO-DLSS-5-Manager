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
