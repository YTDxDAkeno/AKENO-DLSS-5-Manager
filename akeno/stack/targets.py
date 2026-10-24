"""Where a stack is installed and how the target is started.

``GameTarget``: the folder of the game's executable (proxy DLLs, ReShade and add-ons go next to it).
``FiveMTarget``: ``FiveM.app``. Mod files go to ``FiveM.app/plugins`` (Cfx ``ScanForReshades`` loads graphics
mods only from there, ReShade only under a graphics DLL name such as ``d3d11.dll``) and the one client
setting FiveM itself asks for lives in ``FiveM.app/CitizenFX.ini``. GTA V's own folder is never written.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from akeno.core import processes
from akeno.core.models import Game, GraphicsApi
from akeno.paths import is_windows

from .errors import GameRunning, StackError
from .transports import Transport, choose

# Cfx ReShadeFixups.cpp (commit 4c4fe28): ReShade >= 5 loads only with this machine-bound acknowledgement.
RESHADE5_TEXT = "ID:{:08x} acknowledged that ReShade 5.x has a bug that will lead to game crashes"
FIVEM_PROCESS = re.compile(r"^FiveM(?:_b\d+)?(?:_GTAProcess|_ChromeBrowser|_SteamChild)?\.exe$", re.I)


@dataclass
class GameTarget:
    game: Game
    kind: str = "game"
    prefix: str = ""
    api_value: str | None = None          # the renderer My Games uses (profile or the user's choice)
    transport: Transport | None = None    # how ReShade gets in for that renderer and bitness

    def __post_init__(self):
        if self.transport is None:
            try:
                self.transport = choose(self.api, self.game.architecture or "x64", self.root)
            except ValueError:
                self.transport = None

    @property
    def root(self) -> Path:
        return Path(self.game.target_dir or self.game.install_dir)

    @property
    def api(self) -> str:
        return self.api_value or self.game.api.value

    @property
    def arch(self) -> str:
        return "x86" if self.game.architecture == "x86" else "x64"

    @property
    def reshade_name(self) -> str | None:
        """The local DLL name ReShade loads under; None when it is a Vulkan layer."""
        return self.transport.reshade_name if self.transport else "dxgi.dll"

    def rel(self, name: str) -> str:
        return self.prefix + name

    def running(self) -> list:
        try:
            return processes.processes_in_dir(self.game.install_dir)
        except Exception:
            return []

    def ensure_stopped(self) -> None:
        running = self.running()
        if running:
            raise GameRunning(", ".join(sorted({p.name for p in running})))

    def launch(self, connect: str | None = None, args: str | None = None) -> None:
        if not is_windows():
            raise StackError("Starting games requires Windows.", code="not_windows")
        g = self.game
        args = (args or "").strip()
        if g.launcher == "steam" and re.fullmatch(r"\d+", str(g.launcher_id or "")):
            if args:                                                    # Steam passes these to the game
                from urllib.parse import quote
                os.startfile(f"steam://run/{g.launcher_id}//{quote(args)}/")    # noqa: S606
            else:
                os.startfile(f"steam://rungameid/{g.launcher_id}")          # noqa: S606 - Steam protocol handler
        elif g.launcher == "epic" and g.launcher_id and re.fullmatch(r"[A-Za-z0-9:._-]+", g.launcher_id) and not args:
            os.startfile(f"com.epicgames.launcher://apps/{g.launcher_id}?action=launch&silent=true")
        elif g.exe_path and os.path.isfile(g.exe_path):
            # a command line string keeps the arguments exactly as typed (Windows parses them in the game)
            subprocess.Popen(f'"{g.exe_path}" {args}' if args else [g.exe_path], cwd=os.path.dirname(g.exe_path))
        else:
            raise StackError("The game executable was not found.", "Select the executable in My Games.", "no_exe")


def cfx_hash(text: bytes) -> int:
    """Cfx ``HashString``: Jenkins one-at-a-time over ASCII-lowercased *signed* chars (MSVC char is signed)."""
    h = 0
    for b in text:
        if 65 <= b <= 90:
            b += 32
        c = b - 256 if b >= 128 else b
        h = (h + c) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= h >> 11
    h = (h + (h << 15)) & 0xFFFFFFFF
    return h


def computer_name_bytes() -> bytes:
    """What Cfx's ``getenv("COMPUTERNAME")`` sees (ANSI code page on Windows; ``"a"`` when unset)."""
    name = os.environ.get("COMPUTERNAME")
    if not name:
        return b"a"
    try:
        return name.encode("mbcs" if is_windows() else "utf-8", errors="replace")
    except LookupError:
        return name.encode("utf-8", errors="replace")


