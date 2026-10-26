# Architecture

```
            ┌────────────── UI (PySide6, akeno/ui) ───────────────┐
            │ MainWindow · Sidebar · 8 pages · Controller          │
            │ long work → ui/workers.py (QThreadPool)              │
            └───────────────┬──────────────────────────────────────┘
                            │ plain Python calls
                   AppServices (akeno/services.py)
   ┌──────────┬──────────┬───────────┬────────────┬────────────┬───────────┐
hardware/  games/     compat/    recommend/   backends/    benchmark/   deps/
DXGI,reg,  launchers, evidence   measured >   adapters:    PresentMon,
NVML,      PE + VDF   DB +       estimated >  OptiScaler   stats,
displays   parsers,   engine     hardware     NR, Feeder;  CSV/JSON
           analyzer                           runtimes
   └──────────┴──────────┴─────┬─────┴────────────┴────────────┴───────────┘
                    security/ (catalog, downloads, archive, Authenticode)
                    install/  (transactional installer, backups, snapshots)
                    core/     (models, SQLite db, settings, processes)
```

* **Dependencies are resolved before anything is installed.** `deps/resolver.py` turns each backend's
  `dependency_specs()` into a plan (ok / found locally / downloadable / manual / blocked / not searched).
  "Resolve" imports verified NVIDIA runtimes found by `backends/runtimes.py` (DriverStore, System32, NGX
  store, driver packages, games, RHI, Downloads — NVIDIA-signed and correctly named only) and fetches
  hash-pinned packages; it never touches a game folder. See `docs/DEPENDENCIES.md`.
* **Support knowledge vs. verification.** `hardware/support.py` keeps the official DLSS feature matrix,
  the documented runtime-version × GPU-generation behaviour (`nr_runtimes` in `compatibility.json`) and
  the only thing that counts as *verified*: success lines in the backend logs next to the game.
* **Design assets are data.** `ui/assets.py` loads `data/assets/*` through `paths.resource_dir()` (source
  and PyInstaller builds), scales for the device pixel ratio, and falls back to `ui/art.py` drawings.
* **No Qt below `ui/`.** Every service is testable headless; `tests/` exercises them with synthetic
  PE files and fixture game folders.
* **Adapters plan, the installer applies.** A backend adapter returns an `InstallPlan` (files, sources,
  pinned hashes, approved renames). Only `install/transaction.py` touches game folders, and it records
  every step in SQLite *before* doing it.
* **Trust is data.** `data/catalog.json` pins archive and per-file SHA-256 hashes;
  `data/compatibility.json` carries the evidence for every compatibility claim. Both are validated on load.
* **State.** `%LOCALAPPDATA%\AKENO\akeno.sqlite3` holds settings, games and overrides, profiles,
  transactions and their files, benchmarks and imported runtimes. Backups live beside it in `backups\`.

## Installation transaction

1. Folder exists, game not running, folder writable without elevation.
2. Every target that exists must have been installed by AKENO (otherwise: blocked).
3. Every source file re-hashed against its pinned hash.
4. Transaction row created (`planned` → `backing_up`); originals copied to `backups\txNNNNN\files` and
   the copies re-hashed.
5. `applying`: approved renames (`*.akeno-disabled`), then atomic writes (`tmp` + `os.replace`), each
   file's resulting hash recorded.
6. `installed`; previous install of the same backend becomes `superseded` (its backups stay the
   restore point of the original state).
7. Adapter validation; on failure the whole chain is restored automatically.
8. Any exception: rollback of written files and renames → `rolled_back` (or `failed` if incomplete).

Restore undoes the whole chain for that backend and folder, restoring originals from the oldest
transaction and removing created files, after warning about files changed since installation.

## System tools (3.1)

`akeno/system/` holds System Doctor, Smart Cleaner, Network Lab, Universal Mod Scanner and Security Center
without Qt: providers (real Windows implementation and stand-ins for tests) feed pure diagnosis functions that
return `Finding`s with evidence (measured / possible cause / correlation). Confirmed actions are journalled in
`%LOCALAPPDATA%\AKENO\system.sqlite3` (separate from `akeno.sqlite3` and Cloud Sync) and can be undone where
Windows allows it. `AppServices.system_tools` creates the modules on first use. Downloads pass an optional
antivirus veto (`DownloadManager.antivirus`) after their SHA-256 check. `akeno/recovery.py` restores what AKENO
changed without an active license. Details: [AKENO_3_1_SYSTEM_TOOLS.md](AKENO_3_1_SYSTEM_TOOLS.md).
