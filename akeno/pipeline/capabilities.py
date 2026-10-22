"""Exact evidence axes; no generation-wide neural-execution inference."""
import re
from dataclasses import dataclass, field


def version(value):
    if not value or not re.fullmatch(r'\d+(?:\.\d+){1,3}', value):
        return None
    return tuple(int(p) for p in value.split('.')) + (0,)*(4-len(value.split('.')))


@dataclass
class PipelineFacts:
    game_id: str
    api: str
    architecture: str | None
    native_calls: bool | None = None
    native_evidence: str = 'Files alone do not establish native DLSS calls.'
    gpu_vendor: str = 'unknown'
    gpu_device_id: int | None = None
    gpu_generation: str | None = None
    driver: str | None = None
    runtime_sha256: str | None = None
    runtime_version: str | None = None
    runtime_category: str | None = None
    components: dict = field(default_factory=dict)
    consumers: list[str] = field(default_factory=list)
    proxy: str | None = None
    conflicts: list[str] = field(default_factory=list)
    permitted: bool = False
    running: bool = False
    source: str | None = None
