# Desktop Studio 2.0 (AKENO Gaming Suite 3.0)

Desktop Studio is the DESKTOP section of the suite. It covers animated wallpapers, the AKENO Dock, desktop widgets
and the Theme Workshop. It runs entirely on the Windows PC; nothing of it is sent to the license server.

How the Windows integration was designed, what was learned from Lively, ScreenPlay, Rainmeter and weebp, and the
licensing review: [DESKTOP_OPEN_SOURCE_RESEARCH.md](DESKTOP_OPEN_SOURCE_RESEARCH.md).

## Processes

| Process | Started by | Job |
|---|---|---|
| `AKENO.exe` (the suite) | the user | edits `%LOCALAPPDATA%\AKENO\desktop\desktop.json`, starts the runtime, reports running games |
| `AKENO.exe --desktop` (**Desktop Runtime**) | the suite when something is switched on, the tray, or Windows start (optional) | one per Windows user (lock file + local socket); dock, widgets, pause rules, tray icon, supervises renderers |
| `AKENO.exe --wallpaper-renderer --monitor … --server …` | the runtime, one per monitor | shows that monitor's wallpaper behind the desktop icons, or nothing |

The parts talk over a local socket (a named pipe on Windows) that only the current user can open
(`QLocalServer.UserAccessOption`), one JSON object per line.

### The runtime does not depend on the AKENO window

- **Closing the suite** never stops the runtime. Wallpapers, the dock and the enabled widgets stay. When the suite
  opens again, it connects to the running runtime (the status pill shows "Running").
- **Tray icon** (Desktop Runtime tab → "Tray icon for wallpapers, dock and widgets", on by default). Its menu:
  - Open AKENO: brings the running suite to the front, or starts it;
  - Pause wallpapers;
  - Stop wallpaper;
  - Show the dock;
  - Show widgets;
  - Quit Desktop Runtime.
- **Start with Windows** (optional, off by default): an `AKENO Desktop Runtime` value under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. No admin rights and no scheduled task are needed. Switching
  it off removes the value.
- **Stop actions are separate:**

  | Action | What stops | What stays |
  |---|---|---|
  | Stop wallpaper (page, tray) | the wallpapers. Windows shows its own wallpaper again | the dock, the widgets, the library and the playlists |
  | Pause wallpapers (tray) | decoding; the last frame stays | everything; the page shows "paused from the tray menu" |
  | Dock / widgets switches | that feature only | everything else |
  | Quit Desktop Runtime | the runtime process | the whole configuration; applying a wallpaper starts it again |

### Crash isolation and recovery

| Renderer exit | Meaning | Runtime reaction |
|---|---|---|
| crash or kill | broken file, codec crash, WebGL driver reset | restart after 2, 5, 15, 60 s; after five crashes within ten minutes the monitor stays on the Windows wallpaper ("Stopped after repeated crashes") |
| `2` `NO_HOST` | the runtime's socket closed | none needed: renderers never outlive the runtime, so no orphan wallpaper windows |
| `3` `EMBED_FAILED` | no safe place behind the icons (see below); the window was never shown | retry after 3, 10 and 30 s (Explorer may still be starting), then the Windows wallpaper stays and the page says why |
| `4` `DESKTOP_LOST` | the wallpaper lost its parent (Explorer restarted) | a new renderer after 2 s; after eight losses within ten minutes that monitor gives up |

A new wallpaper for a monitor gives it a fresh start. The runtime also ends by itself when:
- nothing is switched on;
- the local license no longer unlocks AKENO (checked every 10 minutes, offline);
- its user session ends.

## Wallpapers behind the icons

### How a wallpaper is attached

`akeno/desktop/winapi.py`, independent code following Microsoft's documented guidance:

1. Program Manager (`Progman`) is sent message `0x052C`, which makes Explorer split the icon view
   (`SHELLDLL_DefView`) from the wallpaper. Nothing of Explorer is replaced, patched or injected.
2. **The layout is detected by structure, not by Windows version.**
   - **Raised desktop** (Windows 11 24H2+): `Progman` has `WS_EX_NOREDIRECTIONBITMAP`, and the icon view and the
     wallpaper `WorkerW` are its children. Before `SetParent`, the wallpaper window becomes a `WS_CHILD` with
     `WS_EX_LAYERED` (alpha 255) and `WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT`. It is then placed
     directly below `SHELLDLL_DefView`, and the `WorkerW` is kept at the bottom.
   - **Classic** (Windows 10, Windows 11 before 24H2, Windows Server): the wallpaper becomes a child of the
     `WorkerW` that sits behind the `WorkerW` holding the icons.
   - **Alternative** (Desktop Runtime tab → Desktop layer → "Inside Windows' wallpaper window"): on the raised
     desktop, a child of the wallpaper `WorkerW`. This is for PCs where the default shows nothing.
