# AKENO free VPN (VPN Gate over OpenVPN) — running progress

Branch `claude/akeno-free-vpn`, started from `claude/confident-goodall-i10aop` @ `ba50de4` (last green code commit
`5cc77fd`, build [40775941347](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40775941347)).
Nothing is published, signed or deployed; no paid server, no production change.

## Environment limits

- This cloud session cannot reach `www.vpngate.net` (environment network policy). All VPN Gate evidence comes from
  GitHub-hosted runners (`.github/workflows/free-vpn-explore.yml`, later `free-vpn-windows.yml`).

## Phase 1 — research (evidence from CI)

Catalog run [40809247326](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40809247326) (job `catalog`,
29.10.2026 02:56 UTC), official `https://www.vpngate.net/api/iphone/`:

| Fact | Value |
|---|---|
| Servers in the API answer | 99 |
| Countries | JP 43, KR 30, RU 9, US 7, TH 3, RO, TW, GB, MX, ID, MA, NZ 1 each — **no TR, DE, NL, FR, CA** at that moment |
| Transport | 87 TCP, 12 UDP; every profile `dev tun`, `cipher AES-128-CBC`, `data-ciphers` present, `auth SHA1` |
| Directives in all profiles | dev, proto, remote, cipher, data-ciphers, auth, resolv-retry, nobind, persist-key, persist-tun, client, verb |
| Inline blocks | `<ca>`, `<cert>`, `<key>` in all; no tls-auth/tls-crypt, no auth-user-pass |
| CA | **ISRG Root X1** (Let's Encrypt root) in all 99 — authenticates *a* publicly trusted certificate, not *this* server; AKENO must also pin the server name |
| Client certificate | the same `CN=VPNGateClientCert` (RSA 2048, SHA-1 self-signed, until 2037) in every profile: a published shared value, not a user secret |
| Logging policy column | `2weeks` for all |
| AKENO sanitizer | accepted 99/99 (87 tcp, 12 udp) |

Official statements (VPN Gate pages, read by the runner):
- “Instead you can use CSV List to make your own VPN Gate client app.” (vpngate.net/en/)
- “Using the VPN Server List of VPN Gate Service as the IP Blocking List of your country's Censorship Firewall is
  prohibited by us.”
- Operator field of the university servers: “Daiyuu Nobori_ Japan. Academic Use Only.”
- FAQ: academic research project of the University of Tsukuba; “Japanese national universities have a general rule
  to prohibits any profit activities.” VPN Gate will not become a paying service.
- Anti-abuse policy (earlier run 40611878680): VPN connection logs kept **three or more months** (central log
  servers of SoftEther Corporation); every volunteer server records **packet headers** for every user, kept **two or
  more weeks**; logs are disclosed to police, prosecutors, lawyers or courts authorised by law.

Open legal point: AKENO is a licensed (paid) product. Whether a commercial product may integrate VPN Gate — even as a
free, unpaid feature — is not answered by these pages (“Academic Use Only” on operator entries). Publication needs
the owner's decision, ideally written permission from the VPN Gate project. See the final report.

## Log
