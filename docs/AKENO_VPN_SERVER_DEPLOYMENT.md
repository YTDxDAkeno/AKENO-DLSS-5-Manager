# AKENO VPN exit server deployment (explicit infrastructure prerequisite)

This is an operator procedure for a **new, dedicated, expressly authorized** VPN exit. No exit has been
deployed by writing these files. The production license VPS is not an authorized general-purpose exit.
No USA, Turkey or other country is usable until an actual server passes the independent checks below.

## Implemented boundary

- `server/akeno_server/vpn/agent.py` reconciles short-lived public-key host-route allow-lists onto one dedicated
  `akenoexit0` interface. It uses distribution `/usr/bin/wg`, fixed argument arrays, no shell or remote scripts.
- An expected server public key prevents using another interface's identity. An owner-only journal records managed
  public keys before mutation. Restart removes previously managed peers; unfamiliar existing peers prevent takeover.
- A manifest expires within 30 seconds, individual peers within 45 seconds. Each two-second refresh checks the
  current account/device/license authority. Failed API refresh, malformed manifest, stale lease or failed heartbeat
  removes managed peers. Deadline checks also run between reconciliation commands. No private device key is uploaded.
- Kernel-owned nftables `leased4`/`leased6` elements expire within the manifest's remaining lifetime (at most 30
  seconds). Both outbound source and return destination forwarding require a live element. This stops forwarding
  even if the agent freezes or receives SIGKILL; WireGuard peer metadata may remain until recovery. Atomic fence
  updates precede peer installation and clearing precedes removal. Service supervision/recovery and packet-level
  freeze tests are still required. Do not equate a green heartbeat with a verified Internet exit.
- HTTPS agent authentication, operator registration, network permission signing and independent exit measurement
  use separate credentials/purposes. Do not reuse release signing keys or production license secrets.

## Prerequisites and costs

Obtain owner approval for the specific provider, country, monthly cost, bandwidth/overage price, acceptable-use
policy and abuse contact before purchase. Confirm that commercial shared VPN exits, UDP WireGuard and forwarding
are permitted. A marketing location does not establish IP geolocation. Server registration does not enable it.
The provider must supply a reachable authorized UDP endpoint, a real IPv4 exit, **and a working global IPv6 uplink**
for this dual-stack implementation. There is no silently leaking IPv6 fallback.

Use a dedicated Docker-capable Linux VM with a functioning distribution WireGuard kernel implementation. The
current cloud executor does not expose that kernel implementation; Linux userspace WireGuard tests elsewhere in
this repository do not prove this exit container's kernel/forwarding path. No unofficial kernel/driver download
or verification bypass is part of the procedure.

## Build the container

From repository root, resolve and review the official `python:3.12-slim-bookworm` image digest. Keep the digest,
source commit, resulting local image ID, distribution package versions and build log in an operator record.
`deploy/vpn-exit/Dockerfile` requires an explicit base image; it has no floating default. Debian package signatures
and TLS verification stay enabled. A package index may change between builds, so this is a reproducible procedure,
not a claim of byte-identical images without a locked Debian snapshot.

```bash
docker build --build-arg AKENO_EXIT_BASE_IMAGE=python:3.12-slim-bookworm@sha256:REVIEWED_DIGEST \
  -f deploy/vpn-exit/Dockerfile -t akeno-vpn-exit:REVIEWED_COMMIT .
docker image inspect akeno-vpn-exit:REVIEWED_COMMIT --format '{{.Id}}'
```

In the managed cloud builder preserve its configured proxy/registry identity. Supply the combined public CA
bundle through `--secret id=system_ca,src=/etc/ssl/certs/ca-certificates.crt` and use writable
`BUILDX_CONFIG=/tmp/akeno-buildx` if required. The build mount is ephemeral; no session CA is copied into the image.
Runtime control HTTPS also needs the environment's normal CA trust if it uses a private enterprise CA; mount a
combined bundle read-only and set `SSL_CERT_FILE` for that process. Never disable certificate verification.

## Separate operator registration and keys

1. Configure a separate control-plane database, `AKENO_VPN_ENABLED=true`, purpose-specific network signing key,
   independent probe public-key pins and a new high-entropy `AKENO_VPN_OPERATOR_TOKEN_FILE`. The defaults expose
   no VPN API. Do this only in an owner-approved environment; do not change production during local development.
2. Inside the dedicated exit's protected runtime directory generate a new distribution WireGuard keypair.
   `umask 077` applies before key creation. Store the private key in `runtime/wireguard.key`, mode `0600`, owner
   matching the root runtime user. Do not echo it, pass it as a shell argument, put it in `.env`, images or Git.
