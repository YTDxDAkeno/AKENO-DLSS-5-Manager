"""Unpacking a verified update package, and checking a program folder against what was unpacked.

The package's SHA-256 is signed, so its bytes are exactly the release's. Unpacking still refuses everything that
could write outside the staging folder or surprise Windows, and stops at the limits the signed release states
(``file_count``, ``uncompressed_size_bytes``), counting the bytes really written, not the sizes the archive claims:

* absolute paths, drive letters, ``..``, empty or ``.`` parts, backslashes (the Release Manager stores only
  ``/``), names not in Unicode NFC (as the Release Manager requires them);
* ``:`` (alternate data streams), control characters, trailing dots or spaces, reserved device names (``CON``,
  ``NUL``, ``COM1`` …), names longer than Windows handles without long-path support;
* symbolic links and any other special file, encrypted entries;
* two entries that are the same file on Windows (case-insensitive), a file that is also a folder;
* more files or more bytes than the release states (ZIP bombs), an entry larger than it declares.

The Release Manager's content aggregate (:func:`content_sha256`) is computed from the bytes really unpacked, so
the caller can compare it with the signed value before anything else uses the files.

What was unpacked is recorded as a content list (relative path → SHA-256 and size). The updater checks the staged
folder against it before it copies anything, copies exactly the listed files, and checks the copy again before the
switch, so nothing changed or added between verification and installation reaches the program folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
import zipfile
import zlib
from pathlib import Path, PurePosixPath

CHUNK = 1024 * 1024
HARD_FILES = 4096
HARD_BYTES = 2 * 2 ** 30
MAX_PATH = 240
RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³]|conin\$|conout\$)(\..*)?$", re.I)
BAD_CHARS = re.compile(r'[\x00-\x1f<>:"|?*]')


class StagingError(Exception):
    pass


def _reparse(path: Path) -> bool:
    """A symbolic link or a junction (or another reparse point on Windows)."""
    try:
        if path.is_symlink():
            return True
        st = os.lstat(path)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & 0x400)                          # FILE_ATTRIBUTE_REPARSE_POINT


def no_links_on_the_way(path: Path, stop: Path) -> None:
    """``path`` and every folder below ``stop`` on the way to it are real folders, not links or junctions pointing
    elsewhere (``stop`` itself may be one: a data folder the user moved to another drive)."""
    path, stop = Path(path).absolute(), Path(stop).absolute()
    current = path
    while current != stop:
        if current.exists() and _reparse(current):
            raise StagingError(f"refused: {current} is a link or junction")
        if not current.is_relative_to(stop) or current.parent == current:
            return
        current = current.parent


def safe_name(raw: str) -> PurePosixPath:
    """The entry's path, or StagingError when it is not a plain relative path Windows stores as named."""
    name = raw
    if not name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name) \
            or len(name) > MAX_PATH or unicodedata.normalize("NFC", name) != name:
        raise StagingError(f"refused path in the package: {raw!r}")
    parts = name.rstrip("/").split("/")
    for part in parts:
        if part in ("", ".", "..") or BAD_CHARS.search(part) or part != part.rstrip(". ") \
                or RESERVED.match(part):
            raise StagingError(f"refused path in the package: {raw!r}")
    return PurePosixPath(*parts)


def _kind(info: zipfile.ZipInfo) -> str:
    """``dir`` exactly when ``ZipInfo.is_dir()`` (the name ends with ``/``), as the Release Manager counts entries:
    every other entry is a file and part of ``content_sha256``, whatever its MS-DOS attributes say."""
    if info.flag_bits & 0x1:
        raise StagingError(f"refused encrypted entry in the package: {info.filename!r}")
    if info.orig_filename != info.filename:          # a NUL byte, or a backslash zipfile turned into "/"
        raise StagingError(f"refused path in the package: {info.orig_filename!r}")
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    if info.is_dir():
        if kind not in (0, stat.S_IFDIR):
            raise StagingError(f"refused special entry in the package: {info.filename!r}")
        return "dir"
    if kind not in (0, stat.S_IFREG):
        raise StagingError(f"refused link or special file in the package: {info.filename!r}")
    return "file"


def content_sha256(entries: list[dict]) -> str:
    """The Release Manager's content aggregate of a portable ZIP.

    One record per regular file: ``name`` (the path exactly as stored in the ZIP, top folder included, not
    normalised), ``sha256`` (of the decompressed bytes) and ``size_bytes`` (the real decompressed size). The records
    are sorted by name in Unicode code point order and encoded as compact JSON (UTF-8, ``ensure_ascii=False``, keys
    sorted, no BOM, no trailing newline); the aggregate is the SHA-256 of those bytes."""
    return hashlib.sha256(canonical_records(entries)).hexdigest()


def canonical_records(entries: list[dict]) -> bytes:
    """The exact bytes :func:`content_sha256` hashes (``canonical`` of the Release Manager)."""
    records = sorted(({"name": e["name"], "sha256": e["sha256"], "size_bytes": e["size_bytes"]} for e in entries),
                     key=lambda r: r["name"])
    return json.dumps(records, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
                      allow_nan=False).encode("utf-8")


