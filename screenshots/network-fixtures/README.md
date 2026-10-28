# AKENO Network — fixture screenshots

Generated with `python tools/network_screenshots.py screenshots/network-fixtures` (Linux, offscreen Qt, the real
themed main window). **The members, paths, pings and diagnostics shown are synthetic fixture data**, not a real
connection. Because these were rendered outside the installed Windows application, the session buttons are disabled
and the page says so.

Real connection evidence (WireGuardNT on Windows, NAT traversal, relay fallback, firewall policy) is recorded with
CI links in `docs/AKENO_NETWORK_PROGRESS.md`.

| File | Content |
|---|---|
| `akeno-network-empty-{en,de}-fixture.png` | No room yet: create or join |
| `akeno-network-session-{en,de}-fixture.png` | Active LAN session: two members, both via the relay (relay-only), IP privacy "Protected: relay only" |
| `akeno-network-vpn-{en,de}-fixture.png` | VPN tab: the 8 target countries, none available (the real state today: no AKENO exit runs) |
| `akeno-network-vpn-connected-{en,de}-fixture.png` | One-click AKENO VPN with a **fixture** catalog (one German exit) and a **fixture** verified session using documentation addresses `203.0.113.24` / `2001:db8::24`; no real exit exists |
| `akeno-network-diagnostics-{en,de}-fixture.png` | Diagnostics results (fixture rows) |

Regenerated for 3.2.0 (redesign): dashboard, tabs, steps, owner view.
Regenerated on the Claude continuation branch: one-click AKENO VPN card, relay-only privacy row, browser filters.
