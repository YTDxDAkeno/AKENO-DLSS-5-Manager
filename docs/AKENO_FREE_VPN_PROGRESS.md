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

### Official OpenVPN for Windows (CI, run 40809247326 and later)

| Fact | Value |
|---|---|
| Current community release | **OpenVPN 2.7.8**, `OpenVPN-2.7.8-I002-amd64.msi`, 5 922 816 bytes |
| SHA-256 (same on swupdate.openvpn.org and .net) | `33104dd02c48cba172612882b1af6276422b1b4340d4c9c84dd2afb4b356fe9f` |
| Authenticode signer of the MSI and `openvpn.exe` | `CN=OpenVPN Inc., O=OpenVPN Inc., L=Pleasanton, S=California, C=US` |
| Library | OpenSSL 3.6.5; ovpn-dco is the default driver in 2.7, Wintun was removed; with VPN Gate's CBC cipher OpenVPN uses TAP-Windows6 |
| MSI features | OpenVPN, OpenVPN.Service, OpenVPN.GUI(.OnLogon), PLAP, Documentation, SampleCfg, Drivers(.TAPWindows6, .OvpnDco), OpenSSL, EasyRSA. A default install also starts `OpenVPNService` and `OpenVPNServiceInteractive` automatically — AKENO installs only `OpenVPN,Drivers,Drivers.TAPWindows6` |

## Phase 2–4 — real connections (exploration runs, AKENO-generated configurations)

| Run | Finding | Consequence |
|---|---|---|
| 1 | every attempt ended after 1 s: OpenVPN reads backslashes in a `log "C:\..."` line as escapes | paths are fixed command-line arguments, never in the configuration |
| 2 | TLS 1.3 handshake OK; server certificate `CN=opengw.net` (Let's Encrypt YR2 → Root YR → ISRG Root X1) on every server; `remote-cert-tls server` fails (EKU) | pin `verify-x509-name opengw.net name`; no `remote-cert-tls` |
| 2–5 | some servers answer `AUTH_FAILED` after the handshake (with and without the published `vpn`/`vpn` credentials alike) | treated as "server refused", next candidate; no credentials sent |
| 3, 5, 6 | tunnel up and traffic through Japan, but no "Initialization Sequence Completed" | cause: `mute 20` also suppresses that line (run 4 without mute: completed after 7–9 s) → `mute` removed |
| 4 | **first verified connections**: 219.100.37.85 → exit **219.100.37.238**, 219.100.37.201 → exit **219.100.37.245**; ipinfo, country.is and ifconfig.co all **JP**; DNS on `AKENO-FreeVPN` = 1.1.1.1, 9.9.9.9; OpenVPN WFP "block-outside-dns" filters active | the exit IP is not the server IP (university NAT pool) → country verified by independent services, never assumed from the server address |
| 4 | geolocation services disagree for some addresses (runner: ifconfig.co "GB", others "US") | "connected" needs ≥ 2 confirmations and more confirmations than contradictions |
| 6 | the VPN Gate list itself timed out once (30 s read timeout) | one quiet retry, then an honest "list not reachable" |

## Log

- `openvpn_profile.py`: untrusted profile → validated data → AKENO's own configuration (48 tests).
- `vpngate.py`: `VPNGateProvider` (HTTPS, no redirects, bounded, retry, 10-min TTL, 60-s rate limit, profile-free
  cache, quality ranking, country-only candidates, UDP preferred, unstable/no-bandwidth servers out).
- `openvpn_windows.py`: consented install (pinned SHA-256 + online Authenticode), own TAP adapter, protected work
  folder (Administrators/SYSTEM, high integrity), `--service` exit event, kill-on-close job.
- `free_vpn.py`: session with failover, verification (adapter, routes, exit IP ≠ normal, ≥ 2 geo confirmations,
  IPv6 blocked, native DNS measurement), watch, switch, recover (15 tests).
- `helper.py`: `free_vpn_session`, `free_vpn_recover`, `install_vpn_component`; one VPN at a time, LAN exclusion.
- `network_free_vpn.py`: Free VPN card first in the VPN tab, DE/EN, consent notice, favourites, search.
- `tools/free_vpn_windows_e2e.py` + `.github/workflows/free-vpn-windows.yml`: real end-to-end on Windows.