def is_pe(path: Path) -> bool:
    """A Windows program or library (PE image): ``MZ`` header with a ``PE\\0\\0`` signature."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
            if len(head) < 64 or head[:2] != b"MZ":
                return False
            offset = int.from_bytes(head[60:64], "little")
            if offset <= 0 or offset > 1 << 20:
                return False
            fh.seek(offset)
            return fh.read(4) == b"PE\0\0"
    except OSError:
        return False


def extract(archive: Path, dest: Path, max_files: int = HARD_FILES, max_bytes: int = HARD_BYTES,
            progress=None) -> dict:
    """Unpack ``archive`` into the new, empty folder ``dest``; returns the content list (see the module docstring).
    ``progress(done_bytes, total_bytes)`` is called while unpacking."""
    dest = Path(dest)
    max_files, max_bytes = min(int(max_files), HARD_FILES), min(int(max_bytes), HARD_BYTES)
    dest.mkdir(parents=True, exist_ok=False)
    files: dict[str, dict] = {}
    entries: list[dict] = []                  # per regular file, under its name as stored (content_sha256)
    folded: dict[str, str] = {}
    dirs: set[str] = set()
    written = 0
    try:
        z = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        raise StagingError(f"the package is not a readable ZIP archive ({exc})") from exc
    with z:
        infos = z.infolist()
        if sum(1 for i in infos if not i.is_dir()) > max_files:
            raise StagingError(f"the package has more files than the release states ({max_files})")
        if sum(i.file_size for i in infos) > max_bytes:
            raise StagingError("the package unpacks to more data than the release states")
        plan = []
        for info in infos:
            kind = _kind(info)
            name = safe_name(info.filename)
            key = name.as_posix()
            low = key.casefold()
            if low in folded and (kind == "file" or folded[low] != "dir"):
                raise StagingError(f"the package names the same file twice: {info.filename!r}")
            folded[low] = kind
            for parent in list(name.parents)[:-1]:
                p = parent.as_posix().casefold()
                if folded.get(p) == "file":
                    raise StagingError(f"the package uses a file as a folder: {info.filename!r}")
                dirs.add(p)
            if kind == "file" and low in dirs:
                raise StagingError(f"the package uses a folder as a file: {info.filename!r}")
            plan.append((info, kind, name))
        total = sum(i.file_size for i, k, _ in plan if k == "file")
        for info, kind, name in plan:
            target = dest.joinpath(*name.parts)
            if kind == "dir":
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            no_links_on_the_way(target.parent, dest)
            h = hashlib.sha256()
            size = 0
            try:
                with z.open(info) as src, open(target, "xb") as out:
                    while True:
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        size += len(chunk)
                        written += len(chunk)
                        if size > info.file_size or written > max_bytes:
                            raise StagingError("the package unpacks to more data than it declares (refused)")
                        h.update(chunk)
                        out.write(chunk)
                        if progress:
                            progress(written, total)
            except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
                raise StagingError(f"the package is damaged ({info.filename}: {exc})") from exc
            files[name.as_posix()] = {"sha256": h.hexdigest(), "size": size}
            entries.append({"name": info.filename, "sha256": h.hexdigest(), "size_bytes": size})
    return {"files": files, "file_count": len(files), "bytes": written, "content_sha256": content_sha256(entries)}


def program_root(dest: Path) -> Path:
    """The folder that holds AKENO.exe: the staging folder itself, or its single top folder."""
    entries = list(Path(dest).iterdir())
    if len(entries) == 1 and entries[0].is_dir() and not _reparse(entries[0]):
        return entries[0]
    return Path(dest)


def relative_to_root(content: dict, prefix: str) -> dict:
    """The content list relative to the program root (``prefix`` is the single top folder, or "")."""
    if not prefix:
        return content
    lead = prefix.rstrip("/") + "/"
    files = {k[len(lead):]: v for k, v in content["files"].items() if k.startswith(lead)}
    if len(files) != len(content["files"]):
        raise StagingError("the package has files outside its program folder")
    return {**content, "files": files, "file_count": len(files)}


def pe_count(root: Path, content: dict) -> int:
    return sum(1 for rel in content["files"] if is_pe(Path(root).joinpath(*rel.split("/"))))


def walk(root: Path) -> dict[str, Path]:
    """Every file below ``root`` by relative path (links and junctions are refused, not followed)."""
    root = Path(root)
    out: dict[str, Path] = {}
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(current)
        for d in list(dirnames):
            if _reparse(here / d):
                raise StagingError(f"refused: {here / d} is a link or junction")
        for f in filenames:
            p = here / f
            if _reparse(p) or not p.is_file():
                raise StagingError(f"refused: {p} is not a plain file")
            out[p.relative_to(root).as_posix()] = p
    return out


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(root: Path, content: dict, extra_allowed: tuple[str, ...] = ()) -> None:
    """``root`` holds exactly the listed files with the listed SHA-256 (``extra_allowed``: top-level names that
    may also be there, such as a portable data folder that was carried over). Raises StagingError."""
    found = walk(root)
    expected = content["files"]
    extra = sorted(k for k in found if k not in expected and k.split("/", 1)[0] not in extra_allowed)
    missing = sorted(k for k in expected if k not in found)
    if missing or extra:
        raise StagingError(f"the program files differ from the verified package (missing {missing[:3]}, "
                           f"unexpected {extra[:3]})")
    for rel, want in expected.items():
        p = found[rel]
        if p.stat().st_size != want["size"] or sha256_of(p) != want["sha256"]:
            raise StagingError(f"{rel} differs from the verified package")


def save(content: dict, path: Path) -> None:
    write_atomic(Path(path), json.dumps(content, sort_keys=True).encode("utf-8"))


def load(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise StagingError("the content list is not readable")
    return data


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` so that ``path`` is either the old or the complete new content, even after a power cut."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
