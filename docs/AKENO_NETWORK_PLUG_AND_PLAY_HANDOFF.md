# AKENO Network technical handoff

Status: 2026-10-28. **Unfinished development; Claude still has backend/native verification work in addition to UI.**
Do not describe this as the full masterprompt's definition of done.

## Source identity and continuation

Work here: `codex/akeno-network-cloud-20261028`. Base: existing development remote
`4190496ee7e470927e6ee905c6c635cd4f62b3b6` on `codex/akeno-network-plug-and-play`.
Original RC/staging remote tips were independently verified as `9eead86f4265cb1621a62e708ec892ce69502f4a`
and `325d35cbad90820a89015825d69a3f50815c9510`; required staging files were already in the RC.
The newer development branch was discovered on the first non-fast-forward push; no force push or remote
history replacement was used. The continuation retains that implementation and fixes its real exit lab.

Last verified checkpoint before this document: `c5600d76c43fb5565399b193edda3689cb43f83d`.
Resolve the exact current branch tip with `git ls-remote origin refs/heads/codex/akeno-network-cloud-20261028`
and compare `git rev-parse HEAD`; a document cannot embed the hash of its own containing commit.
Small WIP checkpoints were scanned for private-key/token patterns, pushed and read back. No PR or release exists
from this continuation. [Branch](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/tree/codex/akeno-network-cloud-20261028).

## Implemented interfaces inherited from the development branch

- Private LAN rooms: authenticated create/join, invitations/passwords, owner removal/ban, leave/delete,
  device binding, virtual addresses and short leases.
- Public rooms/browser: bounded authenticated search/pagination, reviewed game/region filters, publication,
  lock/capacity, password join, report quotas/retention and owner permissions.
  Full request/response contract: `AKENO_LAN_BROWSER_API.md` and the opt-in API's generated OpenAPI.
- Peer privacy: signed relay-only tickets contain the controlled relay, not a peer's real endpoint. Network
  credentials use a separate Ed25519 key delegated by the account trust anchor. Release keys are untouched.
  Path control and Windows outer UDP firewall are relay-only; no automatic P2P fallback is permitted.
- Managed VPN API: `/api/v1/network/vpn/catalog`, device-bound `/leases`, poll/renew/delete, separate
  operator server registration/probe/verify/disable and authenticated exit-agent peers/heartbeat.
  `akeno/network/vpn.py` strictly verifies the signed lease and keeps the device private key local.
- Exit inventory/control: health/capacity, unique address pools, one-use independent country proofs,
  generation acknowledgment, expiry/revocation and operator-only bootstrap probe leases.
- Dedicated exit agent: strict fixed-argument WireGuard peer reconciliation, owned-state journal, nft timeout
  fences, private-destination/service deny rules, IPv4 NAT and IPv6 NAT66. Deployment and manual rollback:
  `AKENO_VPN_SERVER_DEPLOYMENT.md`; it is a template, not an activated real server.
- Regular AKENO Network UI includes browser and automatic country controls alongside expert profile import.
  Module opt-in remains off. Windows bootstrap is a separate explicit consent/UAC action with official MSI,
  Authenticode/revocation and protected staging; see `AKENO_NETWORK_WINDOWS_COMPONENTS.md`.
- Native managed session: protected journal/profile identity, fresh lease/handshake, independent egress gate,
  renewal, cleanup/recovery and LAN/VPN mutual exclusion; see `AKENO_VPN_MANAGED_SESSION.md`.

The relay operator necessarily observes transport source addresses. These controls protect peer-to-peer
disclosure of AKENO-mediated LAN traffic; they do not promise anonymity from AKENO/ISP or from a game's own
external services. Actual public ISP/Windows captures remain required.

## Changes and real evidence in this cloud continuation

The inherited managed-exit lab failed actual IPv4 forwarding. Investigation found multiple fixture defects:
iproute2 serializes /32 routes as bare addresses, causing accidental deletion of the outer endpoint route;
Docker internal bridges drop IPv4 destinations outside their declared pool; wildcard UDP replies can use the
wrong source address; delayed replies can be misattributed to the next random test payload. The corrected lab
compares networks, asserts outer/inner routes, confines target aliases to a randomized internal test pool,
binds listeners explicitly and correlates exact payloads. It retains public native diagnostics on failure,
never `wg dump/showconf` or private keys. Destination-drop counters were added to the actual exit firewall so
negative tests prove the firewall dropped real packets rather than merely observing an unreachable service.

