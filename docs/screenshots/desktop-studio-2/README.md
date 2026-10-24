# Desktop Studio 2.0 screenshots

Renders of the DESKTOP pages in German, 1680×980 window, made on Linux with Qt's offscreen platform and two
virtual monitors (1920×1080 and 1280×1024).

They show **the interface only**, with test-fixture data:
- gradient pictures generated for the screenshot;
- the FFmpeg test patterns from `tests/fixtures/media`;
- the container's own sensor values.

The thumbnails are real frames decoded from those files. The status says "Not running" because no desktop
runtime was started.

**These pictures are not proof that wallpapers sit behind the icons on Windows.** That needs the physical-PC
checklist in [DESKTOP_STUDIO.md](../../DESKTOP_STUDIO.md#physical-pc-acceptance-checklist). For the Windows
runner's measurements, see the `desktop-check.json` artifact of the CI run.

| File | Page |
|---|---|
| `library.png` | Wallpapers → Library: gallery with real thumbnails, preview player, monitor map |
| `playback.png` | Wallpapers → Playback & performance: playback settings and pause rules |
| `runtime.png` | Wallpapers → Desktop Runtime: stop/quit actions, start with Windows, tray icon, desktop layer |
| `dock.png` | Dock: live preview, presets, style, pinned items |
| `widgets.png` | Widgets: layout canvas, per-widget style and colour, widget gallery |
| `themes.png` | Theme Workshop: theme cards with composed previews, restore previous desktop |