3. **Monitors are matched by position.** Qt 6 names screens by model ("DELL U3223QE", "HyperVMonitor"), Win32 by
   device (`\\.\DISPLAY1`). `winapi.monitor_of()` maps the configuration's Qt screen name to the Win32 monitor at
   the same physical position. In 3.0 a name mismatch skipped attaching and showed a plain window over the icons.
   Two monitors of the same model share Qt's name; there the key gets the monitor's position
   (`winapi.screen_key()`: "DELL U2720Q @2560,0"). Unique names stay as they were, and a setting saved under the
   bare model name still applies to both until one is changed. Before, the second identical monitor never got a
   wallpaper. A monitor that matches no Windows monitor gets no wallpaper (the renderer refuses, see 5).
4. **Verified before it is shown.** `winapi.verify()` checks:
   - the parent;
   - the z-order (below the icon view, or behind the icon host);
   - that the window covers the monitor within 2 px;
   - visibility.
5. **Fail-safe.** If no layer exists, or verification fails after five tries, the window is hidden and never shown,
   and the renderer exits with `EMBED_FAILED`. There is no fallback window: a wallpaper that cannot sit behind
   the icons is not shown at all. `SetParent(NULL)` is never called, so the wallpaper never becomes a top-level
   window over the desktop.
6. **Explorer restart.** The renderer checks every 2 s that it is still attached. If not, it hides itself and exits
   with `DESKTOP_LOST`.
7. **Qt replacing the window.** Qt destroys and recreates the native window of a window that is already shown when
   the first GPU-drawn widget joins it. The page of a web wallpaper does that a moment after it starts drawing, both
   after switching to a web wallpaper and in a renderer that starts with one. The new window is an ordinary
   top-level window. Qt reports it (`WinIdChange`) before it shows it; the renderer then attaches it behind the
   icons at once and verifies it after Qt has shown it. Showing it, Qt writes its own window style again (the
   Windows runner measured `WS_CHILD` gone, parent kept); every check that finds a problem therefore attaches the
   window once more, styles included, before it gives up. If that fails too, the window is hidden and the
   renderer exits with `EMBED_FAILED`. See "The desktop window went away" below.
8. **No `SPI_SETDESKWALLPAPER` on the raised desktop.** That call would destroy Explorer's `WorkerW`. On the classic
   layout it redraws Windows' own wallpaper after the last renderer stops.
9. **Clicks go through.** The window is `WS_EX_TRANSPARENT` and never takes the focus. Desktop clicks reach the icon
   view; web wallpapers receive the mouse position over the socket instead.

Widgets are never attached into Explorer. They stay ordinary windows at the bottom of the z-order. On **Win+D** the
runtime raises them just above the desktop and puts them back afterwards. The dock is an always-on-top tool window
that never takes the focus. It fades out while a game or fullscreen program runs ("Hide during games", on by
default).

### Types, playlists, pause rules

- **Types:** pictures (JPG, PNG, BMP, WebP), GIF animations, videos (MP4, WebM, MKV, MOV through Qt Multimedia and its
  FFmpeg backend) and web wallpapers (an HTML file or a folder with `index.html`, rendered by Qt WebEngine/Chromium
  with WebGL).
- **Per monitor:** each monitor has its own playlist (Playlists & schedule tab):
  - one wallpaper;
  - change every N minutes, per entry;
  - a daily schedule ("from 19:30 show entry 2");
  - a fixed entry per Windows virtual desktop, read from Explorer's `VirtualDesktops\CurrentVirtualDesktop`
    registry value.
- **Pause rules** (Playback & performance tab, each can be switched off). Paused wallpapers keep their last frame
  and stop decoding. The rules are:
  - a library game runs (reported by the suite, or found by the runtime itself while the suite is closed);
  - a fullscreen program is in front (`SHQueryUserNotificationState`);
  - a window covers the monitor;
  - battery;
  - a remote desktop session;
  - a CPU limit;
  - "paused from the tray menu".
