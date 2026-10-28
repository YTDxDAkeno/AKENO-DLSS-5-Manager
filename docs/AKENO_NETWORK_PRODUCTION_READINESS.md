# AKENO Network production gates

Status 2026-10-28: **development implementation, not approved for production or a public VPN service**.
The current cloud continuation is `codex/akeno-network-cloud-20261028`, based on
`4190496ee7e470927e6ee905c6c635cd4f62b3b6`. See the handoff for verified evidence and exact commands.

## Locally verified

- Existing account/license/device authorization and opt-in API startup: full backend suite, 260 passed.
- LAN client/browser/signing/bootstrap/session policies and offscreen Qt/i18n: 206 passed, 2 native checks skipped.
- Actual relay transport: 27 checks, three communicating wireguard-go clients, fourth-room denial,
  OpenTTD UDP discovery query/TCP game information, revocation and four outer PCAPs.
- Actual managed exit: 23 checks, native handshake, dual-stack encrypted datagrams, NAT/NAT66,
  private-destination drop counters, revocation, frozen/crashed-agent kernel expiry and recovery.
- Local loopback API/database/signer/web readiness and authentication gates passed before and after restart.
- The full application constructs its pages in Linux offscreen smoke mode. This does not test Windows features.

The packet labs use disposable internal Docker networks, local test keys and synthetic control grants/manifests.
Globally shaped lab IPv4 addresses are aliases inside an internal network; they are **not public country exits**.
No unit, UI or lab result proves that a country VPN works on a customer's Windows PC.

## Required implementation and Windows gates

- [ ] Implement and genuinely validate the native Windows DNS leak observer. Current `default_probe` deliberately
  returns `dns_verified=False`; managed VPN returns `verification_required` and cleans up, even if a lease and
  handshake exist. Do not replace this with a constant success flag.
- [ ] Validate install consent, MSI signature/revocation, actual ACLs, UAC accept/decline, alternate administrator,
  manager DPAPI import, key protection, real tunnel start/stop and crash cleanup in the installed GUI.
- [ ] Validate outer relay firewall enforcement and narrow per-game TCP/UDP rules on Windows, including
  preexisting broad firewall rules, wrong endpoint/port, IPv6, SMB/RDP denial and cleanup failure.
- [ ] Finish and validate Windows broadcast/multicast capture. `discovery.AVAILABLE=False`; explicit OpenTTD
  UDP queries are not evidence of automatic Windows LAN discovery. Do not enable the UI claim prematurely.
- [ ] Verify network change, standby/resume, reboot, safe reconnect/backoff and routing/DNS restoration.
  Managed VPN currently stops on a lost handshake; this is not seamless automatic reconnection.
- [ ] Complete existing game-library integration/automatic executable selection and validate each advertised game.
  Only OpenTTD has a real game-protocol lab check here; Factorio/Terraria sessions are unverified.
- [ ] Run the current Windows build/installer/update/rollback regression and record actual CI run/artifact links.
- [ ] Test two physical Windows PCs behind different authorized ISP/NAT connections with captures and real games.
- [ ] Validate simultaneous LAN/VPN routing, DNS, MTU and restore before allowing it. Current mutual exclusion
  is the intentional safe behavior permitted by the requested specification.

## External infrastructure and operating gates

- [ ] Owner-approved dedicated control/relay staging and public endpoint ACLs, bandwidth budget, duration,
  capacity/abuse monitoring and rollback. No automatic activation of the existing VPS staging is authorized.
- [ ] Confirm provider VPN exit usage rights, current location/cost, bandwidth/overage, IPv6 and abuse terms.
  Official provider requests in this cloud instance returned proxy CONNECT 403; older estimates are not new quotes.
- [ ] Provision authorized real country exits and fresh independent dual-stack IP/country proofs. No TR, US,
  DE, NL, GB, FR, JP or CA exit was supplied or provisioned by this task.
- [ ] Supply a lawfully obtained, checksum-pinned, fresh country database and separate independent probe key.
  No provider contract, paid account, raw credential or private key belongs in Git or chat.
- [ ] Review per-account bandwidth/capacity, moderation queue operations, report retention, access control,
  privacy/abuse policy, backups and load limits under real staging traffic. Global/source limits alone do not
  establish production per-account fairness or adequate capacity.
- [ ] Authorize production activation separately. Keep modules disabled by default. Preserve release Ed25519,
  SHA-256, signed envelopes, anti-replay, rollback and offline release-key handling.

VPN Gate remains disabled. No purchase, public listener, production firewall/DNS/Caddy change, merge, tag or
release was performed. Cloud environment draft saving is separate from publication and fresh-task restoration.
