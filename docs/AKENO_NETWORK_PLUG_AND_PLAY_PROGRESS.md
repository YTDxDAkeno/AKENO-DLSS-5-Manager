# AKENO Network plug-and-play — live progress

This is unfinished development. No production deployment, public listener, purchase, merge, tag or release is authorized.

## Technical completion continuation

Remote tip rechecked before work: `d926ac0a78d14642fd3051639eb3b4c3f8736be3`.
The requested Windows run 40724746824 actually tested `4190496`, not the current cloud tip.
Its public job pages confirm the LAN failure; GitHub API requests (including existing GH_TOKEN
authentication) are still proxy-denied. Public detail-log links return 404. No credential values
were printed. The public job/annotation pages are usable for subsequent CI diagnosis.

First change: purpose-neutral native Windows diagnostics preserve cmdlet, typed error ID and
HRESULT, never raw native output or script arguments. The old catch-all message concealed the
failing operation, including possible cleanup failures. Firewall policy is unchanged at this
checkpoint. Windows reexecution must identify the actual failing cmdlet before a root-cause claim.

### Windows cause identified; native completion in progress

Native run https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40740995137
on `5cacd30` confirms the exact LAN cause: Windows rejects **`::/0`** in NetSecurity
RemoteAddress with HRESULT `0x80070057`. Per-CIDR isolated block-rule diagnostics
identified it; no accept rule was introduced. The replacement is `::/1` plus
`8000::/1`, mathematically the same complete IPv6 set. Native revalidation follows.

DNS verification now measures valid nonce/question-bound DNS responses plus explicit
Windows blocked send/connect results and rechecks NIC/configuration changes. Initial
native tests deliberately failed when evidence was insufficient; diagnostic expansion
continues. No constant DNS success flag, fake country or unverified live server was added.
Bounded managed-session reconnect is implemented with cleanup, fresh signed configuration,
reverification, cancellation during backoff and expired-lease handling after resume.
Focused reconnect/session/DNS/firewall tests: 37 passed with labelled native stand-ins.

### Verification continuation after `6f852fb`

- HTTPS exit measurements now explicitly bind to the signed VPN source address and
  native interface in both families, with default TLS trust and no redirects.
- Native Packet Monitor memory mode omits its filename from status, making ownership
  unprovable. A bounded circular file in the protected directory preserves that gate.
- The LAN attack fixture also had a race: legitimate relay packets overwrite
  session.addr while the attack loop runs, causing some test attack payloads to be
  sent through the permitted relay. Each adversarial datagram now pins its actual
  destination independently. No production firewall rule or assertion is relaxed.
- Remote isolated traffic run 40746348448 and NAT run 40746349019 succeeded on
  `5d1203022ccef8cb49f0bb2018cf969c88611a67` (public job pages verified).
- Local reruns on that source: 27 actual relay checks and 23 actual exit checks pass.
  They do not prove public country exits, Windows or provider usage rights.
- Corrected-environment full client run is being evaluated; the final run after all
  source changes must remain separately identified. Native Windows work continues.

### Technical continuation checkpoint after `4530381`

- `::/0` rejection is corrected with the exact same IPv6 set in two /1 rules.
- A second native LAN startup failure was the parser rejecting its own generated
  ListenPort; strict numeric validation now accepts only 1..65535. Real WireGuardNT
  handshake, relay and encrypted echo then pass before the next assertion.
- The next LAN assertion exposed the loopback fixture's unsuitable firewall boundary.
  A private Windows-container peer now exercises a separate stack; all authenticated
  direct-roaming, SMB/RDP and game-port assertions remain enabled. New fixture
  pending native execution, not a passing test claim.
- DNS observer now requires actual nonce-bound responses plus native denial or
  packet-drop evidence. Packet Monitor integration is pending Windows validation;
  run 40744749103 on `4530381` fails closed on its existing-filter precondition.
- Per-account and per-room coordinator quotas supplement source/global quotas.
  Backend: 262 passed at `15416d9`.
- Complete corrected-environment client run at `15416d9`: 1472 passed, 19 skipped,
  one real SIGSTOP status race (not either old missing-encodings failure). Linux
  suspend now waits for stop acknowledgment. All 12 performance tests pass after
  correction; complete final rerun still required.
- Reconnect has three bounded attempts, fresh leases and verification on each;
  expired leases after resume trigger cleanup/reprovisioning. Recovery failures
  remain terminal. No live country exit is registered or advertised.

## Cloud continuation — 2026-10-28

Active continuation branch: `codex/akeno-network-cloud-20261028`, based on the existing
remote checkpoint `4190496ee7e470927e6ee905c6c635cd4f62b3b6`. That branch was discovered
before replacing any remote history; the original development branch is preserved.
The sections below describe inherited work and must not be read as new runtime evidence.

Current cloud evidence:

- Python 3.12 virtual environment `/workspace/akeno-env`; pinned client/dev/server dependencies installed.
- Server suite: **260 passed**, three dependency deprecation warnings.
- Final client network and i18n suite: **211 passed, 2 skipped** (native Windows checks).
- Actual relay-only lab: **27 passed**, including three communicating WireGuard clients,
  OpenTTD UDP/TCP, cross-room refusal, revocation and four outer packet captures.
- Local API initialized with separate development-only keys/database; loopback HTTP and UDP only.
  Six readiness/authentication/web requests passed. No production credentials or data reused.