- **Frame limit:** web wallpapers get a `requestAnimationFrame` limit (5–144 FPS, default 30).
- **Audio-reactive:** web wallpapers receive the playback level of the default audio device (Core Audio
  `IAudioMeterInformation`), as `livelyAudioListener(array)` and `akenoAudio(array)`. This is a level history, not
  a frequency spectrum.
- **Interactive:** while the desktop has the focus, web wallpapers get `mousemove` events and `akenoMouse(x, y)`.

## Wallpaper library

The Library tab replaces the file list of 3.0 with a visual gallery.

- **Library** (`akeno/desktop/library.py`, `%LOCALAPPDATA%\AKENO\desktop\library.json`):
  - Files stay where they are. Removing an entry never deletes the file.
  - Duplicates are recognised by content fingerprint, not by name.
  - Missing files stay listed and are marked.
  - Folders are added recursively; a web wallpaper folder counts as one wallpaper.
  - 3.0 playlists are migrated into the library automatically, once.
  - Each entry keeps: title, type, format, resolution, length, size, date added, favourite, collections, source,
    author and license.
- **Thumbnails** (`akeno/desktop/thumbs.py`), made in the background and cached in
  `%LOCALAPPDATA%\AKENO\desktop\thumbs`:
  - **Pictures and GIFs:** `QImageReader` decodes at 480×270, on two threads. A 4K picture is never decoded at full
    size.
  - **Videos:** Qt Multimedia opens the file and takes the frame at 25 % of its length, then stops. Resolution and
    length come along. Only one video is decoded at a time, with a 12 s limit.
  - **Web wallpapers:** the picture their package brought, or the frame the renderer captured 5 s after it first
    showed the page.
  - **Cache key:** path, size and modification time. A changed file gets a new thumbnail; an unchanged one is never
    decoded twice.
  - **Broken files:** get a "No preview" placeholder, never a crash.
- **Gallery** (`QListView` in icon mode with its own delegate, so thousands of entries scroll smoothly):
  - Each card shows the real thumbnail, title, type badge, resolution, length, favourite star and an "active"
    mark.
  - Filters: All, Videos, Pictures, GIFs, Web, Favourites.
  - Search in titles, authors and collections.
  - Sort by recently added, name, favourites first, type or size.
  - Files can be dropped onto the gallery.
- **Preview panel:**
  - Videos play muted until you unmute; you can play and pause.
  - Pictures and GIFs are shown as they are.
  - Also on the panel: details and source, favourite, collections, fullscreen preview (Esc closes it), and apply
    to a chosen monitor or to all monitors.
  - Playback stops as soon as the page is left or another entry is selected. Nothing decodes in the background.
- **Monitor map:** the monitors are drawn as in Windows' display settings, each with the thumbnail of what runs
  there. Click a monitor to choose it.
- **Import:**
  - files;
  - a folder;
  - a web wallpaper folder;
  - a **Lively wallpaper package** (`.zip` with `LivelyInfo.json`). Only types `web`, `webaudio`, `video`, `gif`
    and `picture` are accepted. Packages that would run a program (`app`, Unity, Godot, BizHawk, URL and stream
    types), executables, absolute paths and `..` entries are refused before anything is written.

### Discover: finding wallpapers

Wallpapers → **Discover** (or **Browse online**; `akeno/desktop/sources.py`) finds animated wallpapers.

| Source | How AKENO uses it | Why |
|---|---|---|
| **Pixabay** | Search inside AKENO through Pixabay's official API (https://pixabay.com/api/docs/), with your own free API key. You get topics (gaming, anime, neon, space…), the kind (animations or filmed) and the quality (4K when available, 1080p, 720p). Each result shows a preview, its length, size and creator, and **Add** downloads it into the wallpaper library, with its source, creator and license, and selects it in the gallery. | Pixabay offers free videos under the Pixabay Content License through an interface meant for apps. |
| **MotionBGs, MyLiveWallpapers, DesktopHut** | **Open website** in the browser. You download there under the site's own terms, then add the file from **Recent downloads**. | These sites offer no interface for apps, and much of their content shows third-party characters whose rights AKENO cannot check. AKENO does not download from them, scrape them, show their content or redistribute it. |
| **Recent downloads** | Lists video files (MP4, WebM, MKV, MOV) and GIFs from your Downloads folder of the last two weeks. **Add** copies the file into AKENO's wallpaper library (`%LOCALAPPDATA%\AKENO\wallpapers`), so tidying the Downloads folder keeps the wallpaper. | |

