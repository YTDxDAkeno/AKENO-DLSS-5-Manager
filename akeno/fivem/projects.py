"""Source-reviewed candidates, separate from the installable artifact catalog.

These records convey limitations, not malware verdicts or permission to download a binary.
All observations refer to pinned text/source revisions, not an executed upstream release.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectAssessment:
    repository: str
    revision: str
    role: str
    disposition: str
    reasons: tuple[str, ...]

    @property
    def source(self) -> str:
        return f"https://github.com/{self.repository}/tree/{self.revision}"


PROJECTS = (
    ProjectAssessment("kerim0x1/FiveM-DLSS5", "24c25313f13acc358f1f018cd2f59186e3576173",
        "FiveM plugin pack around DLSS5-Feeder and a neural consumer", "not_approved", (
            "README requires RTX 50 for signed neural model; RTX 20/30/40 instructions refer to a GPU-matched runtime from Discord.",
            "Pack script includes NVIDIA runtimes and a non-public consumer; redistribution rights and artifact provenance are not established.",
            "README calls FiveM D3D12 while using d3d11.dll; API must be measured independently.",
            "Installer overwrites/removes some existing files; AKENO does not execute it.",)),
    ProjectAssessment("LanteanDev/GTAV---FiveM---Legacy-DLSS_NR", "7a892f9a5e8cb310887d74fa7e6b1bcb5665a03e",
        "Legacy/FiveM graphics stack", "not_approved", (
            "README explicitly requires a patched nvngx_dlssnr.dll from Discord.",
            "No signed-runtime RTX 4080 evidence established; no binary downloaded or executed.",)),
    ProjectAssessment("bankrollmadethisbeat/BMTB-DLSS-INSTALLER", "53c55a16b97461b5e3261f9398422c51e9d02d2f",
        "FiveM package installer", "insufficient_source", (
            "Root source tree exposes a README, not the installer implementation.",
            "Claims RTX 20/30/40 support and bundled third-party binaries; claims cannot establish neural-feature execution, signatures or redistribution rights.",)),
    ProjectAssessment("VannWasHere/fivem-dlss", "ee0965d2f0878726b20148ecd8452213c43b08f7",
        "ASI frame-generation prototype", "not_neural_rendering", (
            "Source factory explicitly falls back from unimplemented DLSS3 to an FSR3-labelled backend.",
            "Frame generation is not DLSS 5 Neural Rendering; README restricts use to single-player FiveM experiences.",
            "No multiplayer safety or binary integrity certification performed.",)),
    ProjectAssessment("jlrouzies-fr/DLSS5-Feeder", "16cb1946a8331751b0f89a728b797d895ecdfba1",
        "ReShade frame feeder; requires a separate neural consumer", "candidate_only", (
            "README reports signed 310.8.0 neural feature creation failing on RTX 20/30/40.",
            "Feeder alone is not a neural backend; ReShade, motion/depth inputs and exactly one consumer are required.",
            "Upstream installer can fetch modified runtimes; AKENO never executes that installer or downloads those runtimes.",)),
    ProjectAssessment("Skynizz/optiscaler-dlss5", "fc702f8ca22f97b01bd6a02f3386bffb3484fc35",
        "OptiScaler neural consumer", "candidate_only", (
            "README reports RTX 4070 experiments in other games; this is not FiveM/RTX 4080 validation.",
            "Needs usable upscaler inputs, or a separately reviewed feeder route for Legacy.",
            "README says the undocumented NVIDIA runtime is user-supplied; signed-runtime evidence still needs review.",)),
)
