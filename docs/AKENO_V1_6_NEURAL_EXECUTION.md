# v1.6 neural execution implementation

This branch includes v1.5 `c845a29` and the official-SR inventory hotfix through
`6f0e33d`. It does not establish physical RTX success. The implemented subset
makes the existing real OptiScaler NR backend measurable and its failures useful.

## Execution contract

`akeno/diagnostics/execution.py` emits schema 2. It separates:

| Stage | Evidence accepted | What remains unproven |
|---|---|---|
| runtime_module_loaded | Exact installed runtime path in a process module snapshot | Initialization, authentication and execution |
| ngx_initialized | Explicit fresh initialization result | Feature 18 |
| feature_created | Authorized create counters or explicit fresh process-bound create event | Evaluate, GPU work |
| evaluation_api_succeeded | Authorized evaluate return counters or explicit fresh process-bound NR success | GPU completion and presentation |
| gpu_execution_completed | No suitable measurement supplied; false | A fence must cover the actual NR submission |
| display_contribution_verified | No suitable measurement supplied; false | Provenance of the displayed output |

The legacy `verified` state continues to mean process-attributed API evidence.
It is not a playability or GPU-completion certificate. Normal UI wording now says
API returns and explicitly leaves GPU completion/display contribution unconfirmed.
A misleading “neural model (feature 18) loaded” message no longer establishes creation,
including in the older hardware-support log reader. 300/300 DLAA remains SR-only.

Process birth, executable hash, installed component hashes, diagnostic consumer
module hashes, runtime identity, GPU/driver, elapsed observation duration, errors,
shutdown limitations and fallback observations are retained in reports. A process
exit is not a verified crash; process exit is not an instrumented NGX shutdown result.

## Diagnostic consumer v3

The optional derivative is pinned to Skynizz/optiscaler-dlss5
`fc702f8ca22f97b01bd6a02f3386bffb3484fc35` (1.3.0), build
`akeno-fc702f8-v3`. `source_build.py` wraps the existing DX12 forwarder
`g_nr.create`, `g_nr.evaluate`, **and `g_nr.evaluateRect`** call sites, plus the
existing driver-proxy evaluate call. The derivative preserves original arguments
and return values. It does not change signature/caller checks, loader selection,
resource barriers, fence waits, protection policy or Windows TDR settings.

Counters now include maximum **CPU duration** of a returned evaluate, calls taking
at least two seconds, and separate forwarder/proxy call totals. CPU duration is
not GPU timing. A never-returning call cannot emit its final duration. Summaries
are throttled to at most one per five seconds; last intervals may be lost. Counts
include probes/multipass API calls, not distinct frames. Creation failures from a
pointer-returning forwarder cannot supply an exact NGX create error; original
backend logs remain necessary. Exact evaluate failure returns are retained.

The parser accepts v2 for existing installations and v3 for the new local build.
PID/birth/build and the exact selected consumer module must match before counters
are used. Decreasing totals, duplicate protocol keys and invalid unsigned values
are rejected. API counts remain positive facts even if the same lifetime later
reports a GPU timeout; the session itself then reports failure.

## Practical local build

Rendering pipelines → Runtime self-test → **Build optional diagnostic backend locally**.
The app checks Windows, Git, Visual Studio 2022 C++ tools/v143 and a D3D12 Windows
SDK, shows an explicit local-build action, fetches pinned open source and its gitlink
submodules, applies the bundled source-only instrumentation, builds normally with
MSBuild, and writes a private `diagnostic-manifest.json`. No NVIDIA runtime or
community precompiled binary is downloaded. No UAC operation is requested. Build
logs, submodule identities and manifest/binary hashes stay under AKENO's local
`diagnostic-builds` directory. Closing/cancelling stops the direct build command;
MSBuild may leave a compiler child finishing its current job. No game is modified.

Import that manifest using **Import diagnostic source build** and explicit local
consent. This is a local source declaration, not a signature or independent binary
security certification. Optional public binary redistribution is not enabled: a
complete corresponding-source/dependency/license release has not been assembled.
The guided local path is the practical fallback. Byte-identical PE builds across
toolchains are not claimed; each actual result receives SHA-256 hashes.

Reproduce manually with the existing `tools/build-nr-diagnostic.ps1` or run
`python tools/prepare_nr_diagnostic.py <exact-checkout>` then the upstream MSBuild
Release/x64 build and `tools/package_nr_diagnostic.py`. The same Python preparation
module and identical header are shipped in the Windows app. Git attributes and the
preparer preserve canonical LF header bytes, so source identities survive Windows
checkout line-ending conversion. The separate diagnostic
workflow compiles that pinned source; its public artifact contains source/build
records, never the compiled consumer or NVIDIA files.

## Standalone and gameplay

Self-tests retain existing official SR imports and now also accept official NR
inventory selections after fresh family/hash/signature checks. They preserve SR
results, NR results, secure-loader rejection, Feature-18 requirements queries,
dispatch observations, GPU-removal/DRED and evaluate-stall diagnostics separately.
They save local redacted JSON plus a ZIP containing that JSON only. No upload occurs.

A secure-loader rejection recommends a genuinely authenticated local runtime;
it does not trigger an alternate loader. A capability-query NotImplemented result
is recorded for that queried dispatcher, not generalized to every consumer path.

**Launch offline game test** validates the existing reviewed local experiment,
starts process-bound observation before normal executable/Steam launch, then uses
the existing monitoring UI. Configure offline mode in the launcher/game first.
FiveM, GTA Online and ambiguous GTA sessions remain excluded from this generic path.
Device removed/reset/hung and allocator/evaluate stalls latch a failed observation.
No synchronization “fix” is invented from a manager-only log.

PresentMon, screenshot comparison, exact transaction restore/recovery and source/SR
inventory hotfixes remain in place. Normal automatic capture never requests UAC.
