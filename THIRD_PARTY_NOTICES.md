# Third-party notices and dependency inventory

AKENO DLSS 5 Manager is licensed under the GNU General Public License v3.0 or later (see `LICENSE`).
This file lists every third-party component AKENO bundles, downloads or interacts with, and how.

## Bundled in the Windows build

| Component | Version | License | How it is used |
|---|---|---|---|
| Python runtime | 3.12 | PSF License | Interpreter embedded by PyInstaller |
| Qt for Python (PySide6, shiboken6) and Qt 6 libraries | 6.8.1 | LGPL-3.0 (Qt and PySide6 open-source licence) | User interface. Shipped as separate, replaceable DLLs in the one-folder build, as the LGPL requires. Source: https://code.qt.io/cgit/pyside/pyside-setup.git |
| PyInstaller bootloader | 6.11.1 | GPL-2.0 with bootloader exception | Starts the bundled application |
| Qt WebEngine (part of PySide6 6.8.1; Chromium) | 6.8.1 | LGPL-3.0 (Qt); Chromium's BSD-style and other licenses listed in `QtWebEngineCore`'s license files | Web wallpapers (3.0), sandboxed, no network |
| Qt Multimedia with its FFmpeg backend (part of PySide6 6.8.1) | 6.8.1 | LGPL-3.0; FFmpeg LGPL-2.1-or-later build | Video wallpapers (3.0) |
| cryptography | 44.0.0 | Apache-2.0 or BSD-3-Clause | Ed25519 verification of licenses and release manifests, Cloud Sync encryption (3.0) |
| psutil | 6.1.1 | BSD-3-Clause | Game detection, priorities, widgets (3.0) |

The license server (`server/`, runs on the VPS only, never in the Windows build) uses FastAPI, Starlette, Pydantic,
SQLAlchemy, Alembic, psycopg, argon2-cffi, cryptography, pyotp, segno, Jinja2 and Uvicorn (MIT, BSD, Apache-2.0 or
LGPL-3.0 for psycopg), pinned in `server/requirements.txt`.

Weather data in the optional weather widget: Open-Meteo.com, CC BY 4.0 (attribution shown in the widget).

### Versioned community dependencies (v2 Windows CI)

The release job builds ReShade 6.8.0 from commit `18deaa52de0c425a78b329e9cb3c497281cd00ec`
(BSD-3-Clause) with pinned submodule source and notices. It bundles unchanged AlucardDH
`dh_uber_motion.fx` 0.2.0 at `0783b8fde77df48412d79070be7de4bc3743b898` (GPL-2.0), its complete
source/license, required `ReShade.fxh` at `fd0022170615ce0d8162d219bff07232fa6dd84f` (CC0-1.0),
and the original pinned MIT Feeder 1.19.0 archive with matching source and NIGos MIT notice.
The upstream main MIT notice credits NIGos; the same source tree contains the full bridge notice
under `LICENSE-NIGos-dlss5-bridge.md` (its README's old `bridge-1.0.19/LICENSE` path is absent).

Generated dependency archives carry matching source archives, source/file hashes and notices.
See [COMMUNITY_COMPONENTS.md](docs/COMMUNITY_COMPONENTS.md) and `community/vendor/pins.json`.
No proprietary NVIDIA runtime, protected NR consumer or restricted motion package is bundled.
Local NR consumer imports remain private and require the user's own license/permission.

### AKENO design artwork (`akeno/data/assets/`)

The header, recommendation and sidebar backgrounds, the GPU illustration, the AKENO logo/wordmark SVGs
and the navigation icons come from the **AKENO design package** supplied by the project owner
(`AKENO_DLSS5_Design_Assets.zip`, 2026-10). Per the package's own notes, the bitmaps were cut from
generated design mock-ups and partly upscaled, and the logo SVG is a new vector trace of the AKENO "A".
They are decoration only: every title, value, badge and button is a real UI widget drawn on top.
The GPU picture is a generic illustration, not a photo or product image of any manufacturer.
The package's game-cover concept image is deliberately **not** shipped (it shows a third-party game
logo); games without local artwork get a neutral generated placeholder.

Fallback artwork (used only if an asset file is missing) is original and drawn in code
(`akeno/ui/art.py`, `akeno/ui/icons.py`). No fonts are shipped; AKENO uses the fonts installed with
Windows (Segoe UI).

## Downloaded on demand from official sources (pinned SHA-256, see `akeno/data/catalog.json`)

