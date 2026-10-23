# FiveM Legacy: review and rebuilt workflow (AKENO 2.1)

FiveM Legacy is the top-priority target: the owner's PC (RTX 5090) and the private server PC (RTX 4080 16 GB).
This document covers what v2 did, what the Cfx.re source actually requires, and the workflow AKENO 2.1 ships.

## What v2 did

`akeno/fivem/community_stack.py` (`956e5a2`) evaluated about fifteen gates before installing anything. For a
private server five of them could not pass:

* **Server permission forms** (`fivem_permission`, `fivem_client_permission`): server id, permission reference,
  "graphics plug-ins allowed", experimental consent and a client-plugin confirmation, all required.
* **Pure mode** (`fivem_pure`): unknown or level 1 blocked the install.
* **ReShade acknowledgement** (`client_reshade`): the `CitizenFX.ini` line had to exist already, and the code
  was written never to add it. FiveM itself blocks ReShade 5+ until that line exists, so neither side moved first.
* **Consumer and runtime**: RenoDX DLSS 5, `nvngx_dlss.dll` and `nvngx_dlssnr.dll` had to be imported by hand
  and pass a "contract" review. No download path existed, and no runtime could run on the RTX 4080.
* **Detection**: the renderer came from a PE scan of `GTA5.exe`, and the plug-in inventory stopped at 2000 files.
  A normal `reshade-shaders` folder was enough to hit that limit.

The user saw "unavailable" with a list of conditions and no way forward.

## What FiveM really does (citizenfx/fivem `4c4fe28`)

| Question | Source | Answer |
|----------|--------|--------|
| Where does FiveM load graphics mods from? | `ReShadeFixups.cpp` `ScanForReshades` | Only `FiveM.app\plugins`, only the names `d3d8/d3d9/d3d10/d3d10_1/d3d11/dxgi.dll` |
| Does it check the DLL? | same | ProductName must be "ReShade"; 5.9.0 is hard-blocked |
| ReShade 5 and newer? | same | Loads only with `[Addons] ReShade5=ID:<hash> acknowledged that ReShade 5.x has a bug that will lead to game crashes` in `CitizenFX.ini`. The hash is `HashString(lowercase COMPUTERNAME)` (Jenkins one-at-a-time, signed char, `"a"` if unset). FiveM's console prints exactly this instruction |
| Does pure mode (`sv_pureLevel`) block plug-ins? | `PurePackfile.cpp` | No. It validates RPF packfiles (level 1 allows a known audio list); `plugins/` is not inspected |
| Renderer of FiveM Legacy | rage-graphics-five | Direct3D 11 |
| Connect on start | URL handler | `FiveM.exe fivem://connect/<host:port or cfx.re/join/id>` |

So plug-ins are a client-side choice. The server does not grant or deny them, and AKENO can write FiveM's own
acknowledgement line the way FiveM tells the user to.

## The 2.1 workflow

**FiveM Legacy page** (sidebar, Ctrl+2):

1. *FiveM client* card: FiveM folder, GTA V folder and edition, renderer (Direct3D 11, from the Cfx source),
   saved game build, graphics plug-ins already present, and the state of the ReShade notice ("present", or
   "accepted automatically during installation"). It has buttons to open the plug-ins folder and to check again.
2. *Your server*: an address field and **Launch and connect** (`fivem://connect/<addr>`, validated). An optional
   **Check a server.cfg…** reads a `server.cfg` (and its `exec` includes) and reports `sv_pureLevel` as
   information: "pure mode checks game archives, it does not block FiveM.app\plugins".
3. The same game panel as My Games: method, versions, quality preset, **Install DLSS 5**, **Launch game**,
   **Previous version**, **Remove mod**.

**Recipe `fivem`** installs into `FiveM.app\plugins` only (GTA V files are never touched). This is the preview of
a real install, made with the original release archives:

