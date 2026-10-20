# FiveM Legacy integration handover

- Branch: `codex/fivem-legacy-support`.
- Base: `4603906340576da7f7e21681184fa52bb88e3aff`, the newest development commit available at start on `claude/affectionate-newton-cdn1tp`. `main` only contained the initial README. No Claude branch is modified or pushed.
- Scope: isolated FiveM modules and tests; final UI wiring deferred until coordinated with the current launcher.
- Initial state: clean checkout; Linux cloud machine, no Windows/FiveM/RTX hardware validation available.
- Research: direct HTTPS requests to FiveM docs, NVIDIA and GitHub API returned proxy tunnel 403. Native GitHub Git reads work. Investigating authoritative Git sources as a fallback. No compatibility or security approval inferred from inaccessible sources.
- Next: inspect Game/BackendAdapter/CompatibilityEngine/TransactionInstaller contracts; implement discovery, policy and evidence gates, installation planning and transactional integration tests.
- Checkpoints: commit and push at most five minutes apart during active work when access permits; update this file each time.

## Checkpoint 2 — discovery and evidence gates

- Added isolated `akeno/fivem` domain models, bounded discovery/inventory, edition and process classification, server permission and backend evidence checks.
- Official FiveM docs inspected at `6ec9770535bbdd58297e5cb01f264b5a55d937a7`: plugin directory, optional server restrictions, pure levels 1/2 and Enhanced restrictions confirmed from source.
- Community evidence conflicts: Skynizz reports RTX 4070 experiments; DLSS5-Feeder says signed 310.8.0 neural feature creation fails on RTX 20/30/40. RTX 4080 is NOT marked supported.
- Existing suite before new tests: **74 passed** on Linux, offscreen Qt, Python 3.12. No real hardware/game tests.
- Development dependencies installed in `/workspace/akeno-venv` using repository-pinned requirements. No tracked launcher/dependency files changed.
- Git push works. GitHub API returns proxy 403 (despite injected GH_TOKEN presence); `gh auth status` cannot establish API access. No new token requested. GitHub HTML search works and identified actual FiveM projects for source inspection.
- Saved draft additions for api.github.com, docs.fivem.net, developer.nvidia.com and support.cfx.re. This does not prove active network access changed.
- Next: finish planner/transaction facade, inspect FiveM-specific upstream source, add safety and rollback tests.

## Checkpoint 3 — transaction pipeline and first tests

- Implemented an opt-in `BackendAdapter` wrapper and `FiveMService` using the existing registry/catalog, secure staging, Compatibility Engine and Transaction Installer. Default launcher/discovery/UI remain untouched.
- Supports reviewable plans, fresh client/permission/source checks, all-client running-process checks, rollback, restore conflicts, snapshots and restore. No installable FiveM recipe is shipped.
- Added pinned-source candidate assessments for kerim0x1, LanteanDev, BMTB, VannWasHere, DLSS5-Feeder and Skynizz.
- **54 new FiveM tests passed** (filesystem/PE fixtures; mocked Windows signature checks explicitly labelled). Initial fixture issue diagnosed: identical synthetic runtime hashes collided in the existing database primary key; fixture bytes corrected. No safety gate weakened.
- Actual GitHub HTML search and pinned raw text/source retrieval worked. No FiveM pack, third-party installer, proprietary runtime or community mod binary was downloaded/executed for research.
- Next: review safety edge cases, complete research/integration and real-machine test protocol docs; run combined regression suite and source smoke test; create draft PR if API access is available.