How AKENO handles Pixabay:
- **The API key** is stored encrypted for your Windows account (DPAPI), never in settings or in the program, and is only sent to `pixabay.com`. Forget API key removes it.
- **Network:** only HTTPS, only `pixabay.com` (search) and `cdn.pixabay.com` (previews and videos). A redirect anywhere else is refused.
- **Requests** are cached for 24 hours and kept under Pixabay's limit of 100 a minute. Nothing is downloaded until you choose a video, and never in bulk.
- **Downloads:**
  - A file larger than Pixabay announced is refused.
  - A file that does not start like an MP4 is refused.
  - Next to each video, a `.json` note records where it came from, its creator and its license.
- **Credit:** results say that they come from Pixabay and name each creator, as Pixabay's API documentation asks.

**Testing.** The Pixabay client is tested against answers in the documented format through a fake transport (`tests/test_wallpaper_sources.py`). It was not tested against the live Pixabay service, because the development environment cannot reach it.

**Playback.** Added videos play like any other:
- looped by Qt Multimedia (FFmpeg backend);
- one renderer process per monitor;
- paused, with no CPU or GPU time, by the pause rules above (a game, a fullscreen program in front, a covered monitor, battery, remote desktop, CPU load).

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

The dock is an ordinary always-on-top window, docked to a screen edge or floating. It never hides or replaces the
Windows taskbar.

- **Dock page:**
  - a live preview that follows every setting;
  - presets (AKENO, Glass, Midnight, Minimal);
  - a style (Glass with Windows 11 acrylic, Solid, Icons only, Accent);
  - icon size, opacity, edge and screen;
  - auto-hide, labels, animations, and hide during games.
- **Pinned items:**
  - Items are programs, shortcuts (`.lnk` targets are read from the documented [MS-SHLLINK] LinkInfo), folders and
    groups.
  - You reorder them by dragging and add them by dropping files on the list or on the dock.
  - The list is as tall as its items.
- **At runtime:**
  - a running dot under running programs (psutil, every 4 s);
  - a short launch bounce (when animations are on);
  - auto-hide slides out after 1.5 s and returns at the screen edge;
  - the dock fades out during games and fullscreen programs.

## Widgets

The widget types are:
- clock, calendar;
- CPU, GPU, temperature, RAM, VRAM;
- network, drives;
- FPS, shortcuts, music, visualizer;
- notes, to-do;
- weather, uptime, downloads, license.

- **Widgets page:**
  - **Layout canvas:** the monitors are drawn to scale, with every widget rendered as it looks on the desktop. You
    drag and resize widgets there; positions snap to an 8 px grid. Each widget also has a card with an on/off
    switch and settings.
  - **Look:** a style per widget (Dark, Glass, Solid, Accent) and an accent colour.
  - **Profiles:** desktop profiles are kept; export and import are available.
  - **Lock:** prevents moving widgets on the desktop.
- **Previews** use one real sensor reading. A value the PC does not report (for example the GPU without an NVIDIA
  card) shows "—" and is never invented.
- **Values are only what the PC reports:**
  - **GPU, VRAM and GPU temperature:** from `nvidia-smi`. Without an NVIDIA card the widget says so.
  - **CPU temperature:** shown where psutil provides it (Linux). Windows has no documented CPU temperature without a
    vendor tool, so the widget says that.
  - **FPS:** the last Benchmark capture (PresentMon), read only from the suite's database. It is never an estimate.
  - **Music:** controls the active player with the media keys and shows the playback level. It does not read the
    title.
  - **Weather:** off until you enter a place. It uses Open-Meteo, at most every 30 minutes; only rounded coordinates
    are sent after geocoding; the data is CC BY 4.0.
- **While gaming:** widgets stop updating while a game or a fullscreen program runs.

## Theme Workshop 2.0

The Themes page shows the installed themes as cards with their preview picture.

- **Save current desktop:** stores the wallpapers, dock look, widget layout and accent as a theme, with a preview
  composed from the real wallpaper thumbnails and the widgets.
- **Apply:**
  - Before a theme replaces the desktop, the current state is saved (`previous-desktop.json`). **Restore previous
    desktop** brings it back with one click.
  - Dock pins always stay yours.