| File | From | Notes |
|------|------|-------|
| `plugins/d3d11.dll` | ReShade 6.8.0, full add-on build (AKENO CI from crosire/reshade `18deaa5`) | `d3d11.dll` is the only name FiveM loads for a D3D11 game. The official reshade.me add-on setup is the alternative source |
| `plugins/dlss5-feed.addon64` | DLSS5-Feeder 1.19.0 | in-process mode; the helper add-on is never installed with it |
| `plugins/reshade-shaders/Shaders/DLSS5_Feed.fx` | Feeder 1.19.0 | |
| `plugins/reshade-shaders/Shaders/dh_uber_motion.fx`, `ReShade.fxh` | dh_uber_motion 0.2.0 (GPL-2.0, bundled) | motion vectors, provider 0 |
| `plugins/renodx-dlss5.addon64` | RenoDX DLSS 5 7.0.0-rc8 (RHI release) | 8.5.0-rc10 and 6.5.3 selectable |
| `plugins/nvngx_dlss.dll` | DLSS SR 310.9.16 (NVIDIA-signed, RHI release) | 310.9.1 selectable |
| `plugins/nvngx_dlssnr.dll` | RTX 4080: 310.8.2 SF-v2; RTX 5090: 310.8.0 NVIDIA | any build is selectable; RTX 50-only builds are blocked on RTX 40 with an explanation |
| `plugins/ReShade.ini` | generated | `AddonPath=.\`, effect paths, the Legacy depth definitions (reversed depth, far plane 1000), Home key for the overlay, `[RenoDX.DLSS5]` preset |
| `plugins/AKENO-DLSS5.ini` | generated | ReShade preset: `DH_UBER_MOTION_020` above `DLSS5_Feed` |
| `plugins/dlss5-feed.cfg` | generated | `mode=2`, `work_resolution=100`, `preset=5` (the FiveM-DLSS5 reference value) |
| `CitizenFX.ini` | edited | adds FiveM's own `[Addons] ReShade5=…` line for this PC; removed on uninstall if AKENO added it |

The generated `ReShade.ini` for the RTX 4080 default (Performance):

```ini
[ADDON]
AddonPath=.\
[GENERAL]
PresetPath=.\AKENO-DLSS5.ini
EffectSearchPaths=.\reshade-shaders\Shaders\**
TextureSearchPaths=.\reshade-shaders\Textures\**
PreprocessorDefinitions=DLSS5_MV_PROVIDER=0,RESHADE_DEPTH_INPUT_IS_REVERSED=1,RESHADE_DEPTH_LINEARIZATION_FAR_PLANE=1000.0,RESHADE_DEPTH_INPUT_IS_UPSIDE_DOWN=0,RESHADE_DEPTH_INPUT_IS_LOGARITHMIC=0
[INPUT]
KeyOverlay=36,0,0,0
[RenoDX.DLSS5]
NRResolutionScale=0.5
NRPasses=1
NRStyle=0
NeuralUplift=1
NREnableUpscaling=0
EnableHooks=2
```

**Defaults by GPU.** RTX 4080: Performance (model at 50 %, 1 pass) with the SF-v2 runtime, which RHI lists for
RTX 20 to 50. FiveM adds the Feeder and the D3D11-to-D3D12 transport on top of the game, so the lightest preset
is the safe start on 16 GB. RTX 5090: Quality (model at 100 %) with NVIDIA's signed 310.8.0 build. Every preset
can be changed after installation without reinstalling, because only the config files are rewritten.

**Existing plug-ins.** A modern ReShade the user already has (≥ 6.8 with add-on support) is reused, not replaced.
A different ReShade, a second consumer or the Feeder helper is shown in the preview as "disable" or "replace"
and needs one approval checkbox. Disabled files are renamed and come back on removal.

**Running client.** Install, update and removal refuse to run while any `FiveM*.exe` process is alive
(`FiveM_b<build>_GTAProcess.exe`, `FiveM_ChromeBrowser`, `FiveM_SteamChild`, …), with a message saying which.

**Removal** restores `plugins/` byte for byte (hash-checked backups), removes the acknowledgement line if AKENO
added it, and deletes the logs the add-ons wrote, unless they existed before the install.

## The private server

Nothing has to be configured on the server for client plug-ins. To connect directly, the owner enters the
server's address once (`192.168.1.20:30120` on the LAN or a `cfx.re/join/…` link) and uses **Launch and
connect**. `sv_pureLevel` does not need to change: pure mode does not look at `plugins/`. Anti-cheat
resources on the server are outside AKENO's control. A server that kicks clients with ReShade loaded would
show that at connect time, and the mod is removed in one click.

## Still to verify on the real PCs

* FiveM Legacy build 3258 (or the server's enforced build) with ReShade 6.8.0 loaded from `plugins/`, with the
  acknowledgement line accepted and no crash on start.
* DLSS5-Feeder 1.19.0 in-process on D3D11: the private D3D12 device is created and RenoDX DLSS 5 picks it up.
  Check `dlss5-feed.log` and `renodx-dlss5.log` in `plugins/`.
* The SF-v2 runtime on the RTX 4080: model loads, frame time with Performance and Balanced. NVIDIA's 310.8.0 on
  the RTX 5090 with Quality.
* Depth (reversed, far plane 1000) on the server's map and with any custom timecycle.
* The provider: dh_uber_motion (0) ships with AKENO. LumeniteFX Kernel (3, used by kerim0x1/FiveM-DLSS5) is not
  downloaded because of its licence; if the owner has it, the preprocessor value can be changed in `ReShade.ini`.
