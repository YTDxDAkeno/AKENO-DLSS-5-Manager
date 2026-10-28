# AKENO 3.2.0 final — running progress (Claude continuation)

Read this first when continuing. Newest entries at the top of each section. Nothing here is published, deployed,
signed, merged or released. Production VPS, DNS, Caddy, firewall, release keys and paid resources are untouched.

## Source identity

- Working branch: `claude/confident-goodall-i10aop` (fast-forwarded from `main`'s initial commit, no force push).
- Base: `codex/akeno-network-cloud-20261028` @ `1991686c9c4f4f3d975de35b7da9b8dd26543c54` (newest Codex network
  state). It already contains `claude/adoring-cerf-pzowiw` @ `9eead86` (3.2.0 RC design), the whole
  `codex/akeno-network-plug-and-play` @ `4190496`, `feature/akeno-network-modular` and `hotfix/akeno-3.1.1-doctor-startup`
  (verified with `git rev-list --count`: 0 commits of those branches missing).
- `codex/akeno-network-staging-20261028` @ `325d35c` has 11 commits that only add `deploy/network-staging/**`
  (loopback staging kit and smoke report); not yet merged here.
- Resolve the current tip with `git ls-remote origin refs/heads/claude/confident-goodall-i10aop`; a commit cannot
  contain its own hash.

## CI baseline on the Codex tip (before any Claude change)

| Run | Commit | Result | Root cause found |
|---|---|---|---|
| [Build and test 40748314003](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40748314003) | `1991686` | Windows tests: 1 failed, 2 errors, 1496 passed; Linux: segfault (exit 139) | see below |
| [WireGuard on Windows 40747826167](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40747826167) | `2329fe3` | `lan-play` and `wireguard` failed | see below |

1. `test_installer_verifies_before_fixed_argv_execution_and_cleans_private_download`: **test assumption only.**
   Production calls `str(_system_executable('msiexec.exe'))`, a `GetSystemDirectoryW` path that Windows renders with
   backslashes; the stand-in returned `Path('C:/Windows/System32') / name` and the test compared against a
   forward-slash literal. The assertion now compares against the same `Path` rendered natively; the System32-only,
   no-PATH execution rule is unchanged.
2. `test_unusable_index_never_becomes_an_installer[xxxx…]` (2 errors): pytest exports the test id in
   `PYTEST_CURRENT_TEST`; the oversized-index parameter made the id longer than Windows' 32767-character environment
   limit (`ValueError`). Explicit short ids; the oversized case still runs.
3. Linux exit 139: segfault in `pytestqt._process_events` while `akeno/ui/system_ui.py` worker threads were still
   running (doctor CPU sample, network throughput). Being reproduced.
4. `lan-play`: **real bypass, not a fixture error.** The WireGuard log of that run shows peer 2 created with the relay
   endpoint `172.29.77.1:64610` and destroyed with endpoint `172.29.77.14:54937` — WireGuardNT accepted the attacker's
   authenticated direct datagrams and roamed the peer to the direct address, and the attacker received direct
   replies. The Windows Defender Firewall rules bound to the WireGuardNT listen port do not constrain that kernel
   socket (WireGuard's own WFP session hard-permits its service traffic; Defender rules sit in a lower-priority
   sublayer). The test never passed on any Codex commit (runs 35–46 all red). A WFP transport-layer lock owned by
   AKENO is being implemented; until it passes this test the outer lock counts as **not effective**.
5. `wireguard`: "The actual positive DNS reply was not captured" — native DNS observer; being investigated.

## Fixes in this continuation

- `17ba26f` Windows-only test assumptions 1 and 2 (msiexec separator, PYTEST_CURRENT_TEST length).
- (this push) **WFP relay-only transport lock** `akeno/network/wfp.py`: AKENO-owned persistent provider/sublayer,
  six filters per LAN tunnel at INBOUND/OUTBOUND_TRANSPORT_V4/V6 (permit only UDP local port P <-> relay R:port,
  block everything else on P; IPv6 fully blocked). Installed and verified in one BFE transaction before the tunnel
  starts; re-verified every heartbeat (missing lock => session ends, never "active"); removed only after the tunnel
  is gone; survives a helper crash; `recover()` removes tunnel -> Defender rules -> lock. Filter keys are uuid5 of the
  tunnel name; a filter under an AKENO key that is not AKENO-owned is never deleted. Session status carries
  `privacy: relay_locked`. Unit tests: `tests/test_network_wfp.py` (x64 ABI offsets, GUID layout, plan validation,
  transactions/abort, ownership, session/recovery order). Windows proof pending: `network-windows.yml` `lan-play`.
- (this push) DNS observer: Packet Monitor records of layer-3 adapters arrive under an Ethernet link type without an
  Ethernet header (16 records, 0 parsed in run 40747826167). Such records are accepted only when the IP header proves
  itself (length field == captured length, valid IPv4 header checksum); non-IP EtherTypes are reported as metadata.
  Windows proof pending: `network-windows.yml` `wireguard`.
- `network-local-e2e.yml` now also runs on `claude/**` (relay/OpenTTD and VPN-exit labs).
- (this push) Linux segfault (exit 139) root cause: `TaskBar` created one `_Relay` QObject per bar and handed its
  `progress.emit` to worker threads. When a page was closed while its doctor/network sampler still ran, the relay was
  destroyed on the GUI thread while a worker could emit on it (use-after-free; not reproducible on every run, the
  local run did not hit the window). Fix: one process-lifetime relay created on the GUI thread, bars reached only by
  weak reference and `shiboken6.isValid`, and a closed bar cancels its running operation (`destroyed` captures the
  context holder, not the widget). `tests/conftest.py` now drains workers after every test and fails a test whose
  background task outlives it (60 s). No test was skipped or disabled.

## Windows evidence on this branch

| Run | Commit | Result |
|---|---|---|
| [WireGuard on Windows 40762019770](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40762019770) `lan-play` | `acacccc` | **WFP lock proven:** "transport lock verified before reporting active", "valid encrypted direct datagrams cannot bypass the outer relay-only filter", "native peer endpoint stays on the relay"; WireGuard log shows no roaming. Then failed at "after the user allows the game program…": the E2E called `allow_program` on a second `LanFirewall` object whose "default deny 1" collided with the session's set (test bypassed the GUI→helper channel). Fixed: E2E uses the production `lan-allow` channel; `_deny_other_ports` now removes every older default-deny set of the tunnel, whichever object created it. |
| same run, `wireguard` | `acacccc` | Bare-IP parsing confirmed (8/8 records parsed, all IPv4) but no record matched the expected reply tuple. Next: match by resolver address, both ports and the random transaction/question when Windows reports an unspecified local address for the connected UDP socket; failure now reports only structural counts. |

| [WireGuard on Windows 40762936142](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40762936142) `lan-play` | `482ffa4` | **Green: 44 real Windows LAN Play checks passed** (coordinator proof, relay-only path, WFP lock verified before active, encrypted direct-roaming attack dropped and peer endpoint stays on the relay, SMB/RDP/NetBIOS unreachable, game allow via the GUI channel, ports outside the profile closed, AllowedIPs spoofing blocked, revocation, lock removed after clean stop, lock and rules survive a killed session process, recovery removes tunnel+rules+lock, routes restored). |
| same run, `wireguard` | `482ffa4` | Positive tunnel DNS reply now captured. Remaining: "No kernel drop record proves an asynchronous DNS denial" -> structural diagnostics pushed in `2f14877`. |
| [Isolated relay and VPN exit traffic 40762019845](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40762019845) | `acacccc` | success |
| [LAN Play NAT lab 40762019741](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40762019741) | `acacccc` | success |

## UI work

- `1938385` One-click AKENO VPN card (`akeno/ui/network_vpn.py`): flag tiles for the eight planned countries plus any
  catalog country, each saying why it can/cannot be chosen (no exit is available today, so all are disabled with
  "No verified AKENO exit server in this country yet"); Connect/Disconnect/Recover/Diagnostics; facts only from the
  verified session status (IPv4/IPv6 exit, verified country, connected-for, leak protection, latency only if measured);
  stale status => Needs recovery; error codes => DE/EN sentences. Own WireGuard profiles + VPN Gate review moved to a
  closed expert section. 35 new German strings.

## DNS observer iterations (wireguard job)

| Commit | Finding | Change |
|---|---|---|
| `482ffa4` | positive reply captured; one UDP denial probe without drop record | structural diagnostics (`2f14877`) |
| `2f14877` | `drop_records: 0`, probe `seen_in_capture: 0` (no leak observed) — Packet Monitor does not record WFP ALE classify drops | `3eb3d19`: read the BFE classify-drop event log (`akeno/network/wfp_events.py`, `FwpmNetEventEnum0`, read-only) for the exact probe tuple; a probe seen on a link = leak, never "denied"; event collection off = fail closed (setting never changed) |
| `3eb3d19` | positive reply not captured this time (4 unrelated records): `pktmon start` returns before recording | `7343d86`: wait until pktmon reports the private ETL, settle 0.5 s, second positive control after the denial probes, one captured reply per resolver required |

| `7343d86` | both outbound tunnel queries recorded on the WireGuardNT layer-3 adapter (`query_payload: 2`), inbound reply not handed to Packet Monitor | `118e7e0`: capture control accepts the byte-identical query recorded without a link header (tunnel adapter) or the reply; a query on an Ethernet link never counts; the socket still validates every reply |

| `118e7e0` | **[WireGuard on Windows 40766590526](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40766590526) `wireguard`: green, 41 real Windows WireGuard checks** — production DNS probe `dns_verified: True`, `denied_paths: 3` (explicit denials plus the silent UDP denial proven by the WFP classify-drop event), `unroutable_paths: 3`; IPv4/IPv6 Internet packets through the tunnel; foreign DNS not delivered; system DNS to the tunnel resolver; no direct Internet while up; routes/DNS restored; Internet works after disconnect | — |

## Other changes since the last table

- `4f864a5` build.yml: packaged `AKENO.exe --network-helper` through the runas verb (read-only status, refusals,
  no tunnel service/adapter afterwards); report uploaded with the Windows evidence.
- `a8ca408` merged `codex/akeno-network-staging-20261028` @ `325d35c` (history only: the files were already identical).
- `f66b9eb` upgrade path from the live 3.1.1 installer; `3021400` 3.2.0 release notes (EN + DE/EN Release Manager text);
  `e15bd43` provider country matrix (web search, unverified); `65a9ab3` report sections 7–9; `ec54292` VPN latency =
  median DNS round trip through the tunnel (display only).
- Local: server suite **262 passed** (Python 3.12, `/opt/akeno-server-env`).
- `23d79e5` LAN privacy row + "IP protection missing" instead of Connected without the lock; browser polish.
- `92ccc2b` regenerated fixture screenshots incl. a fixture connected-VPN view (documentation IPs).
- [Production read-only check 40765356305](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40765356305)
  (read-only, 14/14): **live stable is 3.1.1, schema 2, sequence 3**, key `d6139235b220a4aa`, issued 2026-10-26,
  expires 2026-11-25; installer `138e8958…f9c1` (157 947 314 B), portable `bdcf3ae5…7b66` (223 594 634 B); 3.0.0
  (seq 1) and 3.1.0 (seq 2) unchanged. => 3.2.0 is unpublished and must be signed as **sequence 4**. The build's
  upgrade-path step now also installs the real 3.1.1 installer (SHA checked) and updates it to this build.

## Final verification commit

Code freeze candidate: **`eaef058ec4bd08d487a9e44e24e2c1dc924bd8d0`** (later commits are documentation only; verify with
`git diff --stat eaef058 HEAD -- . ':!docs'`). Runs on exactly this commit:

- build.yml [40767392705](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40767392705) — running
- network-windows.yml (dispatched), lan-lab.yml [40767470403](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40767470403),
  network-local-e2e.yml [40767474222](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40767474222),
  components-windows.yml (dispatched) — running/queued
- Earlier build on `acacccc` (cancelled to free the runner) already passed: Linux tests, server (SQLite/PostgreSQL/
  Docker), real upstream assets, **Windows unit/integration/UI tests**, 150 % UI, smoke from source, system tools.

## Next concrete step

Read `network-windows.yml` for `7343d86` (wireguard: DNS gate), the production read-only check, and the full
build.yml on the newest commit; finish the local full client run (`/tmp/claude-0/linux-full2.log`) and the server
suite; then write `docs/AKENO_320_FINAL_RELEASE_REPORT.md`.