- **Edit** (name, author, description, license), **Duplicate**, **Export** (`.akenotheme`), **Remove**.
- **Import** checks the archive before anything is written (below). A theme never runs code.

**Format.** A theme is one `.akenotheme` file, a ZIP archive with these parts:
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


Theme files from AKENO 3.0 (format version 1) import unchanged. The new fields are optional:
- the accent colour;
- the dock style, labels and animations;
- the widget style and colour.

## Licenses

See [DESKTOP_OPEN_SOURCE_RESEARCH.md](DESKTOP_OPEN_SOURCE_RESEARCH.md). In short:
- no code was copied from Lively (GPL-3.0), ScreenPlay (AGPL-3.0 / proprietary), Rainmeter (GPL-2.0) or weebp
  (Unlicense);
- the `LivelyInfo.json` format is read for import only;
- Qt WebEngine and Qt Multimedia (FFmpeg) ship with PySide6 under the LGPL-3.0;
- no third-party wallpapers are bundled. The test media in `tests/fixtures/media` are FFmpeg test patterns.

## What was verified, and where

**Automated, Linux (offscreen Qt, real processes, real media):**

| Area | Tests |
|---|---|
| Embedding | `tests/test_desktop_embedding.py`: the attach/verify/fail-safe logic against a model of both Explorer layouts (classic and raised): styles before `SetParent`, z-order, no window when no layer, no `SetParent(NULL)`, Explorer restart detection; a real `WallpaperWindow` whose native window Qt replaces (attached again before it is shown, style rewritten by Qt repaired, refused when no layer exists); two monitors of the same model |
| Runtime | `tests/test_desktop_runtime.py`: exit codes and retries, Explorer-restart handling, the separate stop actions, the tray menu, the autostart value (fake registry); `tests/test_desktop.py`: the runtime with real renderer processes (crash restart, game pause, the suite disconnecting while the wallpaper keeps running) |
| Library | `tests/test_desktop_library.py`: real thumbnails from a 4K PNG, GIF, H.264 MP4 and VP9 WebM; durations and resolutions; cache hits; a broken file; Lively import and every refusal |
| Pages | `tests/test_desktop_ui.py` and `tests/test_desktop.py`: the gallery showing real frames, the preview playing and stopping, favourites, filters, search, drop, fullscreen preview, widget canvas drag with snapping, dock preview, themes save/apply/restore/duplicate/edit/export |

**Automated, Windows CI:**
- `tools/desktop_check.py` runs the packaged `AKENO.exe` on the GitHub Windows runner. Phases: picture → video →
  web → picture again (one renderer reloading), a renderer that starts with the web wallpaper, Windows' own
  wallpaper redrawn next to a running renderer (`refresh_wallpaper`), the suite connecting and disconnecting, and
  a real Explorer restart (`taskkill`, then `explorer.exe`).
- It measures from outside:
  - the parent chain and z-order of each wallpaper window;
  - whether it is a top-level window;
  - whether clicks at 25 points reach it;
  - every 50 ms during the whole run: whether any window of a renderer process is a visible top-level window;
  - every renderer exit the host did not ask for (only the Explorer restart may cause one).
- Result for commit `1e551b1` (run 40143293383, Windows Server 2022, build 20348, classic layout, one monitor
  "HyperVMonitor" = `\\.\DISPLAY1`, 1024×768): all phases pass. Switching to the web wallpaper made Qt replace
  the window once; it was attached before it was visible, and the check after showing put back the style and size
  Qt had reset. `refresh_wallpaper` left the running wallpaper and Explorer's `WorkerW` in place. After the Explorer
  restart the renderer exited with `DESKTOP_LOST`, showed nothing while there was no desktop and came back behind
  the new icons. No renderer window was ever a visible top-level window.
- The runner is Windows Server with the **classic** desktop layout. Windows 11 24H2's raised desktop is **not**
  covered by CI. CI screenshots of the runner are not proof of what a gaming PC shows.

### "The desktop window went away" (investigated 2026-10-24)

The runner logged `wallpaper HyperVMonitor: the desktop window went away (Explorer restarted?)` once (run
40136602963, 22:22:27), about 4 s after the check switched the running renderer from the video to the web
wallpaper; the web wallpaper's page started 2.5 s later in a new renderer.
- Not an Explorer restart, and not `refresh_wallpaper()` of another renderer: the check runs one renderer process,
  which reloads its wallpaper, and no renderer had ended before the message.
