# AKENO Network plug-and-play — live progress

This is unfinished development. No production deployment, public listener, purchase, merge, tag or release is authorized.

## Cloud continuation — 2026-10-28

Active continuation branch: `codex/akeno-network-cloud-20261028`, based on the existing
remote checkpoint `4190496ee7e470927e6ee905c6c635cd4f62b3b6`. That branch was discovered
before replacing any remote history; the original development branch is preserved.
The sections below describe inherited work and must not be read as new runtime evidence.

Current cloud evidence:

- Python 3.12 virtual environment `/workspace/akeno-env`; pinned client/dev/server dependencies installed.
- Server suite: **260 passed**, three dependency deprecation warnings.
- Client network and i18n suite: **206 passed, 2 skipped** (native Windows checks).
- Actual relay-only lab: **27 passed**, including three communicating WireGuard clients,
  OpenTTD UDP/TCP, cross-room refusal, revocation and four outer packet captures.
- Local API initialized with separate development-only keys/database; loopback HTTP and UDP only.
  Six readiness/authentication/web requests passed. No production credentials or data reused.
- Full client regression is running; no final result yet.
- Managed exit lab initially failed. Native diagnostics proved its fixture deleted the outer
  endpoint /32 route because iproute2 serializes it without `/32`. Correct network comparison,
  explicit route assertions, destination-bound echo listeners and private-listener controls
  are under real packet validation. Production firewall rules are unchanged.
- Native Windows DNS leak verification is still deliberately incomplete in `default_probe`;
  managed VPN disconnects with `verification_required`. This is a repository release blocker,
  not a dependency setup failure or working country VPN.
- Current proxy denies official provider pages, WireGuard download/docs website and GitHub API
  with CONNECT 403. Git HTTPS access works. No prices, live CI results or country exits are inferred.

Next: finish actual exit validation, full client regression, save tested installation/startup
configuration, publish an honest technical handoff and production gate list. Local helper files
are outside the checkout in `/workspace/akeno-cloud`; they do not change product configuration.

## Resume here

- Branch: `codex/akeno-network-plug-and-play`.
- Verified starting remote: `claude/adoring-cerf-pzowiw` at `9eead86f4265cb1621a62e708ec892ce69502f4a` (3.2.0 RC).
- Staging remote: `325d35cbad90820a89015825d69a3f50815c9510`; required staging templates already imported by the RC. Do not deploy them automatically.
- Prior Codex branch remains untouched at `833fec043d0e43f17b59ce6759d6ce7b183adb2e`.
- Last actual remote checkpoint: resolve `git ls-remote origin refs/heads/codex/akeno-network-plug-and-play` and compare `git rev-parse HEAD`. Commit identifiers cannot include their own resulting hash.
- Checkpoint policy: scan staged changes for secrets, commit small steps (mark incomplete work WIP), push at least every five minutes with changes, verify exact remote SHA. No empty commits.

## Current phase

Phase 1 analysis complete; implementing phases 2–6 in parallel on disjoint files:

1. Relay-only privacy, purpose-separated network signing and native endpoint restrictions.
2. Authenticated public room browser, bounded listings, room publication/moderation and safe game profiles.
3. Automatic VPN control plane, device leases, private-key-local client and controlled exit-agent templates.
4. Explicit-consent, signature-verified official Windows component installation.
5. Real isolated relay capture/game checks and Windows E2E adaptation.

Root owns final integration, user interface, config/main, workflows, checkpoint commits and documentation.

## Evidence and limitations

- The inherited release readiness report records prior Windows CI/build and Linux regression evidence; it is not evidence for new changes.
- Current machine is Linux, no Windows VM or real ISP-separated PCs available.
- Runtime network is restricted; Git read works. GitHub API and provider domains are not currently allowed. A previous editable configuration draft does not establish active access.
- No real AKENO country exits are provisioned. TR, US, DE, NL, GB, FR, JP and CA are unavailable until authorized healthy exits and actual IP/country checks exist.
- VPN Gate remains disabled. No country availability or price is invented.

## Next concrete step

