# Desktop Studio 2.0: open-source research

This file records which open-source desktop customisation projects were studied for Desktop Studio 2.0, what was
learned from which source file, what that means for licensing, and what AKENO decided.

**Rule followed throughout:** the projects were read to understand how Windows behaves. No source code was copied
or translated line by line. AKENO's code (`akeno/desktop/*.py`, Python with ctypes and PySide6) was written
independently, from the documented Win32 behaviour and from the observations below.

## Projects and the files read

Each repository was cloned at the commit shown (shallow clone, no build, nothing executed).

| Project | Commit | License | Files read |
|---|---|---|---|
| [Lively Wallpaper](https://github.com/rocksdanister/lively) | `25a0a5f` (2026-09-29) | GPL-3.0 | `src/Lively/Lively/Core/WinDesktopCore.cs`, `src/Lively/Lively.Common/Helpers/WindowUtil.cs`, `src/Lively/Lively.Common/Helpers/Shell/DesktopUtil.cs`, `src/Lively/Lively/Core/Suspend/Playback.cs`, `src/Lively/Lively/Core/Watchdog/WatchdogProcess.cs`, `src/Lively/Lively/Helpers/WindowsStartup.cs`, `src/Lively/Lively.Common/Helpers/Shell/ThumbnailUtil.cs`, `src/Lively/Lively/ViewModels/LibraryPreviewViewModel.cs`, `src/Lively/Lively.Models/Enums/WallpaperType.cs`, `src/Lively/Lively.Models/LivelyInfoModel.cs`, `src/Lively/Lively.Common/Helpers/Storage/JsonStorage.cs`, sample `LivelyInfo.json` files |
| [ScreenPlay](https://github.com/kelteseth/ScreenPlay) | `07f13bc` (2025-10-18) | `AGPL-3.0-only OR LicenseRef-EliasSteurerTachiom` (SPDX header of the files) | `ScreenPlayWallpaper/src/windowsintegration.cpp`, `ScreenPlayWallpaper/src/winwindow.cpp` |
| [Rainmeter](https://github.com/rainmeter/rainmeter) | `303a73f` (2026-10-24) | GPL-2.0 | `Library/Skin.cpp` (z-positions), `Library/System.cpp` (desktop window layouts, show-desktop handling), `Library/GameMode.cpp` |
| [weebp](https://github.com/Francesco149/weebp) | `3f9e2d5` (2021-02-03) | Unlicense (public domain) | `src/weebp.c` |

Microsoft's own documentation was used for every API AKENO calls: `SetParent`, `SetWindowLongPtrW`
(`GWL_STYLE`, `GWL_EXSTYLE`), `SetLayeredWindowAttributes`, `SetWindowPos`, `ScreenToClient`, `WindowFromPoint`,
`SendMessageTimeoutW`, `SHQueryUserNotificationState`, the `TaskbarCreated` registered message and the
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` key.

## Findings

### 1. Two desktop layouts, and the one that hid the icons

All four projects use the same publicly known Explorer behaviour: sending message `0x052C` to the `Progman`
window makes Explorer create a `WorkerW` window that sits between the desktop icons (`SHELLDLL_DefView`) and the
Windows wallpaper. weebp (`weebp.c`) sends it with `wParam 0xD` and `lParam 0`/`1`, and falls back to `0, 0`.

There are two layouts:

- **Classic** (Windows 10, Windows 11 before 24H2, Windows Server): a top-level `WorkerW` holds
  `SHELLDLL_DefView`; the next top-level `WorkerW` behind it is the wallpaper layer. Rainmeter documents both
  orders in comments in `System.cpp`; Lively and weebp enumerate top-level windows to find it.
- **Raised desktop** (Windows 11 24H2 and later). Lively quotes Microsoft's guidance in `WinDesktopCore.cs`:
  - `Progman` is created with `WS_EX_NOREDIRECTIONBITMAP`;
  - `SHELLDLL_DefView` becomes a `WS_EX_LAYERED` child of `Progman`;
  - the wallpaper `WorkerW` is a child of `Progman` too, z-ordered under `DefView`;
  - an application must "create its own `WS_EX_LAYERED` child HWND that is z-ordered under the DefView window but
    above the WorkerW window", ideally with `SetLayeredWindowAttributes(bAlpha=0xFF)`.

  Lively detects this layout by the `WS_EX_NOREDIRECTIONBITMAP` style on `Progman`. In `TryAttachToDesktop` it
  first sets `WS_CHILD`, then the layered alpha 255, and only then calls `SetParent` (a comment notes that some
  windows "fail to apply WS_EX_LAYERED if attached after SetParent"). It then places the window directly below
  `DefView`, and `EnsureWorkerWZOrder` keeps the `WorkerW` last among `Progman`'s children.

  ScreenPlay (`windowsintegration.cpp`, `IsWindows24H2OrNewer`, `searchWorkerWindowToParentTo`) looks for the
  `WorkerW` among `Progman`'s children on 24H2, not among the top-level windows.

**Root cause in AKENO before 2.0** (`akeno/desktop/winapi.py` at `ed5d0a7`):

- `attach()` called `SetParent` into `Progman` without changing the window's styles. The window stayed a
  `WS_POPUP` without `WS_CHILD` and without `WS_EX_LAYERED`. On the raised desktop, such a window is not
  composited below the layered icon view, so it can end up in front of the icons.
- `attach()` never checked the result: no parent check, no z-order check, no size check.
- When no layer was found, `renderer.py` kept showing the frameless full-monitor window anyway, with
  `WindowStaysOnBottomHint`. This ordinary window covered the icons. That is exactly what the specification
  forbids.
- `refresh_wallpaper()` called `SPI_SETDESKWALLPAPER` on every layout. Lively skips that call on the raised
  desktop, with the comment "Otherwise will destroy the current WorkerW".
- `detach()` called `SetParent(hwnd, NULL)`, which turns the wallpaper back into a top-level window over the
  desktop for a moment.
- The position was computed from `SM_XVIRTUALSCREEN`, which matches the parent's client origin on the classic
  layout but not necessarily on the raised one.

### 2. Surviving Explorer and the main app

- Lively subscribes to the `TaskbarCreated` message (`WinDesktopCore.cs`, `WndProc_TaskbarCreated`) to notice an
  Explorer restart and re-attach every wallpaper.
- `WatchdogProcess.cs` runs a separate process that ends the wallpaper processes when Lively itself dies, so no
  orphan window stays on the desktop.
- `WindowsStartup.cs` registers start with Windows under the `Run` key.

### 3. Pausing

`Playback.cs` (Lively) pauses on:
- `SHQueryUserNotificationState` (busy, D3D fullscreen, presentation);
- a maximised or fullscreen foreground window per monitor;
- battery;
- a remote session.

Rainmeter (`GameMode.cpp`) has a game mode that unloads skins while a fullscreen program runs. AKENO already had
equivalent pause rules (`akeno/desktop/pause.py`); 2.0 adds a "paused from the tray" reason and hides the dock
during games.

### 4. Thumbnails and packages

- Lively's `ThumbnailUtil.cs` asks the Windows shell (`IShellItemImageFactory`) for a thumbnail;
  `LibraryPreviewViewModel.cs` captures a frame from the running wallpaper for its preview. The shell API is
  Windows-only and depends on installed codecs.
- `WallpaperType.cs`: the type is an enum (`app`, `web`, `webaudio`, `url`, `bizhawk`, `unity`, `godot`, `video`,
  `gif`, …). `JsonStorage.cs` uses Newtonsoft.Json without a string enum converter, so `LivelyInfo.json` files
  store the type as an integer: `web` = 1, `webaudio` = 2, `video` = 7, `gif` = 8, `picture` = 11.
- `LivelyInfoModel.cs` fields used by AKENO: `Title`, `Author`, `License`, `Contact`, `Type`, `FileName`,
  `Thumbnail`, `Preview`, `IsAbsolutePath`.

### 5. Rainmeter's desktop widgets

`Skin.cpp` `ZPOSITION_ONDESKTOP` keeps skins at `HWND_BOTTOM` and re-asserts that position when the desktop is
shown (`System.cpp` watches for "show desktop"). That is the behaviour AKENO's widgets already follow: below every
normal window, never attached into Explorer.

## Licensing

| Source | License | Effect on AKENO |
|---|---|---|
| Lively | GPL-3.0 | AKENO is GPL-3.0-or-later, so even shared code would be compatible. Still, nothing was copied: only the documented Explorer behaviour and the public `LivelyInfo.json` format were used. Reading a file format to import it needs no license. |
| ScreenPlay | AGPL-3.0-only, or a proprietary license | AGPL-3.0-only is not GPL-3.0-or-later. Mixing code would put network-use obligations on AKENO. **No code was used**, only the observation that the 24H2 `WorkerW` is a child of `Progman`, which Microsoft's note also states. |
| Rainmeter | GPL-2.0 (only) | GPL-2.0-only code cannot be combined with GPL-3.0 code. **No code was used**, only the z-order behaviour (`HWND_BOTTOM`), which is plain Win32. |
| weebp | Unlicense | No restrictions. Only the `0x052C` parameters (`0xD`, `0`/`1`) were taken as a fact, not code. |
| Qt (PySide6, Qt Multimedia with FFmpeg, Qt WebEngine) | LGPL-3.0 | Unchanged from before: dynamically linked, as shipped in the PySide6 wheels. |

No wallpapers, pictures or videos from any of these projects or their galleries were downloaded or bundled.
The media in `tests/fixtures/media` are test patterns generated with FFmpeg's `testsrc2` (see the README there).

## Decisions

1. **Detect the layout by structure, not by Windows version.** `winapi.find_layer()` checks
   `WS_EX_NOREDIRECTIONBITMAP` on `Progman` and then looks for `SHELLDLL_DefView` among `Progman`'s children
   (raised) or inside a top-level `WorkerW` (classic). If the icons were not split off, there is no layer, and so
   no wallpaper. A version check would fail on Insider builds and on Windows Server. As an option, a child of the
   wallpaper `WorkerW` can be used on the raised desktop instead (`"workerw"`). Lively and ScreenPlay offer the
   same alternative for systems where the default shows nothing.
2. **Raised desktop:** the wallpaper window gets these, in this order, before `SetParent`:
   - `WS_CHILD` (and loses `WS_POPUP`, caption and frame);
   - `WS_EX_LAYERED` with alpha 255, plus `WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT`.

   It is then placed directly below `DefView`, and `Progman`'s `WorkerW` is kept last. The classic layout parents
   into the wallpaper `WorkerW`.
3. **Verify, or do not show.** After attaching, `winapi.verify()` checks:
   - the parent;
   - that the window is below `DefView`, or that the icon host comes before the `WorkerW`;
   - the size, within 2 px;
   - visibility.

   If attaching fails after five tries, the renderer hides its window, never calls `SetParent(NULL)`, and exits
   with code 3 (`EMBED_FAILED`). The host retries after 3, 10 and 30 s and otherwise leaves Windows' own wallpaper.
   There is no fallback window.
4. **No `SPI_SETDESKWALLPAPER` on the raised desktop.** It is skipped as Lively skips it, so Explorer's `WorkerW`
   survives.
5. **Explorer restarts:**
   - the renderer re-checks its parent every few seconds (`still_attached`);
   - on loss it hides and exits with code 4 (`DESKTOP_LOST`);
   - the host waits 2 s for Explorer and starts a new renderer;
   - after eight losses within ten minutes it gives up for that monitor.

   This avoids hooking `TaskbarCreated` in a second process.
6. **Runtime independent of the window:** closing the AKENO window never stops the desktop runtime (a separate
   `--desktop` process). The runtime has:
   - its own tray icon (open AKENO, pause/resume wallpapers, stop wallpapers, dock and widgets on/off, quit);
   - an optional HKCU `Run` entry (no admin, no scheduled task).

   The renderers exit by themselves when the host's socket closes (exit code 2, `NO_HOST`), which plays the role
   of Lively's watchdog without an extra process.
7. **Thumbnails:** Qt itself, cross-platform and testable. AKENO does not use the Windows shell:
   - `QImageReader` decodes pictures at thumbnail size;
   - `QMediaPlayer` + `QVideoSink` grab a video frame at 25 % of its length, one video at a time;
   - web wallpapers show the frame the renderer captured the first time it showed the page.

   The cache is keyed by path, size and modification time.
8. **Lively import:** only types that are media or sandboxed web pages are accepted: `web`, `webaudio`, `video`,
   `gif`, `picture`. These are refused, because they run programs:
   - `app`, `unity`, `godot`, `bizhawk`;
   - `url` and streams.

   Archives with executables, absolute paths or `..` entries are refused before anything is written.
9. **Themes never run code.** `.akenotheme` stays a data-only ZIP (see `DESKTOP_STUDIO.md`). The new fields are
   optional, so version-1 themes from 1.x still import.