- Cause: Qt replaces the native window of a shown window when the first GPU-drawn widget joins it (see "Qt
  replacing the window" above; reproduced with PySide6 6.8.1). The new window was an ordinary top-level window,
  so the renderer's check found it "gone" and the renderer restarted. Until then the window could cover the
  desktop.
- Fixed in `94f3301` and `1e551b1`; the regression tests and the runner measurement are listed above.

**Not verifiable in CI, so not verified until tested on a real PC:**
- the raised desktop of Windows 11 24H2+;
- icon visibility and clicks on a real Explorer;
- Explorer restarts on a real PC (the runner restart is measured, see above);
- Win+D and Alt+Tab;
- monitor and scaling changes;
- fullscreen games;
- the acrylic dock;
- video decoding on a real GPU.

## Physical-PC acceptance checklist

Run this on the Windows 11 gaming PC (ideally 24H2 or newer, high-resolution monitor) with a build of this branch.
Open Wallpapers → Desktop Runtime first; it shows the Windows build and the detected layer ("Windows 11 24H2 desktop (below the icon view)" or "classic desktop (WorkerW behind the icons)"). For each test, note pass or
fail and, on failure, the layer and the text of the Desktop Runtime tab.

Optional diagnostics:
- `python tools/desktop_check.py --exe <path>\AKENO.exe --out desktop-check` measures the window tree and clicks
  on that PC and writes `desktop-check\desktop-check.json`.
- It runs with its own settings folder and leaves the user's settings alone.

| # | Test | Expected |
|---|---|---|
| 1 | Library → Import → an MP4 → select it → Apply to the main monitor | the video plays on the desktop within a few seconds |
| 2 | Look at the desktop | the video is **behind** the desktop icons; icon labels are readable on top of it |
| 3 | Click, double-click, drag and right-click icons and the desktop | icons open, move and show their menus; the desktop context menu works |
| 4 | Taskbar, Start menu, notification area, Win key | all work; the wallpaper never covers them |
| 5 | Close the AKENO window (X) | the window closes |
| 6 | Look at the desktop and the tray | wallpaper, dock and enabled widgets stay; the AKENO tray icon is there |
| 7 | Tray → Open AKENO (or start AKENO) → DESKTOP → Wallpapers → Desktop Runtime | the status pill shows "Running"; the running wallpaper is marked in the gallery and on the monitor map |
| 8 | Wallpapers → Stop wallpaper (or tray → Stop wallpaper) | Windows' own wallpaper is back; dock and widgets stay |
| 9 | Apply the video again, then Task Manager → Windows Explorer → Restart | icons come back; within about 5 s the video is behind them again, or the Windows wallpaper stays. Never a window over the icons |
| 10 | Win+D twice; Alt+Tab | Win+D shows the desktop with the wallpaper and widgets and returns; Alt+Tab never lists a wallpaper window |
| 11 | Change the main monitor's scaling (100 % → 150 %), then resolution; connect or disconnect a second monitor | the wallpaper covers each monitor exactly, with no gap or offset; the monitor map follows |
| 12 | Start a fullscreen game (and one in borderless window mode) | the wallpaper pauses (Playback tab shows the reason), the dock fades out, widgets stop updating; all resume after the game |
| 13 | Import a new video into the library | a real frame of that video appears on its card within a few seconds; length and resolution are shown |
| 14 | Select the video | the preview plays muted; sound and play/pause work; it stops when you open another page |
| 15 | Themes → Save current desktop → change wallpaper and dock → apply another theme → Restore previous desktop | the saved desktop comes back: wallpaper, dock look, widget layout |
| 16 | Apply a picture, then a web wallpaper (an HTML folder), then the picture again; watch the screen while switching | no window flashes over the desktop or the icons; the web page appears behind the icons |
| 17 | Two monitors of the same model: apply different wallpapers to each | each monitor shows its own wallpaper |
| 18 | With a Windows slideshow or Windows Spotlight as background: apply a wallpaper, then Stop wallpaper | Windows' own background comes back; note whether the slideshow or Spotlight still changes afterwards |

**If test 2 or 3 fails:**
1. Wallpapers → Desktop Runtime → Desktop layer → "Inside Windows' wallpaper window", then repeat.
2. If that fails too, Stop wallpaper. Windows shows its own wallpaper and the desktop is fully usable.
3. Report the Windows version (`winver`) and `desktop-check.json` from the diagnostic.
