"""Domain models shared by the services and the UI.

Every value that may be unknown is ``None`` (or an explicit ``UNKNOWN`` enum member) rather than a
guessed default, so the UI can render "Unknown" instead of a plausible-looking fabrication.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Vendor(str, Enum):
    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"
    MICROSOFT = "microsoft"  # Basic Render Driver / WARP
    UNKNOWN = "unknown"


class HardwareSupport(str, Enum):
    """Support level of a GPU for DLSS 5 Neural Rendering community mods."""

    OFFICIAL = "official"          # only used when evidence says NVIDIA officially supports it
    COMMUNITY = "community"        # community mods report it working
    EXPERIMENTAL = "experimental"  # possible, but depends on factors AKENO cannot confirm
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass
class GpuInfo:
    name: str
    vendor: Vendor = Vendor.UNKNOWN
    vendor_id: int | None = None
    device_id: int | None = None
    subsys_id: int | None = None
    revision: int | None = None
    vram_bytes: int | None = None
    shared_memory_bytes: int | None = None
    driver_version: str | None = None       # user facing, e.g. "561.09" for NVIDIA
    driver_version_raw: str | None = None   # Windows format, e.g. "32.0.15.6109"
    driver_date: str | None = None
    architecture: str | None = None         # "Ada Lovelace" ...
    generation: str | None = None           # "RTX 40"
    chip: str | None = None                 # "AD104"
    has_tensor_cores: bool | None = None
    is_software: bool = False
    is_primary: bool = False
    luid: str | None = None
    feature_level: str | None = None        # "12_2"
    sources: list[str] = field(default_factory=list)  # which APIs contributed data

    @property
    def vram_gb(self) -> float | None:
        return None if self.vram_bytes is None else self.vram_bytes / (1024 ** 3)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["vendor"] = self.vendor.value
        return d


@dataclass
class DisplayInfo:
    name: str
    width: int | None = None
    height: int | None = None
    refresh_hz: int | None = None
    is_primary: bool = False


@dataclass
class SystemInfo:
    os_name: str | None = None
    os_version: str | None = None
    os_build: str | None = None
    os_display_version: str | None = None  # "23H2"
    is_64bit: bool | None = None
    cpu_name: str | None = None
    ram_bytes: int | None = None
    directx_version: str | None = None
    gpus: list[GpuInfo] = field(default_factory=list)
    displays: list[DisplayInfo] = field(default_factory=list)
    hardware_gpu_scheduling: bool | None = None
    detection_errors: list[str] = field(default_factory=list)
    detected_at: str | None = None

    @property
    def primary_display(self) -> DisplayInfo | None:
        for d in self.displays:
            if d.is_primary:
                return d
        return self.displays[0] if self.displays else None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gpus"] = [g.to_dict() for g in self.gpus]
        return d


class GraphicsApi(str, Enum):
    D3D8 = "d3d8"
    D3D9 = "d3d9"
    D3D10 = "d3d10"
    D3D11 = "d3d11"
    D3D12 = "d3d12"
    VULKAN = "vulkan"
    OPENGL = "opengl"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            "d3d8": "DirectX 8", "d3d9": "DirectX 9", "d3d10": "DirectX 10", "d3d11": "DirectX 11",
            "d3d12": "DirectX 12", "vulkan": "Vulkan", "opengl": "OpenGL", "unknown": "Unknown",
        }[self.value]


class AntiCheatRisk(str, Enum):
    DETECTED = "detected"          # known anti-cheat component found -> automatic install blocked
    NONE_FOUND = "none_found"      # no known component found; NOT proof of absence
    UNKNOWN = "unknown"


@dataclass
class DetectedFile:
    name: str
    path: str
    kind: str            # e.g. "dlss", "optiscaler", "reshade", "proxy_dll", "asi_loader", "neural_consumer"
    detail: str | None = None
    version: str | None = None


@dataclass
class Game:
    id: str                         # stable id, e.g. "steam:1091500" or "manual:<hash>"
    name: str
    install_dir: str
    launcher: str                   # steam, epic, gog, ubisoft, ea, xbox, manual
    launcher_id: str | None = None
    exe_path: str | None = None     # chosen main executable
    exe_candidates: list[str] = field(default_factory=list)
    api: GraphicsApi = GraphicsApi.UNKNOWN
    api_source: str | None = None   # "imports", "user", "compat-db"
    api_candidates: list[str] = field(default_factory=list)
    architecture: str | None = None  # "x64" / "x86"
    dlss_version: str | None = None
    upscaler_inputs: list[str] = field(default_factory=list)  # dlss, fsr, xess
    existing_mods: list[DetectedFile] = field(default_factory=list)
    anti_cheat: AntiCheatRisk = AntiCheatRisk.UNKNOWN
    anti_cheat_details: list[str] = field(default_factory=list)
    is_running: bool | None = None
    analyzed_at: str | None = None
    analysis_version: int = 0          # analyzer.ANALYSIS_VERSION that produced this record
    artwork_path: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def target_dir(self) -> str | None:
        """Directory holding the main executable (where proxy DLLs are installed)."""
        if not self.exe_path:
            return None
        import os
        return os.path.dirname(self.exe_path)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["api"] = self.api.value
        d["anti_cheat"] = self.anti_cheat.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Game":
        d = dict(d)
        d["api"] = GraphicsApi(d.get("api") or "unknown")
        d["anti_cheat"] = AntiCheatRisk(d.get("anti_cheat") or "unknown")
        d["existing_mods"] = [DetectedFile(**m) for m in d.get("existing_mods") or []]
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


class CompatStatus(str, Enum):
    CONFIRMED = "confirmed"
    EXPERIMENTAL = "experimental"
    INCOMPATIBLE = "incompatible"
    INSUFFICIENT = "insufficient"
    BLOCKED = "blocked"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKER = "blocker"


@dataclass
class Finding:
    severity: Severity
    code: str
    message: str           # already human readable (English); UI may translate via code
    evidence: str | None = None


@dataclass
class CompatibilityResult:
    status: CompatStatus
    backend_id: str | None
    findings: list[Finding] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: str = "low"   # low / medium / high

    @property
    def blockers(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.BLOCKER]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.WARNING]


class TrustState(str, Enum):
    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    UNVERIFIED = "unverified"
    BLOCKED = "blocked"


@dataclass
class FrameStats:
    frames: int
    duration_s: float
    avg_fps: float
    avg_frametime_ms: float
    median_frametime_ms: float
    p95_frametime_ms: float
    p99_frametime_ms: float
    low_1pct_fps: float
    low_01pct_fps: float | None
    stdev_frametime_ms: float
    stutter_ratio: float   # share of frames > 2x median frame time
    median_fps: float | None = None
    stutter_events_per_minute: float | None = None
    frame_spikes: int | None = None
    application_rendered_fps: float | None = None
    displayed_fps: float | None = None
    generated_frame_share: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkSession:
    id: int | None
    game_id: str
    label: str                  # "baseline" or profile name
    started_at: str
    stats: FrameStats
    gpu: str | None = None
    driver: str | None = None
    resolution: str | None = None
    backend_id: str | None = None
    neural_config: dict[str, Any] = field(default_factory=dict)
    source: str = "presentmon"   # presentmon | csv-import | fixture
    csv_path: str | None = None
    process_name: str | None = None
