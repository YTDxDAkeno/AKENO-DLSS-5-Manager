"""Controlled ZIP extraction.

Rejects absolute paths, drive letters, ``..`` components, symlinks, device names, oversized
members and zip bombs. Members are extracted only when explicitly requested (``wanted``), and each
extracted file is checked against the hash pinned in the catalog.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

# nvngx_dlssnr.dll (the DLSS 5 model) alone is 165.8 MB uncompressed.
MAX_MEMBER = 256 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_RATIO = 200
_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.I)


class ArchiveError(Exception):
    pass


def normalize_member(name: str) -> str:
    """Normalise a member name to a safe relative POSIX path or raise ``ArchiveError``."""
    n = name.replace("\\", "/")
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n) or n.startswith("//"):
        raise ArchiveError(f"Absolute path in archive: {name!r}")
    parts = [p for p in PurePosixPath(n).parts if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ArchiveError(f"Path traversal in archive: {name!r}")
    for p in parts:
        if _RESERVED.match(p) or any(c in p for c in '<>:"|?*') or p.endswith((" ", ".")):
            raise ArchiveError(f"Unsafe file name in archive: {name!r}")
    return "/".join(parts)


def inspect(zip_path: str | os.PathLike) -> list[tuple[str, int]]:
    """Validate the whole archive and return (normalised name, size) for every file member."""
    out = []
    total = 0
    try:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                name = normalize_member(info.filename)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ArchiveError(f"Symbolic link in archive: {info.filename!r}")
                if info.is_dir() or not name:
                    continue
                if info.file_size > MAX_MEMBER:
                    raise ArchiveError(f"Archive member too large: {info.filename!r}")
                if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_RATIO:
                    raise ArchiveError(f"Suspicious compression ratio: {info.filename!r}")
                total += info.file_size
                if total > MAX_TOTAL:
                    raise ArchiveError("Archive expands beyond the allowed size.")
                out.append((name, info.file_size))
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"Not a valid ZIP archive: {exc}") from exc
    names = [n.lower() for n, _ in out]
    if len(set(names)) != len(names):
        raise ArchiveError("Archive contains duplicate file names.")
    return out


def extract(zip_path: str | os.PathLike, dest: str | os.PathLike, wanted: dict[str, str | None],
            root: str = "") -> dict[str, Path]:
    """Extract only ``wanted`` members (relative to ``root``) into ``dest``.

    ``wanted`` maps relative member path -> expected SHA-256 (or None to skip hash pinning, which
    callers use only for files that are not executed, e.g. documentation). Returns rel -> path.
    """
    members = dict(inspect(zip_path))
    dest_p = Path(dest).resolve()
    dest_p.mkdir(parents=True, exist_ok=True)
    root = normalize_member(root) if root else ""
    out: dict[str, Path] = {}
    with zipfile.ZipFile(zip_path) as z:
        by_norm = {normalize_member(i.filename): i for i in z.infolist() if not i.is_dir()}
        for rel, expected in wanted.items():
            rel_n = normalize_member(rel)
            full = f"{root}/{rel_n}" if root else rel_n
            info = by_norm.get(full)
            if info is None or full not in members:
                raise ArchiveError(f"Expected file missing from package: {full}")
            target = (dest_p / rel_n).resolve()
            if dest_p not in target.parents:
                raise ArchiveError(f"Refusing to write outside the extraction folder: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()
            written = 0
            tmp = target.with_name(target.name + ".tmp")
            with z.open(info) as src, open(tmp, "wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_MEMBER:
                        dst.close()
                        tmp.unlink(missing_ok=True)
                        raise ArchiveError(f"Archive member exceeds its declared size: {rel}")
                    h.update(chunk)
                    dst.write(chunk)
            if expected and h.hexdigest() != expected.lower():
                tmp.unlink(missing_ok=True)
                raise ArchiveError(f"Hash mismatch for {rel}: the package content does not match the reviewed release.")
            os.replace(tmp, target)
            out[rel_n] = target
    return out
