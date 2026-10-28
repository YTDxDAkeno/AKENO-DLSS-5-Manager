# AKENO Network technical completion progress

Active branch: `codex/akeno-network-cloud-20261028`. Remote was checked before
continuation: `d926ac0a78d14642fd3051639eb3b4c3f8736be3`.
No merge, release, paid resource, public listener or production deployment is authorized.
Small scanned commits are pushed and the exact remote SHA read back; history is preserved.

## Implemented in this continuation

- Native Windows diagnostics identify cmdlet/error/HRESULT without scripts, tokens or keys.
- Confirmed original LAN failure: NetSecurity rejects `::/0` with HRESULT 0x80070057.
  It is replaced with `::/1` and `8000::/1`, exactly the same denied IPv6 set.
- Confirmed second startup defect: generated LAN ListenPort was rejected by its own
  strict parser. Numeric 1..65535 is now accepted; scripts remain prohibited.
- LAN startup phase status and bounded service readiness; real native handshakes and
  relay echo proceed past the original failures.
- A separate private Windows-container peer exercises native firewall boundaries.
  The adversarial fixture pins each datagram destination, avoiding a race where
  legitimate relay packets changed its session address during the attack loop.
  All direct-roaming, SMB/RDP and game-port assertions remain enabled.
- Measured Windows DNS observer: actual nonce/question-bound tunnel DNS responses,
  every eligible non-tunnel interface probed for UDP/TCP53 and TCP853, explicit
  Windows denial or matching kernel-drop evidence; independently missing routes
  counted separately. Incomplete evidence/configuration changes fail closed.
- Packet Monitor collection is bounded/private and preserves foreign collectors.
  Native CI validation is ongoing; it is not a proven working native DNS gate yet.
- Exit-IP HTTPS measurement binds source and interface, preserves default TLS trust,
  uses fixed hosts, rejects redirects, malformed/private/wrong-family responses.
- Managed reconnect: three bounded attempts after established-tunnel loss/resume,
  owned cleanup, fresh signed device lease and full reverification on every attempt.
  Stop during backoff and incomplete recovery are handled without false Connected.
- Trusted-account and room quotas supplement global/source coordinator limits.
- Linux process suspension waits for actual kernel acknowledgment (full-suite race).
- Long installed-folder paths use the existing middle-elision widget with full
  tooltip/accessibility text, fixing a layout regression found during full testing.
- Official SDK/license sources reviewed and pinned in AKENO_VPN_PROVIDERS.md.
  Provider commercial VPN rights, current prices/regions remain unverified.

## Actual evidence so far

| Check | Result and provenance |
|---|---|
| Full backend | 262 passed, 3 dependency deprecation warnings at `15416d9` |
| Source-bound HTTPS/session/reconnect/DNS focused group | 45 passed at `1547a57`, explicit transport/native stand-ins |
| Linux real relay/OpenTTD/outer packet captures | 27 passed on `5d12030` |
| Linux real exit/NAT/NAT66/kernel expiry/crash/recovery | 23 passed on `5d12030` |
| Remote isolated traffic | [40746348448](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40746348448), succeeded on `5d12030` |
| Remote LAN NAT lab | [40746349019](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40746349019), succeeded on `5d12030` |
| Corrected-environment full run before SIGSTOP fix | 1472 passed, 19 skipped, one actual process-status race at `15416d9` |
| Subsequent complete client run while work continued | 1483 passed, 19 skipped, one long-path layout failure; not final immutable-commit evidence |

The earlier two missing-encodings failures are resolved through the scoped Python 3.12
home, without removing tests. The final full run must execute after all source changes,
with no concurrent source edits, and identify the tested commit in its evidence.

Native Windows follow-up is still in progress. `5d12030` established Packet Monitor's
empty-filter output and showed memory mode omits the capture path. `6f852fb` preserves
ownership via a private circular file but cannot yet capture the positive DNS control.
`2329fe3` adds only packet metadata diagnostics to investigate that failure. No raw
DNS capture is exported. Windows-container build failures are being diagnosed through
bounded public CI annotations. Do not mark these jobs passed while they remain red.

## External gates

No real TR, US, DE, NL, GB, FR, JP or CA server is registered/available. Deployment,
registration, short-lived agent enforcement and independent country proof exist, but
real dual-stack exits, provider permission, licensed fresh GeoIP data and explicit
public activation approval are still required. Provider webpages/GitHub API return
proxy CONNECT 403; public GitHub job pages and official raw GitHub sources are usable.
No credential values were printed and no verification was bypassed.

Windows broadcast/multicast interception is not available: the room-scoped forwarding
policy exists, but a generic UDP socket can steal game traffic and cannot safely spoof
reply origins. Keep discovery.AVAILABLE false until a reviewed native capture/injection
adapter and real-game tests exist. Explicit game/IP and relay paths remain supported.

Final exact counts, CI links, functional connections and Claude tasks belong in
AKENO_NETWORK_PLUG_AND_PLAY_HANDOFF.md and AKENO_NETWORK_PRODUCTION_READINESS.md.
Historical checkpoints/results remain in Git history; they are not final-source evidence.
