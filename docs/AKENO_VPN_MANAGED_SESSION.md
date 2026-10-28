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
`connected`, `stopped`, `failed`, `verification_required`, `recovery_required`. Status contains a wall-clock
`updated`, optional country and signed `lease_expires_at`; only a verified connected result contains observed
`exit_ipv4`, `exit_ipv6`, `routing_verified`, `dns_verified`. The GUI must treat stale/expired status as unavailable,
never continue showing Connected after its helper disappears. Error codes are fixed and never include HTTP,
native command, token or profile text.

## Explicit unfinished default verification gate

The default observer uses verified HTTPS to fixed `api4.ipify.org` and `api6.ipify.org`, rejects redirects, bounds
responses and requires global addresses of the matching family. It preserves configured proxy/CA settings. An
active HTTP proxy makes attributing the response to the tunnel impossible, so verification fails instead of
bypassing the proxy. Fixed Windows PowerShell commands inspect IPv4/IPv6 routes and the tunnel DNS configuration.

DNS configuration alone cannot prove that DNS traffic never escapes. Accordingly the default observer returns
`dns_verified=False`. The session then returns **`verification_required` and disconnects**. No public country VPN
is advertised as connected merely because a profile or handshake exists. A tested native DNS leak observer and
real Windows route/leak/restore tests remain required before this gate can be completed. Replacing the flag with
a constant success is not an implementation of that requirement.

An explicit trusted `probe(claims, tunnel_name)` implementation must return actual `ipv4`, `ipv6`, boolean
`routing_verified=True` and `dns_verified=True` only after those checks really succeed. The session compares both
egress addresses against the independently attested signed country lease, checks the handshake before and after
the probe and checks permission expiry again after blocking operations.

`tests/test_network_vpn_session.py` uses explicit API/native/egress stand-ins with the real strict profile parser
and `TunnelManager`. It checks state transitions, ownership ordering, renewal, stopping, verification rejection,
expiry during a blocking probe, revocation, immutable configuration, mutual exclusion, failed native cleanup,
remote outages, recovery after a lost shared journal and profile-digest mismatch. These tests are not evidence
of Windows DPAPI, a WireGuard handshake, DNS leak prevention or a real country exit.
