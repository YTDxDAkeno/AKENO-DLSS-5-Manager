"""Common interface for DLSS 5 Neural Rendering backend adapters.

An adapter knows one community project: its requirements, the files it needs in a game folder, its
real configuration parameters, and how to validate an installation. It never touches the game folder
itself: it produces an :class:`InstallPlan`, and the transactional installer applies the plan with
backups and rollback.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from akeno.core.models import Finding, Game, GraphicsApi, Severity, SystemInfo, GpuInfo
from akeno.security.catalog import Package


@dataclass
class ParamSpec:
    key: str                     # "Section.Key" for INI-backed settings
    label: str
    kind: str                    # bool | float | int | choice
    default: Any
    help: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: list[tuple[Any, str]] = field(default_factory=list)
    apis: list[str] = field(default_factory=list)   # empty = all APIs
    requires_restart: bool = False

    def applies_to(self, api: GraphicsApi) -> bool:
        return not self.apis or api.value in self.apis

    def coerce(self, value: Any) -> Any:
        if self.kind == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if self.kind in ("float", "int"):
            v = float(value)
            if self.minimum is not None and v < self.minimum:
                raise ValueError(f"{self.label}: {v} is below the supported minimum {self.minimum}")
            if self.maximum is not None and v > self.maximum:
                raise ValueError(f"{self.label}: {v} is above the supported maximum {self.maximum}")
            return int(round(v)) if self.kind == "int" else round(v, 4)
        if self.kind == "choice":
            allowed = [c[0] for c in self.choices]
            for a in allowed:
                if str(a) == str(value):
                    return a
            raise ValueError(f"{self.label}: unsupported value {value!r}")
        return value


@dataclass
class ProfileDef:
    id: str            # disabled | performance | balanced | quality | custom
    label: str
    summary: str
    params: dict[str, Any]
    tradeoff: str
    reference: str | None = None   # upstream measurement this preset is based on (cited, not ours)


@dataclass
class FileOp:
    rel_path: str                                    # relative to the game's exe directory
    source: Path | None = None                       # file to copy
    content: bytes | None = None                     # or generated content
    kind: str = "file"                               # file | config | marker
    description: str = ""
    expected_sha256: str | None = None


@dataclass
class InstallPlan:
    backend_id: str
    package: Package | None
    target_dir: str
    ops: list[FileOp] = field(default_factory=list)
    rename_away: list[str] = field(default_factory=list)  # existing files renamed to *.akeno-disabled
    params: dict[str, Any] = field(default_factory=dict)
    summary: list[str] = field(default_factory=list)
    kind: str = "install"                             # install | configure | swap
    expected_originals: dict[str, str] = field(default_factory=dict)  # explicit reviewed swap preconditions
    # Existing files the user approved to overwrite; their originals are backed up and restored on uninstall.
    replace_foreign: set[str] = field(default_factory=set)
    # Proceed although managed files were changed after installation (their backups are still verified).
    force: bool = False


@dataclass
class BackendContext:
    """What an adapter may consult while planning (keeps adapters testable)."""
    system: SystemInfo | None
    gpu: GpuInfo | None
    runtime_lookup: Callable[[str], dict | None]       # file name -> runtime record (verified) or None
    staging_dir: Path | None = None                   # extracted, hash-checked package files
    managed_files: set[str] = field(default_factory=set)  # lower-case rel paths AKENO installed in this dir
    allow_disable: list[str] = field(default_factory=list)  # user-approved files to rename away
    strict_conflicts: bool = True                    # unknown graphics hooks block installation
    loaded_modules: tuple[str, ...] = ()             # exact cached observations, not arbitrary filenames


class BackendAdapter(ABC):
    id: str = ""
    name: str = ""
    short: str = ""
    experimental: bool = True
    supported_apis: tuple[GraphicsApi, ...] = ()
    supported_arch: tuple[str, ...] = ("x64",)
    description: str = ""
    homepage: str = ""
    license: str = ""

    # -- analysis ------------------------------------------------------------------------------
    @abstractmethod
    def check(self, game: Game, ctx: BackendContext) -> list[Finding]:
        """Requirements, conflicts and game specific findings for this backend."""

    def required_package_files(self, game: Game, package: Package) -> dict[str, str | None]:
        """Package members (relative to package root) that must be extracted for this game."""
        return {}

    def dependency_specs(self, game: Game) -> list[tuple[str, bool]]:
        """(dependency key, required) for this game. Keys: package, nr_runtime, dlss_runtime, reshade, consumer.
        See :mod:`akeno.deps.resolver` for how each key is found, obtained or explained."""
        return [("package", True)]

    @abstractmethod
    def plan(self, game: Game, package: Package, params: dict[str, Any], ctx: BackendContext) -> InstallPlan:
        """Files to write for an installation (or reconfiguration)."""

    def plan_configure(self, game: Game, package: Package | None, params: dict[str, Any],
                       ctx: BackendContext, current_config: str | None) -> InstallPlan | None:
        """Plan that only rewrites configuration of an installed backend; None when not configurable."""
        return None

    def validate(self, game: Game, installed: dict[str, str]) -> list[Finding]:
        """Post-install validation given rel_path -> sha256 of what was written."""
        return []

    # -- settings ------------------------------------------------------------------------------
    def param_specs(self, game: Game | None = None) -> list[ParamSpec]:
        return []

    def profiles(self, game: Game | None = None) -> list[ProfileDef]:
        return []

    def default_params(self, game: Game | None) -> dict[str, Any]:
        return {s.key: s.default for s in self.param_specs(game)}

    # -- helpers ---------------------------------------------------------------------------------
    @staticmethod
    def finding(sev: Severity, code: str, msg: str, evidence: str | None = None) -> Finding:
        return Finding(sev, code, msg, evidence)


class BackendRegistry:
    def __init__(self, adapters: list[BackendAdapter]):
        self._by_id = {a.id: a for a in adapters}

    def all(self) -> list[BackendAdapter]:
        return list(self._by_id.values())

    def get(self, backend_id: str | None) -> BackendAdapter | None:
        return self._by_id.get(backend_id or "")


def default_registry() -> BackendRegistry:
    from akeno.backends.dlss5_feeder import Dlss5FeederAdapter
    from akeno.backends.optiscaler_nr import OptiScalerNrAdapter
    return BackendRegistry([OptiScalerNrAdapter(), Dlss5FeederAdapter()])
