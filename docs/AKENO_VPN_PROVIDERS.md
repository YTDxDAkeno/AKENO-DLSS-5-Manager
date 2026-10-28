# AKENO VPN provider and country preparation

Research checked 2026-10-28. No country exit is running or advertised, no account was
created, no order placed and no host publicly activated by this task. The normal VPN
flow uses the managed catalog and per-device signed leases, without profile import.
An empty/failed health or country gate means the location remains unavailable.

## Candidates requiring a specific owner-approved offer

| Candidate | Intended review | Verified here | Still required before selection |
|---|---|---|---|
| Hetzner Cloud | USA and European exits | Official location SDK exposes country/city and location listing; MIT SDK license | Current US/European region/dual-stack capacity, commercial shared-VPN permission, quote, bandwidth/overage and abuse terms |
| Gcore | Turkey (Istanbul candidate), USA and other countries | Official Python cloud REST SDK and Apache-2.0 SDK license | Current Istanbul availability, exact location/IPv6, VPN-exit permission, quote and abuse terms |
| is*hosting | Turkey candidate | Official Turkey offer URL identified, page retrieval blocked | Entire offer, commercial VPN rights, real location, dual-stack, costs and contract |
| Vultr | USA and additional regions | Official SDK lists country/city/region and per-region plan availability | Current region selection, dual-stack, explicit shared-VPN rights, quote/traffic and abuse terms |
| Contracted white-label provider, e.g. Windscribe | Managed multi-country service alternative | Official white-label URL identified, retrieval blocked | Written integration/resale contract, supported device/API flow, privacy terms and current quote |

These are candidates, **not approved or legally cleared hosts**. SDK copyright licenses
permit code use under their conditions; they do not grant hosting/resale rights or prove
VPN traffic is permitted. Obtain the host's written applicable terms for the intended
commercial shared exit before ordering. Consumer subscription credentials and arbitrary
free/public relays are not infrastructure for this integration. VPN Gate remains disabled.

Official provider web requests returned cloud proxy CONNECT 403, including Hetzner,
Gcore, is*hosting and Windscribe. No price or current server stock was verified. Earlier
rough prices and provider-rights assertions are intentionally removed. Git HTTPS and
raw GitHub documentation were accessible over normal verified TLS.

## Country coverage matrix (web search 2026-10-28, Claude continuation)

