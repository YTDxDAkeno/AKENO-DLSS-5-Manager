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
| Design assets → `akeno/data/assets/` (+ `akeno/ui/assets.py` loader, source + PyInstaller) | Done (79732d8) |
| Theme = design tokens (`akeno/ui/theme.py`) | Done |
| Header / sidebar / dashboard redesign, pinned action footer, density levels, ultrawide cap | Done (d62707e) |
| NVIDIA runtime discovery (DriverStore, System32, NGX store .bin, driver pkg .dl_, games, RHI, Downloads) | Done (103e9f9) |
| Dependency resolver service + "Resolve Dependencies" | **Next** |
| Install wizard (System / Game / Dependencies / Installation sections) | Pending |
| GPU-generation support matrix (RTX 20/30/40/50, SR vs NR vs FG vs MFG) + log-based verification | Pending |
| Hardware page: use GPU illustration | Pending |
| Tests: DPI, resolver, offline, corrupt packages, rollback, repeat install, packaged GUI start | Pending |
| CI: packaged GUI startup + asset check, screenshots | Pending |
| README / STATUS / THIRD_PARTY_NOTICES update | Pending |

## Research results (2026-10-20) — keep, they drive the design

* `nvngx_dlssnr.dll`: "the model ships in driver packages" (Skynizz `OptiScaler/dlssnr/README.md`). It is **not**
  in NVIDIA's official DLSS SDK (`raw.githubusercontent.com/NVIDIA/DLSS/main/lib/Windows_x86_64/rel/` has
  `nvngx_dlss.dll`, `nvngx_dlssd.dll`, `nvngx_dlssg.dll`, no `nvngx_dlssnr.dll` → 404). No official stand-alone
  download exists → AKENO only discovers + imports a signed local copy. Guided path when absent: update the NVIDIA
  driver (nvidia.com / NVIDIA App), then Resolve again.
* NVIDIA RTX SDK license (DLSS SDK `LICENSE.txt`): developers may distribute SDK files "as incorporated in object
  code format into a software application"; "may not distribute ... the SDK as a stand-alone product". AKENO
  (GPL) therefore neither bundles nor downloads `nvngx_dlss.dll`; it uses NVIDIA-signed copies already on the PC
  (most DLSS games ship one; NGX model store).
* DLSS5-Feeder README: NVIDIA's **signed** `nvngx_dlssnr.dll` 310.8.0 creates the neural feature (NGX feature 18)
  on **RTX 50 only**; RTX 20/30/40 fail with `feature 18 create failed with 0xbad00001` (issue #131). The
  community route for older GPUs is a **modified** NVIDIA DLL from Discord → AKENO refuses it (signature).
  Skynizz numbers were measured on an RTX 4070 (runtime provenance not stated).
* Skynizz installer looks for the runtime in the game folder and `%LOCALAPPDATA%\RHI\DLSS-NR`.
* Log markers (DLSS5-Feeder README): success `neural model (feature 18) loaded`, `feature 18 created`,
  `inline feature 18 evaluation succeeded`; failure `feature 18 create failed`, `0xbad00001`,
  `NEURAL MODEL NOT CREATED`, `the DRIVER answered the NGX probe`, `not a DLSS-NR fork`.
* `dlss5-feed-host64.exe --test` without a consumer only tests DLSS DLAA (no neural pass) → not an NR verifier.
* RHI deploys `nvngx_dlssnr.dll` from its own manifest (third-party redistribution) → never a download source.

## Current unfinished work

* Dependency resolver (`akeno/deps/`): plan + resolve actions, consent, progress; wire into services + UI.

## Known bugs / limitations

* Not verified on a physical NVIDIA RTX GPU (dev container has none; CI runner has none).
* `nvngx_dlssnr.dll` is NVIDIA proprietary. AKENO must not bundle or download it from unofficial hosts.

## Test results

| Date | Where | Result |
|---|---|---|
| 2026-10-20 | Linux container, offscreen Qt, Python 3.13 / PySide6 6.8.1 | 74 passed (baseline, before changes) |
| 2026-10-20 | same, after redesign + runtime discovery (103e9f9) | 87 passed |

## Files currently being modified

* `akeno/services.py`, new `akeno/deps/`, `akeno/ui/pages/install.py`

## Exact next steps

1. `akeno/deps/resolver.py`: `DependencyPlan` (items: package, nr_runtime, dlss_runtime, reshade, consumer) with
   state ok / resolvable_local / resolvable_download / blocked + reason + guided action; `resolve()` imports
   verified local runtimes, pre-downloads verified packages (with progress + cancel), never marks planned work
   as passed.
2. `AppServices.dependency_plan(game)` / `resolve_dependencies(game, progress, token)`.
3. Install page → wizard with grouped sections + "Resolve Dependencies" button.
4. Dashboard Install tile: enabled when only resolvable dependency blockers remain.

## How to render screenshots locally

```
python tools/fixture_home.py /tmp/fx/home /tmp/fx/games --with-benchmarks --gpu rtx5090
AKENO_HOME=/tmp/fx/home QT_QPA_PLATFORM=offscreen python -m akeno --screenshot /tmp/shots   # 1920x1009 default
```

## Design / architecture decisions

* Assets live under `akeno/data/assets/` so the existing PyInstaller `datas` entry (`akeno/data`) bundles them
  and `paths.resource_dir()` resolves them from source and from `_MEIPASS`.
* Artwork is decoration only — all text, values, badges and buttons stay real Qt widgets (per asset README).
* `game_cover_concept_687x537.png` from the design ZIP is NOT bundled: it shows a third-party game logo and the
  asset README says not to present it as a real cover. Procedural cover fallback stays.
* Pages may expose `footer` (pinned below the scroll area, see `PageHost` in `main_window.py`) and
  `set_density(level)` (0/1/2 from window height).
* Commits: always run tests with `set -o pipefail` before committing (a failing test slipped into d62707e
  because `| tail` masked the exit code; fixed in 4603906).
