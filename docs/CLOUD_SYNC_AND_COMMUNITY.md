# Cloud Sync and Community (AKENO Gaming Suite 3.0)

Both live in the ONLINE section. Neither uses the license server: the VPS runs licensing only.

## Cloud Sync

Cloud Sync is local first and opt-in.
- You pick a folder that a cloud drive or NAS already copies between your PCs (OneDrive, Dropbox, Google Drive, a network share).
- AKENO writes small JSON items into `<folder>\AKENO Sync\` and merges what your other PCs wrote. It never uploads anything itself.
- Without the folder, everything keeps working.

| Synced | Never synced |
|---|---|
| Settings from a fixed list: language, appearance, benchmark length, … | License state, tokens, passwords, the GitHub token |
| Game favourites, categories, notes, launch options, playtime and launches per PC | Program files, DLLs, mods, game files |
| DLSS 5 method, preset and custom values per game | Wallpaper media, dock pins (paths of one PC) |
| FPS Booster profile per game, as a suggestion only | Logs, caches, capture files |
| Benchmark results (statistics, GPU, driver, resolution) | |
| Widgets, pause rules, dock style | |

How the merge works:
- **Base copy:** each PC keeps the version of every item it last synced (the base).
- **Changed on one PC:** taken over by the other.
- **Changed on both:** merged field by field. Playtime is counted per PC, so two PCs never double it.
- **Same field changed differently on both:** a conflict you decide on the page ("Keep this PC's" or "Use the other PC's").
- **A PC joining for the first time:** it compares against a fresh installation's defaults, so only values both PCs really changed can conflict.
- **Deletions:** deleting a benchmark here removes it on the other PCs too, through a tombstone.

Safety:
- Every item is built from fixed field lists and checked for secret-looking keys before it is written.
- Each write is atomic, so a cloud drive never uploads half an item.
- A quota (default 25 MB) and a 1 MB limit per item apply.
- With a passphrase, items and their file names are encrypted (AES-256-GCM, key from scrypt). The passphrase stays on each PC under Windows DPAPI.
- FPS Booster choices from another PC only pre-select the profile. Windows settings change only when you apply them on that PC.

## Community packages

A `.akenopack` file is one JSON document, data only. There are three kinds:

| Kind | Contains | Using it |
|---|---|---|
| DLSS 5 preset | a method from the stack catalog, a preset, custom values (names and plain values checked) | selects them for the game; installing is the normal previewed step |
| FPS Booster profile | a profile and its options (known keys and values only; never your program list) | pre-selects it on the FPS Booster page; nothing is applied |
| Widget layout | widgets with positions, sizes and options (never notes or to-dos) | replaces your widgets after you confirm |

Themes (`.akenotheme`) keep their own format and checks (see `DESKTOP_STUDIO.md`).

Import rebuilds every package from known fields. Unknown kinds, settings, values or anything script-like are refused. A package never carries files, paths to run or URLs to open.

### Moderation and the community index

The index is `community/index.json` in the **public release repository**. AKENO fetches it only when you refresh the Community page.
- **Review:** a package gets into the index through a reviewed pull request, and the index records its SHA-256 and size.
- **Downloads:** HTTPS only, from GitHub hosts only, with no redirects elsewhere. A download is checked against the index's SHA-256 before import.
- **Reporting:** "Report…" opens a prefilled GitHub issue in your browser. AKENO sends nothing itself.
- **Blocking:** reported packages are removed, and their SHA-256 goes on the index's `blocked` list. AKENO also applies that list to files you import by hand.