Search results only; the official pages themselves could not be fetched from this environment (host not in the
session's network allowlist). Nothing here is a quote, stock check, legal clearance or approval.

| Country | Candidate locations found | Evidence quality |
|---|---|---|
| Germany | Hetzner Falkenstein / Nuremberg ([Hetzner docs](https://docs.hetzner.com/cloud/general/locations/)); Vultr Frankfurt | official docs (search snippet) |
| USA | Hetzner Ashburn (us-east) and Hillsboro (us-west) ([Hetzner docs](https://docs.hetzner.com/cloud/general/locations/)); Vultr US regions | official docs (search snippet); a Feb 2026 review states 1 TB included traffic outside Europe — verify before use |
| Canada | Vultr Toronto ([Vultr regions](https://www.vultr.com/features/datacenter-regions/), [Vultr blog](https://blogs.vultr.com/Deploy-Cloud-Servers-in-Toronto-Canada)) | official pages (search snippet) |
| Japan | Vultr Tokyo ([Vultr regions](https://www.vultr.com/features/datacenter-regions/)) | official page (search snippet) |
| Netherlands, United Kingdom, France | Vultr Amsterdam / London / Paris (listed on the Vultr regions page per earlier SDK review) | SDK + search; re-check on the official page |
| Turkey | No major cloud found with Istanbul in these results (Vultr: none). Smaller Istanbul VPS hosts advertise IPv4+IPv6 (e.g. [WORLDBUS](https://worldbus.ge/cloud/), [OperaVPS](https://operavps.com/?p=20351), [Colonel Server](https://colonelserver.com/nl/vps-turkey)); earlier candidates Gcore Istanbul and is*hosting | vendor marketing only; VPN-exit rights, real location, IPv6, abuse handling and contract must be verified |

Hetzner's docs list four network zones / six locations (fsn1, nbg1, hel1, ash, hil, sin); product availability
differs per location. A shared VPN exit for other users needs the host's explicit permission in its terms.

## Reviewed official implementation sources

Sources were fetched from these exact upstream commits; local copies/checksums are in
`/workspace/akeno-cloud/research/source-manifest.json` (outside Git).

- [Hetzner location API](https://github.com/hetznercloud/hcloud-go/blob/9adc140755042b41d7a1d865d8c1afe94535b5d6/hcloud/location.go)
  and [MIT license](https://github.com/hetznercloud/hcloud-go/blob/9adc140755042b41d7a1d865d8c1afe94535b5d6/LICENSE).
- [Vultr region and plan availability](https://github.com/vultr/govultr/blob/dc6446c10c55fbbf8fd7b9e8fcde9e676958c057/region.go).
- [Gcore official Python SDK](https://github.com/G-Core/gcore-python/blob/81eb3de72f78ba983fabbe8b42d5279925983860/README.md)
  and [Apache-2.0 license](https://github.com/G-Core/gcore-python/blob/81eb3de72f78ba983fabbe8b42d5279925983860/LICENSE).
- [Official WireGuard Windows enterprise integration](https://github.com/WireGuard/wireguard-windows/blob/6ece77bc487c8aa697e3c092197621c4f3e5ccb8/docs/enterprise.md)
  and [Windows project license](https://github.com/WireGuard/wireguard-windows/blob/6ece77bc487c8aa697e3c092197621c4f3e5ccb8/COPYING).
  The installed binary's actual Authenticode chain is independently checked; source
  licensing does not validate a downloaded driver or replace its component notices.

Official offer/terms URLs to review once access is available:
[Hetzner locations](https://docs.hetzner.com/cloud/general/locations/),
[Hetzner cloud terms](https://www.hetzner.com/legal/cloud-server/),
[Gcore Istanbul](https://gcore.com/news/looking-for-a-virtual-server-in-turkey-order-it-in-istanbul/),
[is*hosting Turkey](https://ishosting.com/en/vps/tr),
[Windscribe white label](https://windscribe.com/white-label-vpn).

## Implemented provisioning and registration path

The host-specific ordering step is deliberately separated from AKENO registration.
No application startup, country click or CI job creates a provider VM or charges an
account. A provider API adapter can consume an approved offer later; credentials are
operator-only and must never enter the customer client.

1. Approve a precise host/country offer and prepare a new dedicated dual-stack Linux
   VM. Record provider, region ID, physical-location evidence, quote, traffic budget,
   terms, abuse contact, approved public UDP ACL and rollback owner.
2. Use `deploy/vpn-exit/` with an explicit reviewed image digest and separate private
   runtime state. Its default binding is loopback-only; public binding needs separate
   authorization. Never attach the production licensing database or host network.
3. The existing operator-only registration API registers public identity, unique
   IPv4/IPv6 pools, exact endpoint, DNS and capacity. It returns a one-time agent token;
   write it directly to an owner-only file. The agent never receives the operator key.
4. The actual WireGuard/nftables agent acknowledges generations and enforces short
   device leases. Kernel timeout fences stop forwarding even when the agent freezes.
5. A separate operator probe peer measures dual-stack egress through the real tunnel,
   checks a fresh licensed, checksum-pinned country database and signs a one-use proof
   with a separate pinned key. A requested flag or provider marketing location is
   insufficient. Both IP families must agree with the chosen country.
6. Only recent independent country proof, healthy authenticated agent and free capacity
   expose the server. The client chooses a country, receives its device-bound signed
   configuration, installs after consent, verifies handshake, routes, real DNS behavior
   and source-bound HTTPS exit IPs, and only then displays Connected.
7. Disable withdraws the catalog entry and revokes leases; the agent removes peers.
   Stop the dedicated compose project to remove its owned namespace and forwarding.

Exact API fields, commands, trust separation, deployment profile, independent probe and
rollback are in [the deployment guide](AKENO_VPN_SERVER_DEPLOYMENT.md). Registration,
agent and probe APIs are implemented and tested with isolated infrastructure. Native
Windows validation and actual provider/country activation remain separate gates in
[production readiness](AKENO_NETWORK_PRODUCTION_READINESS.md).

Required owner approval covers new accounts, payment, recurring/traffic cost, provider
terms, any public ACL/UDP binding and production changes. Nothing in this document
supplies that approval. No real countries are enabled by documentation or lab results.