3. Authenticate to `POST /api/v1/network/vpn/operator/servers` over verified HTTPS with the separate operator
   bearer credential. Send the server public key, actual authorized public UDP `endpoint` IP/port, `name`, ISO
   `country`, unique `subnet4` within `10.204.0.0/16` (a `/24`), unique `subnet6` within `fd42:204::/48` (a `/64`),
   explicit trusted resolver IPs and `capacity` (1–250). All subnets must be unique across exits.
4. Capture the returned **one-time agent token directly into** `runtime/agent.token` mode `0600`; only its hash
   is stored by the API. Redact the response from terminal/log captures. Never use an operator token in the agent.
5. Copy `deploy/vpn-exit/operator.example.json` to ignored `operator.json`; fill in the returned server ID, the
   matching public key, approved control origin and the exact registered subnets. An example IPv6 subnet is
   `fd42:204:0:1::/64`, not `fd42:204:1::/64` (which is outside the chosen `/48`).

Use a request client reading bearer credentials from protected files, not literal tokens in shell history or CLI
arguments. Repository templates contain no usable secrets or real server addresses.

## Namespace and firewall

Review `deploy/vpn-exit/compose.yml` and `entrypoint.py` together. The template:

- Has an explicit `approved-exit` profile, `restart: no`, read-only root, dropped capabilities except `NET_ADMIN`,
  resource/PID limits and no Docker socket, host root, production data, host PID or host network mounts.
- Requires an operator-approved **dedicated external uplink network**. Do not attach a production application or
  database network. Its default host UDP publication is loopback-only (`127.0.0.1:51820`). No public port is opened.
- Enables forwarding only in its own container network namespace. **Never use `network_mode: host`**, run its
  entrypoint on the host, make it privileged or add host firewall/sysctl commands. A Docker marker is an accidental
  invocation check, not proof of network-namespace isolation; verify the actual compose/container configuration.
- Installs its own namespaced nftables tables in one atomic transaction before bringing up its new interface.
  Input/forward default-deny; clients cannot access the exit's local services or other peers. Forwarding allows
  only dedicated source pools toward `eth0`, blocks private/reserved/multicast destinations, SMTP/25, Windows
  service ports, unsolicited inbound traffic and caps aggregate forwarding at 8,000,000 bytes/second.
- Performs IPv4 masquerade and explicit IPv6 NAT66 because clients receive ULA addresses. This is **not** a
  routed global IPv6-prefix implementation. Public IPv6 egress and provider/router policy must be independently
  tested. A provider with only IPv4 does not meet the current full-tunnel prerequisites.

The relay and VPN exit are separate roles. LAN Play uses `10.203.0.0/16`; VPN uses `10.204.0.0/16`. A simultaneous
LAN/VPN session still requires routing/MTU/DNS checks on Windows; disjoint address pools alone are not that proof.

## Explicit local start (after review)

Prepare ignored `.env` with the exact immutable image ID in `AKENO_EXIT_IMAGE` and the approved external network
name in `AKENO_EXIT_UPLINK`. It contains selectors only, no bearer or private keys. Create protected
`runtime/state/` owner-only. Check mounts and profile without starting anything:

```bash
cd deploy/vpn-exit
docker compose --profile approved-exit config
```

The following is a **manual, owner-authorized deployment step**, not executed by the development task:

```bash
docker compose --profile approved-exit up -d exit
```

The new agent starts from an empty peer allow-list. An expired/missing independent verification keeps the API
catalog empty. For any public test, separately authorize the source ACLs, public UDP binding, duration, clients,
bandwidth budget, abuse monitoring and provider firewall changes. Do not convert the default loopback template
into a public relay/exit without those decisions.

## Prove the real exit before enabling a country

The initial disabled exit has no customer peers, so it cannot be verified by allocating an ordinary customer
lease. The separate operator-only `POST /api/v1/network/vpn/operator/servers/{server_id}/probe` accepts only a
probe's **public** WireGuard key and grants one measurement peer for at most 45 seconds. It uses reserved
`.252/32` and `::fc/128` addresses in that exit's assigned subnets, replacing any previous measurement peer.
It never enables a country, bypasses customer licensing, appears in the customer catalog or accepts a private
key. Renewing the measurement window requires another explicit authenticated operator request. The same key
cannot be an active customer peer. Disabling an exit revokes the probe as well as customer leases.

Create the probe in its own expressly authorised namespace/host with a separate WireGuard identity; configure
the returned public endpoint, DNS and addresses through its approved operator procedure. The private key stays
on that probe. Prepare a separately generated owner-only **Ed25519 independent measurement key**, provision only
its public half in `AKENO_VPN_PROBE_KEYS_PATH` (`{"keys":{"<sha256-first16>":"<base64url-public-key>"}}`),
and keep that private key outside both the exit agent and control server. Operator bearer and probe signing
credentials remain different trust roles.

