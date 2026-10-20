"""Reject links, junctions, unsafe Windows names and foreign transaction temp files."""
from pathlib import Path
from akeno.security.archive import normalize_member


def is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def guard_target(target: Path, app: Path, rel_paths=()) -> None:
    if is_link(app) or is_link(target) or target.resolve() != app.resolve() / "plugins":
        raise PermissionError("FiveM plugin target is linked or escaped the application directory")
    for rel in rel_paths:
        name = normalize_member(rel)
        if not name:
            raise PermissionError("An empty plugin file path is invalid")
        path = target / name
        if not path.resolve().is_relative_to(target.resolve()):
            raise PermissionError("Plugin file escaped the target")
        for p in (path, *path.parents):
            if p == app:
                break
            if is_link(p):
                raise PermissionError("Linked plugin paths and Windows junctions are not permitted")
        temporary = path.with_name(path.name + ".akeno-tmp")
        if temporary.exists() or is_link(temporary):
            raise PermissionError("An existing transaction temporary file needs manual inspection")