| Check | Current-instance result |
|---|---|
| Full backend, `cd server; /workspace/akeno-env/bin/python -m pytest -q` | 260 passed, 3 third-party deprecation warnings |
| Client network + i18n | 206 passed, 2 native checks skipped |
| Actual relay/OpenTTD/PCAP lab | 27 passed |
| Actual managed-exit dual-stack/NAT/freeze/crash/recovery lab | 23 passed |
| Isolated API readiness/auth/web | Six HTTP assertions passed before and after process restart |
| Offscreen app smoke | 3.2.0 starts; 19/19 assets and all pages construct |
| Copied-interpreter performance tests | Initial environment failure diagnosed; all 12 pass with scoped PYTHONHOME |
| Full client regression | Still running when this draft was written; consult final progress entry before claiming a pass |

Evidence in this machine: `/tmp/akeno-server-tests.log`, `/tmp/akeno-final-network-tests.log`,
`/tmp/akeno-relay-report.json`, `/tmp/akeno-relay-pcaps/`, `/tmp/akeno-vpn-exit-correlated-report.json`,
`/tmp/akeno-vpn-correlated-lab.log`, `/tmp/akeno-performance-env-fixed.log`, `/tmp/akeno-app-local-smoke.log`.
Reports/PCAPs describe only artificial local networks. They do not contain country exit evidence.
The latest CI outcomes could not be read: GitHub API returned proxy CONNECT 403. Workflow triggers now include
the continuation branch; [Actions](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions) is a navigation
link, not a claim that any current run passed. Historical RC runs are recorded in the older readiness report.

## Cloud development workflow

Use the existing checkout; each task is already isolated, so do not create a worktree unless explicitly asked.
Python 3.12 dependencies are installed in `/workspace/akeno-env`. Generated helpers are outside the repository
in `/workspace/akeno-cloud`. The saved environment `install_script` recreates dependencies, loopback API helper
and official-package Docker test image; `start_skill` explains process startup and checks. These fields were
saved as a draft, not published or tested by restoration into another task.

`/workspace/akeno-cloud/test-client.sh -q` runs client tests with offscreen Qt and a scoped `PYTHONHOME` pointing
to the cloud runtime's relocated standard library. Without it, tests that copy the interpreter launch a broken
sleeper process (`encodings` missing). Do not set that Python 3.12 home globally for the Debian Python 3.13 tool.

For local API startup:

```bash
/workspace/akeno-env/bin/python /workspace/akeno-cloud/local_server.py init
/workspace/akeno-env/bin/python /workspace/akeno-cloud/local_server.py serve
# In another execution session:
/workspace/akeno-env/bin/python /workspace/akeno-cloud/local_server.py check
```

The helper clears inherited AKENO configuration and binds only HTTP `127.0.0.1:18510` and UDP `127.0.0.1:3478`.
Separate owner-only synthetic development secrets, signer and SQLite files live under its runtime directory;
mail is file-only. No account is automatically licensed, no real exit is registered, and no production secrets
are imported. A running PID does not survive publication/new-task startup; restart via the saved instructions.
Stop only the process you launched. Do not run global Docker cleanup or production deployment scripts.

## Remaining work and activation gates

No real country server was provided or deployed. TR/US/DE/NL/GB/FR/JP/CA remain unavailable. Official provider,
WireGuard download/docs website and GitHub API requests returned CONNECT 403. Domain additions were saved for
review; no new pricing, terms, availability, signing-chain or installer result is inferred from the draft.
No paid order or new account was created. Use the approved operator deployment guide only after the owner
authorizes the particular host, country, cost, public ACL and operating budget.

The default managed VPN DNS observer is unfinished and intentionally fails closed. Windows discovery capture,
native install/UAC/DPAPI/firewall evidence, seamless reconnect, real ISP/NAT tests, broader game compatibility,
operations/moderation/capacity review and Windows installer/update regression also remain. These are technical
and operational gates, not UI polish. The detailed checklist is `AKENO_NETWORK_PRODUCTION_READINESS.md`.

For Claude: retain relay-only IP privacy and separate signing purposes; never introduce a direct fallback,
constant-success verification flag, fabricated country or permissive game rule to make UI appear complete.
Complete native/backend gates first, then UI/translation polish and actual release QA. Keep the normal updater
from installing/activating tunnels without consent. Offline release keys and existing user profiles remain
protected. Public activation, production DNS/firewall/Caddy changes, paid resources and release publication
still require the owner's express authorization.