`python -m akeno_server.vpn.probe` is the independent measurement tool (run from `server/` in that authorised
probe environment, never to circumvent the managed cloud executor's proxy). Its operator arguments are:

```text
--server-id REGISTERED_ID --server-public-key REGISTERED_PUBLIC_KEY --country EXPECTED_ISO
--source-ipv4 ASSIGNED_PROBE_IPV4 --source-ipv6 ASSIGNED_PROBE_IPV6
--key-file OWNER_ONLY_RAW_ED25519_FILE
--country-database LICENSED_GEOLITE2_COUNTRY_FILE --database-sha256 TRUSTED_OFFLINE_SHA256
--output NEW_OWNER_ONLY_PROOF_JSON
```

The tool changes no adapter, route or firewall. It binds each verified-TLS IPify observation to the assigned
IPv4/IPv6 tunnel source address and rejects redirects, oversized responses, nonglobal observed IPs and wrong
address families. Country checks use a licensed GeoLite2-Country/GeoIP2-Country database no older than 30 days;
the reader consumes precisely the checksum-verified in-memory snapshot. The optional independent-probe dependency
is pinned separately in `deploy/vpn-exit/probe-requirements.txt` (`maxminddb==3.2.0`, Apache-2.0). It was installed
and imported locally; **no licensed country database or real country probe was available**. Obtain MaxMind's
required account/licence, database and attribution under its current terms; never commit that database or keys.
An outdated database, mismatch or missing reader blocks proof generation. A matching GeoIP result represents
that source's current assignment, not a guarantee of physical server location.

The tool writes a one-use typed proof valid for 120 seconds directly into a new mode-0600 file. Submit that JSON
using the operator-authenticated verification endpoint below. Do not print bearer tokens, private keys or proof
tokens into command logs. Both measured address families must match the desired country; there is no IPv4-only
success fallback. Live IPv4/IPv6/DNS/route probes and deployment permission remain external prerequisites.

1. Through the actual client tunnel, measure IPv4 and IPv6 from independent HTTPS IP observers, DNS resolver
   behavior and routing; record verified TLS sources, UTC timestamps and sanitized results. An agent's own address
   assertion is insufficient. Validate geolocation using a trustworthy maintained source and provider evidence;
   reject country disagreement rather than displaying the requested flag.
2. A separate trusted probe signs the typed bounded measurement proof described by `akeno_server/vpn/proofs.py`.
   Its pinned public key is configured on the control plane; **its private key must not be on the exit agent**.
   Submit it using `POST /operator/servers/{server_id}/verify` with the operator credential. Proof replay, wrong
   server/public key, expired measurement and country mismatch must fail or disable the exit.
3. Only recent independent verification plus an authenticated healthy agent and free capacity exposes the country.
   The Windows client also verifies the signed device-bound lease, waits for the agent's applied generation and
   a real WireGuard handshake, then independently verifies the visible exit before saying connected.
4. Test multi-device isolation, capacity exhaustion, entitlement/device revocation, agent/API outage, lease expiry,
   process freeze/restart, IPv6/DNS leaks, disconnect and restore. Do not test arbitrary third-party destinations.

No real country proof or public Internet exit was completed by the local unit tests. Provider offers and current
costs remain a separate review in `AKENO_VPN_PROVIDERS.md`; an unverified offer cannot create catalog availability.

## Disable and roll back

First authenticate to `POST /api/v1/network/vpn/operator/servers/{server_id}/disable`; it withdraws the catalog entry
and revokes leases. The next agent reconciliation removes peers. Confirm the native peer table becomes empty.
Then stop only this compose project's `exit` service; dedicated namespace removal cleans its interface/firewall.
Preserve protected runtime secrets/journal for an authorized recovery and destroy them securely only on explicit
retirement. Do not run global Docker prune, flush host nftables/iptables, restore someone else's rules or remove
production networks/volumes. Agent normal SIGTERM also attempts managed-peer cleanup.

## Evidence in this development environment

- Agent policy/native-command tests use an explicit stand-in and are labelled accordingly. They cover malformed
  or expired manifests, host-route pool isolation, duplicate addresses, revocation, API/heartbeat failures,
  crash-journal recovery, foreign-interface refusal and file permissions.
- Actual container build passed using reviewed official Python image digest
  `sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337`, source selected at build time.
  The full nftables ruleset passed actual `nft -c` in an isolated `--network none` namespace with `NET_ADMIN`.
  Local logs: `/tmp/akeno-vpn-exit-build.log`, `/tmp/akeno-vpn-nft-syntax.log`.
- Actual kernel checks in an isolated `--network none` Docker namespace installed IPv4/IPv6 timed elements,
  observed both expire without any agent refresh, and cleared the sets atomically (three checks passed).
  Log: `/tmp/akeno-vpn-nft-expiry.log`. Compose profile configuration also validated without starting an exit.
- Container build, kernel timer tests and actual forwarding are separate evidence. Neither a successful build nor
  a syntax/timer check proves a VPN exit, country IP, Windows tunnel or DNS restoration.
