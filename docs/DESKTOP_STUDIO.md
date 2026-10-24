# Desktop Studio (AKENO Gaming Suite 3.0)

Desktop Studio is the DESKTOP section of the suite. It covers animated wallpapers, the AKENO Dock, desktop widgets and the Theme Workshop.
It runs entirely on the Windows PC; nothing of it is sent to the license server.

## Processes

| Process | Started by | Job |
|---|---|---|
| `AKENO.exe` (the suite) | the user | edits `%LOCALAPPDATA%\AKENO\desktop\desktop.json`, starts the host, reports running games |
| `AKENO.exe --desktop` (host) | the suite, when something is switched on | one per Windows user (lock file + local socket); dock, widgets, pause rules, supervises renderers |
| `AKENO.exe --wallpaper-renderer --monitor … --server …` | the host, one per monitor | shows that monitor's wallpaper behind the desktop icons |

The parts talk over a local socket (a named pipe on Windows) that only the current user can open (`QLocalServer.UserAccessOption`).
Each message is one JSON object per line.

Crash isolation works like this:
- A broken wallpaper, a codec crash or a WebGL driver reset ends one renderer, never the suite or the host.
- The host restarts a renderer after 2, 5, 15 and 60 seconds.
- After five crashes within ten minutes, that monitor stays on the Windows wallpaper. The Wallpapers page then shows "Stopped after repeated crashes".
- A new wallpaper for that monitor gives it a fresh start.

The host ends by itself in four cases:
- nothing is switched on;
- the suite tells it to stop;
- the local license no longer unlocks AKENO (checked every 10 minutes, offline, from the stored lease or certificate);
- its last user session ends.

Windows then redraws its own wallpaper.

## Wallpapers

- **Types:** pictures (JPG, PNG, BMP, WebP), GIF animations, videos (MP4, WebM, MKV, MOV through Qt Multimedia and its FFmpeg backend) and web wallpapers (an HTML file or a folder with `index.html`, rendered by Qt WebEngine/Chromium with WebGL).
- **Per monitor:** each monitor has its own playlist.
- **Playlists:**
  - one wallpaper;
  - change every N minutes, per entry;
  - a daily schedule ("from 19:30 show entry 2");
  - a fixed entry for a Windows virtual desktop. The current virtual desktop is read, read-only, from Explorer's `VirtualDesktops\CurrentVirtualDesktop` registry value.
- **Behind the icons:** Program Manager (`Progman`) is sent message `0x052C`, which makes Explorer create a `WorkerW` window between the icons and the wallpaper. The renderer window becomes a child of that window.
  - On Windows 11 24H2 and later, the `WorkerW` is a child of `Progman`, and the wallpaper is placed directly below `SHELLDLL_DefView`.
  - Nothing of Explorer is replaced, patched or injected.
- **Pause rules:** each can be switched off; paused wallpapers keep their last frame and use no CPU or GPU time. The rules are:
  - a library game runs: reported by the suite, or found by the host itself from the library while the suite is closed;
  - a fullscreen program is in front (`SHQueryUserNotificationState`: busy, D3D fullscreen, presentation mode);
  - a window covers the monitor (maximised there, or borderless fullscreen);
  - the PC runs on battery;
  - a remote desktop session is active;
  - the CPU is busier than a limit.
- **Frame limit:** web wallpapers get a `requestAnimationFrame` limit (5–144 FPS, default 30).
- **Audio-reactive:** web wallpapers receive the playback level of the default audio device through Core Audio's `IAudioMeterInformation`.
  - This is a level history (128 values, 0..1), not a frequency spectrum. It needs no capture permission and no driver.
  - It is passed to `livelyAudioListener(array)` and `akenoAudio(array)`, so Lively audio wallpapers react, but to the level only.
- **Interactive:** while the desktop has the focus, web wallpapers get `mousemove` events and `akenoMouse(x, y)`.

### Web wallpaper sandbox

A web wallpaper is served from its own folder through the `akeno-wallpaper://wallpaper/` scheme (a Qt WebEngine URL scheme handler):
- It can read the files of that folder and nothing else. Paths that resolve outside the folder get "not found".
- Every request to another scheme (`http`, `https`, `file`, `ws`, …) is blocked by a request interceptor.
- The page runs in an off-the-record profile (no cookies or cache kept), with pop-ups and plug-ins off.

All of this was checked in a real Qt WebEngine (6.8.1, offscreen) with a test page:
- it loads its own `app.js` and fetches its own `data.json`;
- `fetch("https://example.com")` is blocked;
- an escape attempt (`%2e%2e/secret.txt`) gets a 404;
- `file:///etc/hostname` is refused.