Integrate relay-only tickets and path control, add browser/VPN routers behind opt-in settings, then execute authenticated API and actual encrypted relay checks. Update this file after each verified milestone, preserving failures and unrun checks.

## Checkpoint 1 — implementation in progress

- First checkpoint pushed and verified: `d65def9f039f032acb0589a0c4fa86b18b6dc64e`.
- Relay-only controller/tickets and separate network signer are now being integrated; focused controller tests: 11 passed (native WireGuard stand-in).
- Public room browser/store/client and explicit per-game port profiles are implemented; API tests pending.
- Opt-in VPN settings and browser/VPN router registration added; VPN package under development.
- Windows/component/NAT workflows now include this feature branch. Windows execution of this checkpoint is not verified.
- This WIP checkpoint may contain cross-file integration still under test; continue integration before a release claim.

## Checkpoint 2 — first working slices

- Verified checkpoint before this update: `d02d010b7cfd23144ba4530bbaa81db58acdde63`; current pushed SHA is also written by the task-local watchdog to `/tmp/akeno-checkpoint-state.json`.
- Three-minute task-local watchdog is running; bounded source/doc allowlist, private-key/token scan, nonempty WIP commits, push + exact SHA comparison. Runtime credentials, key files, databases and builds are excluded. Check its state after resumption; live processes may not survive a new machine.
- Public browser: API + legacy RoomStore tests 15 passed; desktop browser 3 passed. Safe listings, password joins, owner publication/lock, expiry, one-public-room quota and bounded reports implemented.
- Explicit Windows bootstrap: 35 policy tests passed with platform stand-ins; actual installation is NOT executed locally. Fixed official MSI source, online Authenticode revocation, protected privileged staging, explicit consent and UAC helper wired. Native CI now uses this bootstrap.
- Existing + new UI/browser/bootstrap focused check: 56 passed (Qt offscreen Linux).
- Actual isolated relay lab: 27/27 checks passed, four wireguard-go clients, three communicating in one room, fourth room denied, real OpenTTD discovery query/game-info TCP, revocation/lease and four outer PCAPs. Captures show only controlled relay destinations and no application plaintext. Explicit UDP game query is not Windows broadcast forwarding, public ISP NAT or two physical PCs.
- VPN operator/agent/device/proof control plane and automatic DPAPI client are implemented, integration tests ongoing. No authorized real country exit exists; no live country is connectable.
- Next: privileged automatic VPN session with real handshake/egress checks, private-key-local provisioning, exact game-port firewall UI, full regression and handoff.

## Checkpoint 3 — integrated client and independent exit expiry

- Last verified remote before this entry: `ca938458363ffe04e0cfb5c37114fbf5b52bbe8b` (watchdog continues pushing nonempty work).
- Browser and automatic country controls are integrated into the regular AKENO Network page. Expert imported profiles remain available. Module opt-in stays off by default; construction performs no API/tunnel operation.
- A real Qt country-selection bug (placeholder left no country selected) was detected and fixed; new UI + existing network UI + DE placeholder coverage: 26 passed.
- Full backend run at this stage: 248 passed, 3 third-party deprecation warnings. New operator probe bootstrap/proof collector changes are newer and need subsequent tests.
- Managed session provisions device-bound signed configuration, uses protected local profiles, journals ownership, renews, stops on loss/revocation, and performs safe recovery. Native/API/egress stand-in coverage: 18 session cases; helper/bootstrap/tunnel/session/client focused union 114 passed. These do not establish actual Windows/UAC.
- Windows process mutex prevents competing helpers; session nonces prevent stale stop flags from stopping a new session. Simultaneous LAN/VPN is refused until routing is proven.
- Exit agent now has kernel nft timed address fences so a frozen/crashed control agent cannot leave indefinite forwarding. Real isolated nft expiry checks passed (3); full real VPN-exit traffic fixture is running.
- Managed VPN must not show Connected merely on a lease/handshake. Default Windows observer currently cannot prove DNS leak protection and fails closed with verification_required; remaining native probe work is explicit, not a success placeholder.
- Public test listeners/exits/deployments remain disabled; provider terms and real TR/US/etc exits are absent.
