# Community runtime source review — v1.3, 2026-10-21

This review queries source and release metadata. No RHI/NVIDIA proprietary archive was downloaded, executed, scanned or approved. Local review/import is separate from the unchanged official NVIDIA signature gate. Lack of a signature alone is not a malware verdict; a hash alone is not a safety verdict.

## Sources inspected and evidence limits

| Source pinned at review | Technical findings | Disposition |
|---|---|---|
| [Skynizz fc702f8ca22f97b01bd6a02f3386bffb3484fc35](https://github.com/Skynizz/optiscaler-dlss5/tree/fc702f8ca22f97b01bd6a02f3386bffb3484fc35) | Open GPL consumer/forwarder; source documents D3D12, D3D11-on-12 and Vulkan-on-12 paths. Its DLSS-NR README says the forwarder contains no NVIDIA model code and model files are not redistributed. Other-game RTX 4070 measurements exist; they do not identify the father's RTX 4080 runtime/hash. | Existing pinned consumer adapter retained; proprietary runtime compatibility remains exact-build dependent. |
| [DLSS5-Feeder 16cb1946a8331751b0f89a728b797d895ecdfba1](https://github.com/jlrouzies-fr/DLSS5-Feeder/tree/16cb1946a8331751b0f89a728b797d895ecdfba1) | MIT frame/input feeder, not the NR model. README states signed 310.8.0 and modified Lecram fail feature 18 on RTX 20/30/40 and distinguishes other GPU-specific runtime variants. It warns about native-DLSS conflicts and certain consumer/Smooth Motion crashes. Installer describes antivirus exclusions and third-party downloads. | Existing vetted file-only package path retained. AKENO does not run upstream installers, add antivirus exclusions, download their runtime repacks or register machine-wide Vulkan layers. |
| [Dagherbou 973761621353b99bee3dc7d4bb27b117fef2644f](https://github.com/Dagherbou/OptiScaler_DLSSNR/tree/973761621353b99bee3dc7d4bb27b117fef2644f) | Alternative consumer fork; the inspected README does not establish an exact RTX 4080 NR runtime hash or a reproducible replacement model. | Research candidate; no new automatic package or hardware claim. |
| [RHI 3e80cabe5041071832ceea92c8803b5fdcc517ae](https://github.com/RankFTW/rhi-repo/tree/3e80cabe5041071832ceea92c8803b5fdcc517ae) | Repository metadata has `license: null`, no description. Complete tree contains game database JSON and two INI files; no model source, licence, README or reproducible NR build instructions. Latest repository push observed 2026-10-13. Release bodies only repeat version labels. | Distribution/implementation provenance unresolved. Release labels are research leads, not pre-approved community runtime reviews. |

The Feeder statement about Lecram/310.8.0 is a version-level upstream report, not our exact-hash hardware validation. It must not be generalized to every future runtime or GPU. Its Defender warning is an upstream consumer heuristic report, not an independently confirmed antivirus verdict about SF-v2/Lecram. No hash-specific trusted malware scan was available; no clean/malicious label is inferred.

## RHI release metadata observed

These SHA-256 values are GitHub-published **ZIP archive digests**, not extracted DLL pins and not proof of safety, provenance or permission:

| Release | Published | ZIP size | Published archive SHA-256 |
|---|---|---|---|
| [dlssnr-310.8.0](https://github.com/RankFTW/rhi-repo/releases/tag/dlssnr-310.8.0) | 2026-08-27 | 109425288 | `388c0a7912e15ec911b9c9e11a692142b11fe387ddf2b637d8c358138fffb3ac` |
| [dlssnr-310.8.SF-v2](https://github.com/RankFTW/rhi-repo/releases/tag/dlssnr-310.8.SF-v2) | 2026-08-30 | 116693212 | `1da35941894994eb087e017577829e492454e9bae3a6a9397027069ceb74955c` |
| [dlssnr-310.8.Lecram](https://github.com/RankFTW/rhi-repo/releases/tag/dlssnr-310.8.Lecram) | 2026-09-24 | 116658260 | `ddb64e5545ba3f217c32555d2eb8fdfe76b9510d2256f13e8a5ecb1a9650ca54` |

An additional `dlssnr-310.8.0-RTX40` tag was observed. Naming a release RTX40 or SF-v2 does not demonstrate cross-generation support. The SF-v2 body contains no such technical explanation; the advertised cross-generation claim remains unverified. GPU architectures, CUDA/NGX dependencies, modifications, DLL hashes, exact driver compatibility, reproducibility and local-use/redistribution permission remain unknown for these archives here. No NVIDIA signature is inferred from the publisher name/version strings.

## Exact compatibility matrix

AKENO ships **no approved binary hashes** for those RHI candidates. For the owner's GPUs, all combinations of these candidates are currently `unreviewed / execution unknown`, with Lecram's older-GPU failure report retained as a limitation. A local review must supply the exact extracted DLL SHA-256/version and explicit generation + driver + API + backend experimental targets. Incompatible entries block; missing exact targets remain blocked. Experimental entries allow a controlled offline test, not a green working status. Fresh attributed creation/evaluation is still required.

The user reports a real RTX 5090 smoke run: 31.8 GB VRAM, Windows 11, driver 617.42, 57 games, 41 signed regular DLSS candidates and zero NR candidates. This is a user-supplied observation, not a test repeated in this cloud. It establishes why regular SR discovery does not resolve missing `nvngx_dlssnr.dll`. The father's RTX 4080/16 GB Swapper report remains separate from demonstrated NR evaluation.

## Game/backend inputs

| Game situation | Backend path | Additional prerequisites / limits |
|---|---|---|
| x64 D3D12, detected DLSS/FSR2+/XeSS input | Existing OptiScaler NR | Required real temporal inputs, compatible game build, exact reviewed runtime, NVIDIA SR where needed; existing proxy/consumer conflicts block. |
| x64 D3D11 with supported input | OptiScaler D3D11-on-12 (`dlss_12`) | Upstream bridge must work in that game; API label alone does not create depth/motion inputs. |
| x64 Vulkan with supported input | OptiScaler Vulkan-on-12 | Supported bridge/driver prerequisites; no universal Vulkan promise. |
| x64 D3D11/D3D12/Vulkan without native input | Existing DLSS5-Feeder | Independently installed compatible ReShade/add-on/input provider and exactly one neural consumer; SR runtime required. No arbitrary injection fallback. |
| Other APIs / x86 / absent input or bridge | No new community deployment route | Missing prerequisites shown by existing adapters. Wider upstream claims do not expand AKENO's implemented API contract. |
| FiveM / GTA Online / ambiguous GTA / anti-cheat | Community route blocked | Existing FiveM read-only wizard and signed-runtime recipe gates are retained; server ownership cannot authorize a client-protection bypass. |

## Review trust boundaries

A user-selected review record is an explicit local expert/operator attestation, not an AKENO-maintainer endorsement or cryptographic provenance proof. Required evidence fields prevent silent approval from a filename or ZIP digest; their truth cannot be established by JSON schema. Review source, modifications, dependencies, build reproducibility and licensing independently before signing your name to a record. If this cannot be established, leave it unreviewed. Do not put placeholder claims in required fields to enable import. No automatic runtime downloader or review-file auto-discovery is added.

No part of this feature patches a binary, spoofs a signature, alters multiplayer protection, suppresses antivirus or bypasses licensing/DRM. Runtime families are never substituted. Review permission for local use does not grant redistribution rights.
