# Development progress (hand-over document)

> **Read this first in every new session**, then `git log --oneline -20`.
> GitHub is the source of truth — never rely on chat memory.

## Branches

| Branch | Purpose |
|---|---|
| `claude/affectionate-newton-cdn1tp` | **Active development branch** (v1.1 redesign + dependency automation) |
| `claude/eager-cori-r2l5ir` | Previous v1.0.0 work (d1611da). Fast-forwarded into the active branch, untouched since. |
| `main` | Initial commit only |

## Last pushed commit

See `git log -1 origin/claude/affectionate-newton-cdn1tp`. (Updated at each checkpoint below.)

## Session log

### Session 2 (2026-10-20) — v1.1 upgrade

Task brief: integrate the `AKENO_DLSS5_Design_Assets.zip` artwork, fix layout/scaling, improve NVIDIA
runtime discovery, automate lawful dependency acquisition, redesign the install page as a grouped wizard,
add regression tests, build Windows artifacts. Preserve all v1.0 features.

| Step | State |
|---|---|
| Baseline: fast-forward active branch to d1611da, push | Done |
| Baseline tests (Linux, offscreen): **74 passed** | Done |
| Extract design assets → `akeno/data/assets/` | In progress |
| Asset loader (`akeno/ui/assets.py`) with source + PyInstaller paths | Pending |
| Header / sidebar / hardware / recommendation / profile / security / actions redesign | Pending |
| Layout fixes (fit 1920x1080 without scroll, scaling, ultrawide) | Pending |
| NVIDIA runtime discovery (DriverStore, System32, NGX model store, game folders) | Pending |
| Dependency resolver service + "Resolve Dependencies" | Pending |
| Install wizard (System / Game / Dependencies / Installation sections) | Pending |
| GPU-generation support matrix (RTX 20/30/40/50, SR vs NR vs FG vs MFG) | Pending |
| Tests: assets, DPI, resolver, offline, corrupt packages, rollback, repeat install | Pending |
| CI: packaged GUI startup + asset check, screenshots | Pending |
| README / STATUS update | Pending |

## Current unfinished work

* (updated at each checkpoint)

## Known bugs / limitations

* Not verified on a physical NVIDIA RTX GPU (dev container has none; CI runner has none).
* `nvngx_dlssnr.dll` is NVIDIA proprietary. AKENO must not bundle or download it from unofficial hosts.

## Test results

| Date | Where | Result |
|---|---|---|
| 2026-10-20 | Linux container, offscreen Qt, Python 3.13 / PySide6 6.8.1 | 74 passed (baseline, before changes) |

## Files currently being modified

* `docs/DEVELOPMENT_PROGRESS.md`

## Exact next steps

1. Copy curated assets from the design ZIP to `akeno/data/assets/` (backgrounds, branding, icons, illustrations).
2. Add `akeno/ui/assets.py` (cached QPixmap/QSvgRenderer loading, `resource_dir()` aware).
3. Rework `main_window.py` header + sidebar with the artwork.

## Design / architecture decisions

* Assets live under `akeno/data/assets/` so the existing PyInstaller `datas` entry (`akeno/data`) bundles them
  and `paths.resource_dir()` resolves them from source and from `_MEIPASS`.
* Artwork is decoration only — all text, values, badges and buttons stay real Qt widgets (per asset README).
