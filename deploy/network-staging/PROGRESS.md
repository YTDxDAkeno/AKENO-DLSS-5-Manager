# AKENO Network: VPS staging preparation

Base: `588b21ca64a78f13d937a89abb58d5289b34f7c5` (`claude/inspiring-gauss-i8vjdr`).

Preparation only. No staging deployment, firewall/DNS/Caddy change, release, production database write, or production key copy is authorised by this checkpoint.

The existing LAN override must not be applied to the production compose project. An independent project with an isolated license database, room database, test-only signing keys, bounded coordinator/relay and loopback-only listeners is being prepared here. Public activation remains gated on the owner's explicit approval.

The coordinator uses UDP 3478; it is not an anonymous STUN/TURN or Internet exit service. Current host capacity and implementation security constraints are under review. This checkpoint is not activation approval or a claim that staging is running.
