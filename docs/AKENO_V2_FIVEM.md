# FiveM Legacy community workflow (v2)

Open **My Games → FiveM Legacy → Community DLSS 5**. This is a real, separately scoped
installer, not the GTA5.exe adapter or the v1.6 read-only feasibility screen.

The concrete experimental stack is:

```
FiveM Legacy GTAProcess: D3D11
  → genuine ReShade 6.8 full add-on, plugins/d3d11.dll
  → GenericDepth + dh_uber_motion (estimated vectors)
  → pinned DLSS5-Feeder 1.19.0, private D3D12 transport
  → one locally licensed RenoDX DLSS5 consumer, exact ConfigVersion 7
  → independently authenticated SR runtime + explicitly eligible local NR runtime
```

The game device and Feeder's private consumer device are reported separately. The source
reference's D3D12 assertion is not adopted as evidence for FiveM's game renderer.

## Preparation

1. Detect the real Legacy client. Its launcher, app path, GTA path, edition, renderer selection,
   client files and existing plugin inventory are inspected. Enhanced/unknown editions use no
   generic fallback. Generic game installers remain unable to modify FiveM.
2. Supply the test server and operator permission reference. Confirm the exact permitted graphics
   mechanism. A `server.cfg` plus included configurations can be read locally; include order and
   last assignments matter. Missing/cyclic/escaped/dynamic includes leave policy unresolved.
   Server configuration contents and secrets are not copied into the app database or logs.
3. Resolve the licensed dependency bundle and SR runtime. Import the licensed NR consumer once
   using the UI (file, exact version, original HTTPS source, license and local-use consent).
   Its PE architecture, SHA-256 and actual NR config markers are checked; a generic RenoDX HDR
   add-on is rejected. These checks establish a declared format, not an independent binary audit.
4. Select an NR runtime with a profile explicitly covering `renodx_nr` and its D3D12 transport.
   The father's RTX 4080 is an experimental target rather than a universal rejection. Known
   RTX-50-only runtime builds remain rejected on that GPU. No signature or capability check is
   patched to turn a refusal into success.
5. Choose Performance/Balanced/Quality/Ultra/Custom, confirm experimental local use, and preview
   the complete file list. Every FiveM/GTAProcess must be stopped.

## Client compatibility found in real Cfx source

Reviewed Cfx source commit: `4c4fe28a2fe4359610024269b2f1bf60cc5ea982`.

- `code/components/rage-graphics-five/src/RenderHooks.cpp` creates/wraps a D3D11 game device.
- `ReShadeFixups.cpp` explicitly scans `plugins/d3d11.dll` and other graphics hook names,
  checks their real ProductName/version and loads accepted graphics libraries.
- ReShade 5.9.0 is hard blocked. Feeder 1.19 requires modern full add-on ReShade, so downgrading
  to an old ReShade cannot satisfy this recipe.
- The current client source rejects ReShade 5+ unless its **own machine-bound crash-risk
  acknowledgement already exists** in CitizenFX.ini. AKENO checks that existing native value
  using Cfx's actual HashString algorithm. AKENO does not generate/write it, alter the client,
  disguise another DLL as ReShade, or turn server ownership into client approval. When the
  client rejects the selected build, installation is unavailable with that exact explanation.
- Pure level 2 blocks the route. Pure level 1 needs a reviewed client-recognized complete artifact
  allowlist; this stack has no such contract, so an operator-entered hash list cannot enable it.
  Only an actually permitted client/server configuration can proceed. AKENO changes no Pure,
  anti-cheat, integrity, signing or server policy values.

Client changes after preview invalidate the plan. Source research is a loading contract for an
experiment, not proof that a particular installed client accepted or executed this stack.

## Deployment, launch and restoration

`akeno/fivem/community_stack.py` prepares the complete pinned payload, validates source bytes,
rechecks client/configuration/component/target identities, and applies it through the existing
journalled Installer. Writes are confined to the inspected `FiveM.app/plugins`; links,
junctions, escaping paths and conflicting mods are rejected. Original GTA V files are untouched.
Existing matching ReShade bytes can be preserved. Other foreign components need an explicit
coexistence review; AKENO does not overwrite them under a generic consent checkbox.

Preset changes use real `RenoDX.DLSS5` ConfigVersion 7 keys in ReShade.ini. Feeder stays at 100%
transport resolution; NRResolutionScale and NRPasses set consumer cost. NRStyle defaults to 0
because the reference reports style-2 crashes on some paths. No predicted FPS is shown.

Launch uses the normal FiveM launcher after renewed eligibility/byte checks. It means **launch
requested**, not “DLSS 5 works.” Optional Advanced diagnostics retain actual module/log evidence
and unknown renderer/input/Feature-18 states. Successful SR evaluates alone never verify NR.
Removing the mod uses verified backups and does not require renewed server permission. Restoration
still requires the original client target and stopped processes.

## Acceptance evidence

Automated inert fixtures cover a complete stack install, preset change, denied Pure/client routes,
policy/source changes after preview, local consumer format rejection, and exact restore. Separate
GTA fixture hashes are unchanged. No real DLL was executed by these tests.

The physical acceptance test is pending: father's RTX 4080, exact driver/runtime/consumer/client,
permitted private server, valid depth/motion, Feature 18 creation and successful evaluate,
measured GPU completion if available, actual visual contribution, stable gameplay and restore.
A related 4080 Super report with long NGX stalls is a known risk, not proof on this RTX 4080.
