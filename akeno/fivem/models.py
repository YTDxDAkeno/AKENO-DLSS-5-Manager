"""FiveM-specific evidence, kept outside shared launcher domain models."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from akeno.core.models import Game


class Edition(str, Enum):
    LEGACY = "legacy"
    ENHANCED = "enhanced"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InventoryFile:
    path: Path
    kind: str
    sha256: str


@dataclass
class FiveMInstallation:
    root: Path
    app_dir: Path
    launcher: Path
    gta_dir: Path | None
    edition: Edition
    game: Game
    inventory: tuple[InventoryFile, ...] = ()
    errors: tuple[str, ...] = ()
    saved_build: str | None = None  # configured preference, never proof of the running build
    update_channel: str | None = None
    fingerprint: str = ""

    @property
    def plugin_dir(self) -> Path:
        return self.app_dir / "plugins"


@dataclass(frozen=True)
class ServerPolicy:
    """Operator-supplied evidence, not remote attestation or a guarantee against bans.

    Never infer permission from ownership, a reachable server, or missing anti-cheat files.
    Values must describe the effective server configuration, including exec'd configs.
    """
    server_id: str = ""
    own_server: bool = False
    permission_reference: str = ""
    graphics_plugins_allowed: bool | None = None
    pure_level: int | None = None
    script_hook_allowed: bool | None = None
    allowed_backends: frozenset[str] = field(default_factory=frozenset)
    known_graphics_hashes: frozenset[str] = field(default_factory=frozenset)
    experimental_consent: bool = False
