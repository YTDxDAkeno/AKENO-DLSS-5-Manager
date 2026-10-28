"""Curated game-port profiles. No adapter or firewall changes are performed on import.

Only these explicit ports may be requested by automatic LAN setup. A profile's presence does not claim Windows
broadcast discovery works; OpenTTD's evidence is a real Linux relay lab, while the other profiles remain untested.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

BLOCKED_PORTS = frozenset({53, 67, 68, 123, 135, 137, 138, 139, 161, 162, 445, 500, 1900, 3389, 4500, 5353, 5355})


@dataclass(frozen=True)
class GameProfile:
    id: str
    name: str
    executables: tuple[str, ...]
    ports: tuple[tuple[str, int], ...]
    method: str = 'virtual_ip'
    evidence: str = 'not_tested'
    discovery_verified_windows: bool = False

    def validate(self):
        if any(proto not in ('TCP', 'UDP') or type(port) is not int or not 1024 <= port <= 65535
               or port in BLOCKED_PORTS for proto, port in self.ports):
            raise ValueError('Only explicit game ports are permitted.')
        return self


PROFILES = {
    'openttd': GameProfile('openttd', 'OpenTTD', ('openttd.exe',), (('TCP',3979),('UDP',3979)),
                          evidence='linux_wireguard_relay_lab'),
    'factorio': GameProfile('factorio', 'Factorio', ('factorio.exe',), (('UDP',34197),)),
    'terraria': GameProfile('terraria', 'Terraria', ('Terraria.exe','TerrariaServer.exe'), (('TCP',7777),)),
}


def profile(ident: str) -> GameProfile:
    try:
        return PROFILES[ident].validate()
    except (KeyError, TypeError):
        raise ValueError('This game has no reviewed LAN port profile.') from None


def catalog() -> list[dict]:
    return [{'id':p.id,'name':p.name,'executables':list(p.executables),
             'ports':[{'protocol':proto,'port':port} for proto,port in p.ports], 'method':p.method,
             'evidence':p.evidence, 'discovery_verified_windows':p.discovery_verified_windows}
            for p in (item.validate() for item in PROFILES.values())]


def installed_program(ident: str, games) -> Path | None:
    """Find a reviewed executable using the existing library, including nested launcher paths.

    No recursive filesystem scan or executable launch. Library metadata is a suggestion:
    require an existing file inside its canonical install directory and a reviewed basename.
    The privileged helper still independently validates the selected program and port profile.
    """
    reviewed = profile(ident)
    names = {name.casefold() for name in reviewed.executables}
    for game in games:
        directory = getattr(game, 'install_dir', None)
        if not isinstance(directory, str) or not directory.strip():
            continue
        try:
            root = Path(directory).resolve(strict=True)
            if not root.is_dir():
                continue
        except (OSError, ValueError, RuntimeError):
            continue
        recorded = [getattr(game, 'exe_path', None), *(getattr(game, 'exe_candidates', None) or [])]
        for value in [*recorded, *(root / name for name in reviewed.executables)]:
            if not isinstance(value, (str, Path)) or not str(value).strip():
                continue
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = root / candidate
            try:
                candidate = candidate.resolve(strict=True)
                if candidate.is_relative_to(root) and candidate.name.casefold() in names and candidate.is_file():
                    return candidate
            except (OSError, ValueError, RuntimeError):
                continue
    return None
