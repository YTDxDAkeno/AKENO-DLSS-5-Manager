# AKENO VPN — providers, locations and how a location becomes connectable

Status 2026-10-28. **No AKENO VPN server runs today, and no paid service was ordered or contacted.** The only
connectable locations in the app are the user's own imported WireGuard profiles.

## Rule in code (`akeno/network/providers.py`)

- A location is connectable only if a provider in `VERIFIED` is enabled **and** supplies a real server for it.
- `own-wireguard` (user-imported profile): enabled. The user owns the server or has a contract with its provider.
- `akeno` (AKENO-operated WireGuard exits): enabled as a provider, but `akeno/data/vpn_locations.json` lists **no
  servers**, so all 8 target countries (TR, US, DE, NL, GB, FR, JP, CA) show "No AKENO VPN server runs in this
  country yet." A catalog entry alone is not enough: the server must also deliver a signed profile (not built yet).
- `vpngate`: disabled (logging ≥ 3 months, packet-header logs ≥ 2 weeks, disclosure to authorities, academic
  non-commercial project, OpenVPN transport not integrated). The catalog can be loaded read-only for review.
- Any other provider id is disabled ("has not been reviewed by AKENO"). Adding a provider = code change + a review
  entry in this document.

## Integration models compared

| Model | Countries | Cost | API/SDK | Usage rights | Privacy | Protocol | Effort | Verdict |
|---|---|---|---|---|---|---|---|---|
| User imports own WireGuard profile | any the user has | user's own | none needed | user's own contract | AKENO stores DPAPI-encrypted, keys never leave the PC | WireGuard | done | **Shipped** |
| Own WireGuard exits on rented VPS (one per country) | DE, NL, GB, FR, JP, CA, US widely available; TR via local providers (e.g. is\*hosting Premier DC Istanbul, Gcore Istanbul — to be confirmed on their pricing pages) | roughly 4–10 € per server per month plus traffic (provider list prices, not ordered) | provider REST APIs for provisioning; AKENO would issue per-device WireGuard peers signed by the license server | AKENO is the operator: needs ToS check of each host for VPN exit use, abuse handling, imprint/privacy policy | AKENO controls logging (can be no-logs), but becomes responsible for abuse complaints | WireGuard | high: provisioning, key distribution, revocation, monitoring, abuse desk | **Recommended path**, needs owner approval and budget |
| Oracle Cloud Always Free VM as exit | only the tenancy's home region (one country) | free tier, ~10 TB/month outbound per Oracle's pricing page | OCI API | free tier is per account — not a basis for a public multi-user service | Oracle as host | WireGuard | medium | Only for the owner's private testing |
| White-label / reseller VPN (e.g. Windscribe white label, Defguard partners, BillingServ reseller) | provider's network | contract (BillingServ from ~£150/month; others on request) | Windscribe offers API access for own apps; others panel-only | only under a signed contract | provider's logging policy applies and must be shown to users | WireGuard/OpenVPN | medium | Possible later; needs contract + review |
| Consumer VPN accounts (Mullvad, Proton, …) | many | per user | no reseller API | personal use only | provider policy | WireGuard | — | **Not allowed** to embed; users may import their own profiles from them |
| VPN Gate | volunteer servers | free | public CSV | academic, non-commercial; logs kept and disclosed | weak | OpenVPN/L2TP/SSTP | — | **Disabled** |

Sources checked during this work: Oracle networking pricing (https://www.oracle.com/cloud/networking/pricing),
Windscribe white label (https://windscribe.com/white-label-vpn), Defguard partners (https://defguard.net/partners),
BillingServ reseller (https://www.billingserv.com/blog/expand-your-offerings-introducing-our-whitelabel-vpn-service-for-resellers),
is\*hosting Turkey VPS (https://ishosting.com/en/vps/tr), Gcore Istanbul
(https://gcore.com/news/looking-for-a-virtual-server-in-turkey-order-it-in-istanbul/). Prices and terms must be
re-confirmed on the providers' own pages before any order; nothing here is a commitment.

## What an AKENO exit server needs before a country turns connectable

1. Owner approval and budget; host ToS allows VPN exit traffic; privacy policy and abuse contact published.
2. Server hardened (WireGuard only, no logs of destinations), monitored, with per-account bandwidth limits.
3. License-server endpoint that issues a per-device WireGuard peer (device-bound, revocable, signed response).
4. Client: provider `akeno` fetches the signed profile, stores it with DPAPI, then uses the existing
   `TunnelManager` (route/DNS restore, leak probes — 40 real Windows checks in `network-windows.yml`).
5. Real tests per country: handshake, exit IP in that country, IPv4/IPv6/DNS leak probes, reconnect.
6. Only then add the server to `akeno_servers` in `akeno/data/vpn_locations.json` (shipped inside the signed
   release), and the location becomes connectable.

## Security status of the VPN client (what is and is not proven)

Proven in CI (`network-windows.yml`, WireGuard 1.1.1/WireGuardNT): split and full tunnel, leak probes, route and DNS
restore, recovery after an interrupted tunnel. Not proven / not implemented: kill switch (none — documented in the
UI), standby/resume, Wi-Fi changes, real third-party exit servers, IPv6-only networks. The VPN never changes routes
at program start; it connects only on an explicit click and UAC.