| Component | Version | License | Official source | What AKENO does with it |
|---|---|---|---|---|
| OptiScaler DLSS 5 (Skynizz fork of OptiScaler / OptiScaler_DLSSNR) | 1.3.0 | GPL-3.0 | https://github.com/Skynizz/optiscaler-dlss5 | Copies `OptiScaler.dll`, `nvngx.dll_dlssnr.dll` and a configured `OptiScaler.ini` into a game folder after user confirmation. Its bundled `.bat`/`.py` installers are never executed. |
| DLSS5-Feeder | 1.19.0 | MIT | https://github.com/jlrouzies-fr/DLSS5-Feeder | Copies the ReShade add-on (and the 64-bit helper for 32-bit games) plus `DLSS5_Feed.fx`. `Verify-DLSS5Feeder.ps1` and the `.bat` launchers are never executed. |
| Intel PresentMon (console) | 2.6.0 | MIT | https://github.com/GameTechDev/PresentMon | Started as a separate process to record frame times for benchmarks. |

These programs are not part of AKENO. Their source code and licence texts are available from the
upstream repositories above; the OptiScaler package also contains its own `Licenses` folder.

## Referenced but never downloaded or redistributed

* **NVIDIA DLSS / NGX runtimes** (`nvngx_dlssnr.dll`, `nvngx_dlss.dll`) — proprietary NVIDIA software.
  AKENO only imports a copy that is already on the user's PC (NVIDIA driver, NGX model store, the user's
  games, or a file the user selects), and only if it identifies itself correctly and carries a valid
  NVIDIA Authenticode signature. Modified NVIDIA binaries are refused. NVIDIA's DLSS SDK licence does
  not allow stand-alone redistribution, so AKENO neither bundles nor downloads these files.
  Details: `docs/DEPENDENCIES.md`.
* **ReShade** (https://reshade.me, BSD-3-Clause) — required by DLSS5-Feeder; the user installs it (no
  reviewed, hash-pinned build in the catalog yet).
* **Neural add-ons for ReShade** (renodx-dlss5, Deep Fried Chicken) — detected for conflict checks only.
* **Dagherbou/OptiScaler_DLSSNR** — listed in the catalog for information; no reviewed release hash, so
  automatic installation is disabled.

## Projects studied for design (no code copied)

* RankFTW/RHI (GPL-3.0) — game discovery and mod-manager workflows.
* Skynizz/optiscaler-dlss5 `deploy/install.py` (GPL-3.0) — installation layout and the "never overwrite
  a file you did not install" rule. AKENO's installer is an independent implementation.
* ShyVortex/dlss-unlocked (MIT) — feature separation of Neural Rendering, Frame Generation and MFG.
* rocksdanister/lively (GPL-3.0) — wallpaper behind the desktop icons (the documented `Progman`/`WorkerW`
  behaviour of Windows), one process per wallpaper, the `livelyAudioListener` web API name. Desktop Studio is an
  independent implementation in Python/Qt; no Lively code was copied.

## Python packages used only for development and CI

pytest (MIT), pytest-qt (MIT), PyInstaller (GPL-2.0 with exception), Inno Setup (Inno Setup licence) for
the installer. None of these are part of the shipped application except the PyInstaller bootloader.


## v1.5 source research and diagnostic additions

The 19 upstream projects and their exact source revisions/licenses are documented in
`docs/AKENO_V1_5_UPSTREAM_RESEARCH.md`. Manager patterns were independently implemented;
no Recol AGPL implementation was copied into AKENO. The root licenses do not license
NVIDIA runtime DLLs, Discord-only NR consumers, shaders/assets or every bundled binary.
Public AKENO artifacts include none of those user-supplied proprietary components.

The optional NR diagnostic counter header and preparation/build tools are AKENO GPL-3.0-or-later
source additions to the pinned GPL Skynizz backend. Preserve upstream GPL/copyright/notices
and corresponding source obligations when distributing a derivative. The public compile
artifact contains source additions and build records, not a runtime bundle. Local component
and diagnostic manifests declare local permission/provenance, not malware-free certification.
The pinned Feeder host retains its MIT/component notices; PresentMon remains MIT; ReShade
and shader import requires separate user-supplied license/permission metadata.


## v1.6 diagnostic and research additions

The optional source instrumentation remains GPL-3.0-or-later, based on the exact
Skynizz/optiscaler-dlss5 GPL source commit fc702f8ca22f97b01bd6a02f3386bffb3484fc35.
The graphical builder produces private local artifacts and keeps the complete
checkout/submodule identities; no NVIDIA NR runtime or compiled derivative is
redistributed in public AKENO builds. The copied bundled header is AKENO's own
identical GPL addition. The upstream program's dependency notices/licenses still apply.
FiveM-DLSS5 (MIT wrapper) and official Cfx documentation were research references;
no third-party installer, proprietary snapshot, NR consumer or motion shader was copied.
See docs/AKENO_V1_6_OFFICIAL_RUNTIME_RESEARCH.md and AKENO_V1_6_FIVEM_FEASIBILITY.md.