- Original full client regression: **1449 passed, 19 skipped, 2 environment failures**, 27 min 12 s.
  Both failures concern copied Python binaries missing the relocated standard library. After the scoped
  PYTHONHOME correction, their full affected files passed: **12 performance tests**, **108 updater-v2 tests,
  1 skipped**. The final five added library tests are included in the separate 211-test network run.
- Managed exit lab initially failed. Fixture routing, internal-bridge addressing, UDP source and delayed
  reply defects were corrected. **23 real packet/native/kernel checks pass**. Production forwarding verdicts
  remain unchanged; private-destination drop counters were added to verify negative tests meaningfully.
- Native Windows DNS leak verification is still deliberately incomplete in `default_probe`;
  managed VPN disconnects with `verification_required`. This is a repository release blocker,
  not a dependency setup failure or working country VPN.
- Current proxy denies official provider pages, WireGuard download/docs website and GitHub API
  with CONNECT 403. Git HTTPS access works. No prices, live CI results or country exits are inferred.

The tested installation/startup instructions and exact checkout are saved in the cloud draft. Publication and
restoration into a fresh task have not been verified. Local helpers/evidence remain outside the checkout in
`/workspace/akeno-cloud`; they do not change product configuration. Next work is the documented native Windows
implementation/validation and authorized external infrastructure gates, not an automatic production deployment.

### Cloud validation checkpoint

Last verified remote before this update: `c5600d76c43fb5565399b193edda3689cb43f83d`.
The managed-exit lab now passes **23 real packet/native/kernel checks**, including dual-stack
NAT, negative private-destination drop counters, revocation, SIGSTOP, SIGKILL, independent
kernel expiry and journal recovery. Its internal bridge/addressing and delayed-echo fixture
defects were corrected without weakening forwarding policy. Diagnostics retain only public
native state and artificial packet summaries. See the handoff for the exact defects.

The local API was stopped/restarted and all six checks passed again. Dependency installation
was repeated successfully; `pip check` reports no broken requirements. Scoped offscreen GUI
smoke passes, with the local source endpoint and a separate AKENO_HOME. Network/i18n was
rerun through the saved client test wrapper: **206 passed, 2 skipped**.

The full client suite found a copied-interpreter setup failure in the performance test:
the cloud Python lives under `/opt/codex/...`, whereas a copied binary looks under `/install`
and exits without `encodings`. `/workspace/akeno-cloud/test-client.sh` supplies the correct
`PYTHONHOME` only to this Python 3.12 test process; **all 12 performance tests then passed**.
No application assertions or tests were weakened. The original full run continues; report
its final counts separately from this targeted successful rerun.

`install_script` and `start_skill` were saved as a draft with six exact custom network
additions (`api.github.com`, `download.wireguard.com`, `www.wireguard.com`, `windscribe.com`,
`www.oracle.com`, `gcore.com`). This did not publish or change active proxy policy.
Official WireGuard enterprise/embedding/license text was retrieved through normal verified
TLS from its raw GitHub repository. Provider prices and current CI runs remain unverified.
Four network/component workflows now include the continuation branch; YAML parsing passed.

The new handoff and production gate list explicitly retain native DNS observer, Windows
discovery/driver/UAC/DPAPI, reconnect/standby, actual country exits, per-account operations
review and two physical ISP-separated PCs as outstanding. No completed product claim.

### Existing game-library selection

Last verified remote before this update: `099983ee5cd82f34bac7c5f29c7888479bb3fb99`.
The LAN game action now reuses recorded nested executable paths/candidates from the existing
game library instead of requiring both an exact display title and a root-level executable.
For example, Factorio (Steam) at `bin/x64/factorio.exe` is selected automatically after the
user selects its reviewed port profile. No recursive scan, game launch or extra port opening
occurs. Canonical containment, reviewed executable basenames, existing regular files and
the elevated helper's independent validation remain mandatory. A symlink outside the recorded
installation is rejected. Manual executable selection remains the fallback.

The targeted library, UI and translation group passes **34 tests**, including a UI assertion
that nested library selection avoids the file picker and submits only the reviewed profile.
This verifies selection policy/wiring, not an actual Windows game session.

### Final cloud validation record

Last verified remote before this entry: `87e96a7c4b7959835d8fbbcdc5eef69507507eaf`.
All 1470 originally collected client tests completed: **1449 passed, 19 skipped, 2 failed**.
The failures are `test_background_programs_lowered_and_paused_then_restored` and
`test_other_akeno_processes_from_the_program_folder_are_ended`; captured stderr proves the
same missing `encodings` issue in copied relocated Python binaries. The corrected wrapper's
entire affected files pass (12 performance; 108 updater v2, 1 skipped). Full network/i18n
including the new library integration passes 211, with 2 native skips. No test, assertion,
dependency declaration or lockfile was disabled/rewritten to resolve the environment issue.

The original full-run XML/log are preserved unchanged. This is a completed original run plus
successful affected-suite reruns, **not a second completely green full run**. No unexplained
required Linux check remains. Remaining product limitations are in the handoff/readiness list.
Runtime files/processes survived the reported reconnect; this does not establish a published
snapshot or fresh-task restoration. The draft contains installation, startup, exact repository
commit/path and six reviewed domain additions; user review/save/publication is still required.

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
