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

## Next concrete step

Read `network-windows.yml` run for `acacccc` (lan-play: WFP lock vs. direct attack; wireguard: DNS positive
control). Full local client run with the drain fixture (Python 3.12 venv at `/opt/akeno-env`, offscreen Qt).
