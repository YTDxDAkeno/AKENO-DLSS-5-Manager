# v1.6 official runtime and consumer research

## Verified release contents

Downloaded from the actual [NVIDIA release asset](https://github.com/NVIDIA-RTX/Streamline/releases/download/v2.14.16/streamline-sdk-v2.14.16.zip):

- Release v2.14.16, source `625db07d4bca5abc67d781c42af7e7fb0761f1b6`.
- Archive size: **276016170 bytes**; 511 ZIP entries.
- Archive SHA-256: `32041e51db0cab709a271b72a352281b690c2abbd669a827809401a5dd23956b`.
- Contains standard SR/RR/FG/Streamline DLLs; **no entry named `sl.dlss_nr` or `nvngx_dlssnr`**.
- [Changelog](https://github.com/NVIDIA-RTX/Streamline/blob/625db07d4bca5abc67d781c42af7e7fb0761f1b6/changelog.txt)
  says 2.14.0 added the `sl.dlss_nr` plugin. That statement is not a plugin distribution.

Only the x64 ZIP above was inventoried, not ARM64 variants or every historical SDK.
The archive was inspected privately; none of its proprietary DLLs enter this repository
or public AKENO artifacts. No renamed Streamline stand-in or invented download is used.

The requested [DLSS overview](https://www.nvidia.com/en-us/geforce/technologies/dlss/)
and [faster-model article](https://www.nvidia.com/en-us/geforce/news/dlss-5-faster-ai-model-hogwarts-legacy-rtx-spark-laptops/)
were denied by this executor's outbound proxy (403). The user's release/NBA 2K27
premise is therefore recorded as supplied context, not independently confirmed from
those pages. An official GPU-support announcement alone would not establish a
working community consumer/runtime/driver combination.

## Local runtime evidence

| Runtime | Evidence available in this task | Compatibility conclusion |
|---|---|---|
| NR 310.8.0.0 `8270b350cd82de5ce89806872cdd6b6a9249b80836b91bbeb3573470744cc206` | User reports modified community file and NGX secure-loader signature failure | Not NVIDIA-authenticated; no successful Feature-18 result established |
| SR 310.9.1.0 `3975567b8943c53acce397f2b72380092f84f162d00b0d2c7d08a1025c563983` | User reports official import and 300/300 SR host calls | SR-only result; bytes/signature not available to this executor |
| Genuine signed NR replacement | No runtime file provided; absent from inspected SDK ZIP | Whether it resolves RTX 5090/617.42 loading remains a hardware acceptance test |

AKENO's official inventory now retains source path, permission, signer, signature
result, family, version, SHA-256 and an explicit **execution untested** note. Hashes
are checked across identity/signature inspection and cache copy. Both official NR
inventory and the older verified runtime store can be selected in the existing
self-test/pipeline flow. A cached trust flag never substitutes for fresh signature
inspection before executing a selected official runtime.

Use **Find local runtimes** or select a legitimately obtained local copy from your
own permitted installation/driver/NGX store. Discovery is not licensing permission.
No universal signed-NR hash, minimum driver or compatible plugin version is invented.
Signature authenticity, NGX secure-loader acceptance and Feature-18 capability are
three separate tests. The manager cannot turn the modified candidate into an
authenticated NVIDIA file and does not patch NVIDIA verification.

## Consumers and current suitable releases

Latest release pages were revisited during this task:

| Project | Observed release | Source/review decision |
|---|---|---|
| [Skynizz NR](https://github.com/Skynizz/optiscaler-dlss5/releases/latest) | 1.3.0 | Existing pinned `fc702f8…`; v3 instruments DX12 forwarder/proxy and rect evaluation |
| [Feeder](https://github.com/jlrouzies-fr/DLSS5-Feeder/releases/latest) | 1.19.0 | Retain existing exact package pins; main HEAD `16cb1946a8331751b0f89a728b797d895ecdfba1` |
| [OptiScaler](https://github.com/optiscaler/OptiScaler/releases/latest) | 0.9.4 | Standard upstream is not automatically the NR fork |
| [Dagherbou NR](https://github.com/Dagherbou/OptiScaler_DLSSNR/releases/latest) | 0.2.0-dlssnr | Source reviewed in v1.5; not interchangeable with pinned Skynizz ABI |
| [RenoDX](https://github.com/clshortfuse/renodx) | Individually versioned add-ons | Public HDR utilities do not establish licensed NR consumer/source identity |
| [RHI](https://github.com/RankFTW/RHI), [rhi-repo](https://github.com/RankFTW/rhi-repo) | Tool/metadata references | Filename labels/distribution claims do not establish an allowed consumer package |

The existing [19-project source review](AKENO_V1_5_UPSTREAM_RESEARCH.md) records exact
commits/licenses/paths for the three OptiScaler trees, Feeder host/transport, RenoDX,
RHI and Streamline. Reuse that reviewed code; no AGPL/proprietary consumer code or
third-party snapshot binaries have been copied into AKENO. No backend is stacked
with another neural consumer. The source build wrapper preserves the selected
upstream path; it does not promote that path to official Streamline integration.
