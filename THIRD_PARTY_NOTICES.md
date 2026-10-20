# Third-party notices and dependency inventory

AKENO DLSS 5 Manager is licensed under the GNU General Public License v3.0 or later (see `LICENSE`).
This file lists every third-party component AKENO bundles, downloads or interacts with, and how.

## Bundled in the Windows build

| Component | Version | License | How it is used |
|---|---|---|---|
| Python runtime | 3.12 | PSF License | Interpreter embedded by PyInstaller |
| Qt for Python (PySide6, shiboken6) and Qt 6 libraries | 6.8.1 | LGPL-3.0 (Qt and PySide6 open-source licence) | User interface. Shipped as separate, replaceable DLLs in the one-folder build, as the LGPL requires. Source: https://code.qt.io/cgit/pyside/pyside-setup.git |
| PyInstaller bootloader | 6.11.1 | GPL-2.0 with bootloader exception | Starts the bundled application |

AKENO's icons, logo, header banner, GPU illustration and cover placeholders are original artwork drawn
in code (`akeno/ui/art.py`, `akeno/ui/icons.py`). No third-party images or fonts are shipped; AKENO
uses the fonts installed with Windows (Segoe UI).

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
  AKENO only uses the copy the user's own NVIDIA driver installed (or a file the user selects), and
  only if it carries a valid NVIDIA Authenticode signature. Modified NVIDIA binaries are refused.
* **ReShade** (https://reshade.me) — required by DLSS5-Feeder; the user installs it.
* **Neural add-ons for ReShade** (renodx-dlss5, Deep Fried Chicken) — detected for conflict checks only.
* **Dagherbou/OptiScaler_DLSSNR** — listed in the catalog for information; no reviewed release hash, so
  automatic installation is disabled.

## Projects studied for design (no code copied)

* RankFTW/RHI (GPL-3.0) — game discovery and mod-manager workflows.
* Skynizz/optiscaler-dlss5 `deploy/install.py` (GPL-3.0) — installation layout and the "never overwrite
  a file you did not install" rule. AKENO's installer is an independent implementation.
* ShyVortex/dlss-unlocked (MIT) — feature separation of Neural Rendering, Frame Generation and MFG.

## Python packages used only for development and CI

pytest (MIT), pytest-qt (MIT), PyInstaller (GPL-2.0 with exception), Inno Setup (Inno Setup licence) for
the installer. None of these are part of the shipped application except the PyInstaller bootloader.
