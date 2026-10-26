"""Reading metadata from mod archives (JAR/ZIP) without extracting or running anything.

Limits that keep a hostile or broken archive from exhausting the PC:
* the archive file itself: at most ``MAX_ARCHIVE`` bytes;
* the central directory: at most ``MAX_ENTRIES`` entries;
* one metadata entry: at most ``MAX_ENTRY`` bytes uncompressed and a compression ratio of at most ``MAX_RATIO``
  (a "zip bomb" expands far beyond that);
* nested archives (Fabric's jar-in-jar): one level, each at most ``MAX_NESTED`` bytes, read in memory.
Only the named entries are read; nothing is written to disk.
"""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass, field

MAX_ARCHIVE = 1024 * 2 ** 20
MAX_ENTRIES = 60_000
MAX_ENTRY = 4 * 2 ** 20
MAX_RATIO = 200
MAX_NESTED = 32 * 2 ** 20
MAX_NESTED_COUNT = 64


class ArchiveError(Exception):
    """The archive cannot be read safely (damaged, too large, a zip bomb)."""


@dataclass
class Archive:
    path: str
    names: list[str] = field(default_factory=list)
    _zip: zipfile.ZipFile | None = None
    _data: bytes | None = None

    def has(self, name: str) -> bool:
        return name in self._index

    @property
    def _index(self) -> set[str]:
        if not hasattr(self, "_idx"):
            self._idx = set(self.names)
        return self._idx

    def read(self, name: str) -> bytes:
        """One entry's bytes, within the size and ratio limits."""
        if self._zip is None:
            raise ArchiveError("closed")
        try:
            info = self._zip.getinfo(name)
        except KeyError as exc:
            raise ArchiveError(f"no entry {name}") from exc
        if info.file_size > MAX_ENTRY:
            raise ArchiveError(f"{name} is larger than {MAX_ENTRY} bytes")
        if info.compress_size and info.file_size / max(1, info.compress_size) > MAX_RATIO:
            raise ArchiveError(f"{name} expands {info.file_size // max(1, info.compress_size)}x (zip bomb?)")
        try:
            with self._zip.open(info) as f:
                data = f.read(MAX_ENTRY + 1)
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError) as exc:
            raise ArchiveError(f"{name}: {exc}") from exc
        if len(data) > MAX_ENTRY:
            raise ArchiveError(f"{name} is larger than declared")
        return data

    def text(self, name: str) -> str:
        return self.read(name).decode("utf-8-sig", errors="replace")

    def nested(self, name: str) -> "Archive":
        """An archive inside this one (read in memory, one level only)."""
        if self._data is not None:
            raise ArchiveError("nested archives are read one level deep only")
        try:
            info = self._zip.getinfo(name)
        except KeyError as exc:
            raise ArchiveError(f"no entry {name}") from exc
        if info.file_size > MAX_NESTED:
            raise ArchiveError(f"nested {name} is too large")
        if info.compress_size and info.file_size / max(1, info.compress_size) > MAX_RATIO:
            raise ArchiveError(f"nested {name} expands too much")
        with self._zip.open(info) as f:
            data = f.read(MAX_NESTED + 1)
        if len(data) > MAX_NESTED:
            raise ArchiveError(f"nested {name} is larger than declared")
        return _open_bytes(f"{self.path}!{name}", data)

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def open_archive(path: str) -> Archive:
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise ArchiveError(str(exc)) from exc
    if size > MAX_ARCHIVE:
        raise ArchiveError(f"larger than {MAX_ARCHIVE // 2 ** 20} MB")
    try:
        zf = zipfile.ZipFile(path, "r")
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ArchiveError(f"not a readable archive: {exc}") from exc
    return _wrap(path, zf, None)


def _open_bytes(label: str, data: bytes) -> Archive:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, ValueError) as exc:
        raise ArchiveError(f"nested archive unreadable: {exc}") from exc
    return _wrap(label, zf, data)


def _wrap(label: str, zf: zipfile.ZipFile, data: bytes | None) -> Archive:
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        zf.close()
        raise ArchiveError(f"more than {MAX_ENTRIES} entries")
    total = sum(i.file_size for i in infos)
    packed = sum(i.compress_size for i in infos) or 1
    if total > 64 * MAX_ARCHIVE or total / packed > MAX_RATIO:
        zf.close()
        raise ArchiveError("the archive expands far beyond its size (zip bomb?)")
    a = Archive(label, [i.filename for i in infos])
    a._zip = zf
    a._data = data
    return a
