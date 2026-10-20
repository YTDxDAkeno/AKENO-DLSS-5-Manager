# Architecture

```
            ┌────────────── UI (PySide6, akeno/ui) ───────────────┐
            │ MainWindow · Sidebar · 8 pages · Controller          │
            │ long work → ui/workers.py (QThreadPool)              │
            └───────────────┬──────────────────────────────────────┘
                            │ plain Python calls
                   AppServices (akeno/services.py)
   ┌──────────┬──────────┬───────────┬────────────┬────────────┬───────────┐
hardware/  games/     compat/    recommend/   backends/    benchmark/
DXGI,reg,  launchers, evidence   measured >   adapters:    PresentMon,
NVML,      PE + VDF   DB +       estimated >  OptiScaler   stats,
displays   parsers,   engine     hardware     NR, Feeder;  CSV/JSON
           analyzer                           runtimes
   └──────────┴──────────┴─────┬─────┴────────────┴────────────┴───────────┘
                    security/ (catalog, downloads, archive, Authenticode)
                    install/  (transactional installer, backups, snapshots)
                    core/     (models, SQLite db, settings, processes)
```

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
