# Windows network component installation

AKENO uses the official WireGuard for Windows MSI, signed by **WireGuard LLC**, through its documented enterprise
service interface. The GUI stays unelevated. Installation is a distinct, explicit settings action with user consent
and UAC; enabling a module, opening a page, or applying an AKENO update never installs or activates a tunnel.

## Implemented contract

- `akeno.network.bootstrap.status()` only reads the installed signed tools and manager state. Results are
  `unsupported`, `missing`, `incomplete` or `ready`; no download/service start/elevation happens.
- `bootstrap.install(consent=True)` requires an elevated Windows helper. It selects only a numeric-version MSI for
  the native OS architecture (AMD64/ARM64) from the fixed official index. Index is limited to 256 KiB, MSI to 32 MiB;
  HTTPS and system CA validation stay enabled, configured proxies are retained, and redirects are rejected.
- The temporary MSI is created under the OS Program Files directory in a new random directory **owned by
  Administrators**, with a protected Administrators/SYSTEM DACL and inherited high integrity label. It is not placed
  in a user-writable temp directory. This prevents same-account unelevated replacement between verification and MSI
  execution. Existing paths, links and junctions are refused.
- Fresh MSI Authenticode must chain to Windows trust and have verified signer `WireGuard LLC` with **online chain
  revocation checking**. Failure to reach revocation services fails closed; there is no cache-only retry. This uses
  `authenticode.verify(..., online=True)`. Existing imported/runtime/installed-executable callers preserve their
  previous offline verification contract by default.
- The helper executes only the OS-resolved System32 `msiexec.exe`, with fixed `/i <verified-msi> /qn /norestart
  DO_NOT_LAUNCH=1` arguments and no shell. It validates installed `wireguard.exe` and `wg.exe` before the documented
  `/installmanagerservice` action. Existing ready installations are a no-op; incomplete trusted installations need
  only manager repair. Temporary files are cleaned on failure or success.
- Results distinguish component readiness and a requested reboot. **Ready components do not mean a connected
  tunnel.** The installer neither creates an AKENO profile nor alters routes, DNS, firewall or signature policy.
- AKENO does not uninstall a shared WireGuard product or another application's adapters. AKENO tunnel teardown
  continues to require the existing fixed tunnel-name and matching public-key ownership checks.

## Official source and licensing review

Read-only upstream sources retrieved on 2026-10-28, through normal TLS/proxy validation:

- [Enterprise installation and service CLI](https://github.com/WireGuard/wireguard-windows/blob/master/docs/enterprise.md):
  direct official MSI downloads are explicitly supported; `DO_NOT_LAUNCH`, manager service and tunnel service CLI
  are documented. The manager imports and DPAPI-encrypts profile files without manual user interaction. It can
  show the official tray UI to administrators; operating that UI is not required by AKENO.
- [WireGuard Windows COPYING](https://github.com/WireGuard/wireguard-windows/blob/master/COPYING): MIT-style
  permission, including distribution/modification, with copyright/permission notice retention. AKENO downloads
  the intact official package and does not copy upstream code in this integration. No claim is made that this
  single notice covers every independently distributed driver/dependency.
- [Embeddable tunnel library](https://github.com/WireGuard/wireguard-windows/blob/master/embeddable-dll-service/README.md):
  alternative requires maintained tunnel.dll/wireguard.dll, a correctly configured Windows service including
  unrestricted service SID, and lifecycle/logging integration. It remains a future integration choice. AKENO does
  not load an unverified downloaded DLL or compile/sign its own driver.

Official index: `https://download.wireguard.com/windows-client/`. The current cloud runtime denied this destination
with proxy CONNECT 403. Raw official GitHub documentation was reachable. **No live download/installation was
performed here**, and the source denial was not bypassed. The environment needs that destination allowed for the
actual installer test, plus Windows' normal certificate revocation/chain endpoints as applicable.

## Verification

Linux policy tests in `tests/test_network_bootstrap.py` use explicit native/network stand-ins. They cover source
substitution, traversal, architecture/version selection, size/truncation, redirect refusal, signer/revocation
failure, consent, fixed privileged argv, cleanup, MSI errors/reboot and read-only no-op/repair behavior. They are
not evidence of an actual driver, ACL enforcement, UAC interaction or Internet connection.

On a **disposable elevated Windows runner**, before the existing tunnel tests:

```powershell
python tools/network_bootstrap_windows_e2e.py --consent-install --report network-bootstrap-windows-report.json
```

That check installs the official package if absent, reads the actual protected directory owner/DACL/high-integrity
label, verifies installed Authenticode and manager readiness, and checks no AKENO profile was created. Upload its
JSON as private CI evidence. Run the existing Windows VPN/LAN suites afterwards. Interactive GUI UAC accept/decline,
alternate-admin credentials, installation cancellation/reboot and two real PCs still require separate testing.

No public endpoint, production deployment, driver-signature exception or automatic component update is authorized
by this integration. A certificate revocation outage is reported as installation failure, not silently weakened.