## AKENO Dock

The dock is an ordinary always-on-top window, docked to a screen edge or floating. It never hides or replaces the Windows taskbar.
- **Items:** pinned programs, shortcuts (`.lnk` targets are read from the documented [MS-SHLLINK] LinkInfo), folders and groups (a click opens a menu).
- **Running dot:** shown under programs that are running (psutil process paths, every 4 s).
- **Auto-hide:** the dock slides out after 1.5 s away from it and comes back at the screen edge.
- **Look:** adjustable opacity, and the Windows 11 acrylic backdrop (`DWMWA_SYSTEMBACKDROP_TYPE`).
- **Editing:** drop files on the dock, or use its context menu.

## Widgets

The widgets are:
- clock, calendar;
- CPU, GPU, temperature, RAM, VRAM;
- network, drives;
- FPS, shortcuts, music, visualizer;
- notes, to-do;
- weather, uptime, downloads, license.

They sit below other windows. You drag them, resize them at the lower right corner, lock them, set their transparency, export or import them, and keep them in desktop profiles.

Values are only what the PC reports:
- **GPU, VRAM and GPU temperature:** from `nvidia-smi`. Without an NVIDIA card the widget says so.
- **CPU temperature:** shown where psutil provides it (Linux). Windows has no documented CPU temperature without a vendor tool, so the widget says that instead of guessing.
- **FPS:** the last Benchmark capture (PresentMon), read only from the suite's database. It is never an estimate.
- **Music:** controls the active player with the media keys and shows the playback level. It does not read the title (that would need the WinRT media session API).
- **Weather:** off until you enter a place. Only then does it ask Open-Meteo, at most every 30 minutes. It sends the place name once for geocoding, then only rounded coordinates. Open-Meteo needs no account or key, and its data is CC BY 4.0; the widget shows the attribution.
- **Paused updates:** widgets stop updating while a game or a fullscreen program runs.

## Theme Workshop

A theme is one `.akenotheme` file, a ZIP archive with these parts:
- **`theme.json`:** name, author, description, license, the wallpaper playlist and its settings, the dock style and the widget layout;
- **`preview.png`:** the picture shown in the library;
- **`media/…`:** the wallpaper files.

What an export leaves out:
- dock pins, which are your paths;
- the contents of notes, to-do, shortcuts and downloads widgets.

Import validates the archive before anything is written:
- **Paths:** no absolute paths, drive letters, backslashes, `..` or symbolic links.
- **Top level:** only `theme.json`, `preview.png`, `LICENSE*` and `README*`.
- **Files in `media/`:** only media, web wallpaper files and fonts. `.exe`, `.dll`, `.bat`, `.ps1`, `.lnk` and every other type are refused.
- **Size:** at most 5000 files and 4 GB, and no file compressed more than 200:1 above 10 MB (zip bombs).
- **`theme.json`:** every value is checked and clamped. Every playlist entry must exist in the archive with the kind its file extension says, and widget options keep plain values only.

The theme is unpacked into a staging folder and then moved into `%LOCALAPPDATA%\AKENO\themes\<id>` in one step.

## Licenses

- **Lively Wallpaper** (rocksdanister/lively, GPL-3.0) was studied for its documented approach:
  - the `Progman` `0x052C` `WorkerW` technique, a publicly known Windows behaviour;
  - one process per wallpaper;
  - the `livelyAudioListener` web API name.

  No Lively source code was copied. AKENO is GPL-3.0-or-later, which is compatible should code ever be shared.
- **Qt WebEngine** (Chromium) and **Qt Multimedia** (FFmpeg) ship as part of PySide6 under the LGPL-3.0. The FFmpeg libraries in PySide6 wheels are LGPL builds.

## What was verified, and where

On Linux, with the offscreen Qt platform and real processes, the following was checked (`tests/test_desktop.py`, 33 tests):
- the host with real renderer subprocesses: start, crash and restart after a kill, pause and resume through the suite's "game" message, live config changes, quitting without leftover processes;
- the web sandbox in a real WebEngine;
- every widget type built;
- dock editing and `.lnk` parsing;
- theme round trips and every import refusal;
- the four pages editing the configuration.

What only Windows can show, and what has **not been run in this environment**:
- placing the wallpaper behind the icons on a real Explorer (`WorkerW` on 21H2–24H2);
- `SHQueryUserNotificationState`;
- the Core Audio meter;
- the acrylic dock;
- the virtual desktop registry value;
- video decoding on a real GPU.

The Windows CI build runs the smoke test, which builds every page. These behaviours need a test on a Windows desktop before they are called verified.
