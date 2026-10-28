# Managed VPN session contract and verification gate

`akeno/network/vpn_session.py` runs inside the explicitly elevated helper. It is part of the ordinary AKENO
client; no separate account or profile import is required. `VPNClient` uses the existing account/session/device
and a separate local DPAPI identity. Its signed lease verification is a separate layer.

Constructor: `ManagedVPNSession(client, profiles, tunnel_manager, journal, status, stop_requested, probe=None,
*, clock=time.time, sleep=time.sleep, lan_journal=None)`. `run(country)` blocks until stop or a failure and returns
a safe status dictionary. `recover()` performs owned cleanup without obtaining another lease. The helper must
hold the OS session mutex around both methods. The GUI controls its nonce-bound stop store and reads status.

The managed journal, public status and device identity are DPAPI stores. The profile store contains only DPAPI
blobs. Session intent is written before profile creation; profile identity and its digest are journaled before
native installation. Cleanup reconstructs a missing shared tunnel journal only for that owned profile. Native
removal still proves possession of the profile's private key. Failed removal preserves the profile and recovery
metadata. Imported user profiles are never selected for deletion.

Flow: provision → await server acknowledgement → create managed profile → native start → fresh handshake →
renew permission → real egress/route/DNS verification → connected. The lease is renewed every twelve seconds;
permission expiry, revocation, changed server/address configuration or a lost native handshake triggers cleanup.
Local native cleanup happens before server revocation or profile deletion. If the remote control plane is
unavailable, local cleanup still proceeds and remote recovery metadata is retained. A signed lease expired by
at least sixty seconds may be forgotten locally without an online server. This does not certify a malicious
server's behavior; the server reconciler independently enforces its own lease expiration.

States exposed to the GUI: `provisioning`, `awaiting_exit`, `connecting`, `awaiting_handshake`, `verifying`,
`connected`, `reconnecting`, `stopped`, `failed`, `verification_required`, `recovery_required`. Status contains a wall-clock
`updated`, optional country and signed `lease_expires_at`; only a verified connected result contains observed
`exit_ipv4`, `exit_ipv6`, `routing_verified`, `dns_verified`. The GUI must treat stale/expired status as unavailable,
never continue showing Connected after its helper disappears. Error codes are fixed and never include HTTP,
native command, token or profile text.

## Measured default verification gate (native validation still required)

The default observer checks native full-tunnel routes, exact DNS settings and assigned
source addresses. The new HTTPS transport binds both the socket source and Windows
IP_UNICAST_IF/IPV6_UNICAST_IF to the signed tunnel assignment. It uses normal system TLS
trust and fixed `api.ipify.org` / `api6.ipify.org` hosts, validates bounded direct HTTP 200
JSON, and rejects redirects/private or wrong-family results. An active system HTTP proxy
still makes verification unavailable; no proxy bypass is attempted.

DNS configuration alone is insufficient. The observer requires actual nonce/question-bound
UDP DNS replies, then probes UDP/TCP 53 and TCP 853 on each eligible non-tunnel interface.
Only explicit Windows access denial or a captured matching kernel-drop record is counted
as blocking. A missing route must be independently established and is counted separately.
Accepted sends/timeouts without evidence fail. Packet Monitor uses a bounded capture in
an Administrators/SYSTEM-only high-integrity directory. It refuses existing collectors or
filters; stopping requires ownership of the exact private capture path. Raw captures never
leave that directory and are removed on normal cleanup. Inventory changes during the
measurement and unimplemented scoped link-local DNS paths fail closed.

Native CI is still validating this observer. Neither successful unit tests nor a signed
lease means a real country exit works. `dns_verified` becomes true only after the complete
measurement path succeeds; missing evidence returns `verification_required` and cleanup.

After an established tunnel loses its handshake or returns from standby with an expired
lease, at most three reconnects are attempted (2/4/8 second stop-responsive backoff).
Each cleans the owned tunnel and revokes the prior lease before fresh provisioning and
all verification. Changed signed configuration, authorization failure or incomplete cleanup
is terminal. This state-machine coverage does not prove actual Wi-Fi/standby behavior.

An explicit trusted `probe(claims, tunnel_name)` implementation must return actual `ipv4`, `ipv6`, boolean
`routing_verified=True` and `dns_verified=True` only after those checks really succeed. The session compares both
egress addresses against the independently attested signed country lease, checks the handshake before and after
the probe and checks permission expiry again after blocking operations.

`tests/test_network_vpn_session.py` uses explicit API/native/egress stand-ins with the real strict profile parser
and `TunnelManager`. It checks state transitions, ownership ordering, renewal, stopping, verification rejection,
expiry during a blocking probe, revocation, immutable configuration, mutual exclusion, failed native cleanup,
remote outages, recovery after a lost shared journal and profile-digest mismatch. These tests are not evidence
of Windows DPAPI, a WireGuard handshake, DNS leak prevention or a real country exit.