def reshade5_value() -> str:
    return RESHADE5_TEXT.format(cfx_hash(computer_name_bytes()))


@dataclass
class FiveMTarget:
    game: Game
    installation: object                       # akeno.fivem.models.FiveMInstallation
    kind: str = "fivem"
    reshade_name: str = "d3d11.dll"
    prefix: str = "plugins/"
    notes: list[str] = field(default_factory=list)

    @property
    def root(self) -> Path:
        return Path(self.installation.app_dir)

    @property
    def plugins(self) -> Path:
        return self.root / "plugins"

    @property
    def citizen_ini(self) -> Path:
        return self.root / "CitizenFX.ini"

    @property
    def api(self) -> str:
        # FiveM Legacy creates a D3D11 device for GTA V Legacy (Cfx rage-graphics-five RenderHooks.cpp).
        return GraphicsApi.D3D11.value

    def rel(self, name: str) -> str:
        return self.prefix + name

    def running(self) -> list:
        try:
            procs = processes.list_processes()
        except Exception:
            return []
        # Any FiveM client counts: its GTAProcess holds plugins/*.dll open while it runs.
        return [p for p in procs if FIVEM_PROCESS.match(p.name)]

    def ensure_stopped(self) -> None:
        running = self.running()
        if running:
            raise GameRunning(", ".join(sorted({p.name for p in running})))

    # -- CitizenFX.ini ReShade acknowledgement -----------------------------------------------------
    def reshade_ack(self) -> str:
        """``present`` when FiveM will load ReShade 5+, ``missing`` when it will block it."""
        from akeno.backends import ini
        if not self.citizen_ini.is_file():
            return "missing"
        try:
            text = self.citizen_ini.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return "missing"
        value = ini.get_values(text).get("Addons", {}).get("ReShade5", "")
        return "present" if value.strip() == reshade5_value() else "missing"

    def reshade_ack_content(self) -> bytes:
        """CitizenFX.ini with FiveM's own ``[Addons] ReShade5`` acknowledgement added; nothing else changes."""
        from akeno.backends import ini
        text = self.citizen_ini.read_text(encoding="utf-8-sig", errors="replace") if self.citizen_ini.is_file() else ""
        return ini.set_values(text, {"Addons": {"ReShade5": reshade5_value()}}).encode("utf-8")

    def launch(self, connect: str | None = None, args: str | None = None) -> None:
        if not is_windows():
            raise StackError("Starting FiveM requires Windows.", code="not_windows")
        args = [str(self.installation.launcher)]
        if connect:
            addr = connect.strip()
            if not re.fullmatch(r"(cfx\.re/join/[A-Za-z0-9]+|[A-Za-z0-9.\-]+(:\d{1,5})?)", addr):
                raise StackError("The server address is not valid.", "Use host:port or a cfx.re/join link.",
                                 "bad_address")
            args.append("fivem://connect/" + addr)
        subprocess.Popen(args, cwd=str(self.installation.root))


def target_for(game: Game, installation=None, api: str | None = None):
    if game.launcher == "fivem":
        if installation is None:
            from akeno.fivem.discovery import inspect_installation
            installation = inspect_installation(game.install_dir)
        if installation is None:
            raise StackError("FiveM was not found at its recorded location.", "Scan games again.", "fivem_missing")
        return FiveMTarget(game, installation)
    if not game.exe_path or not game.target_dir:
        raise StackError("The game executable is unknown.", "Select the game's .exe in My Games.", "no_exe")
    return GameTarget(game, api_value=api)
