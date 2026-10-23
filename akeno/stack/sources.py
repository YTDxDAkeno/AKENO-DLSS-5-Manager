"""Obtaining component files: verified downloads, bundled files, the official ReShade setup and local copies.

Every component version ends up as a *staged* folder under ``<cache>/components`` holding exactly the
files an installation copies, each with a known SHA-256. A staged folder is reused (offline reinstall,
downgrade, switching back) as long as its files still hash to the recorded values.
"""

from __future__ import annotations

from akeno.i18n import msg

import json
import logging
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from akeno.games import pe
from akeno.paths import is_windows, resource_dir
from akeno.security import archive
from akeno.security.downloads import CancelToken, Cancelled, DownloadManager, sha256_file

from .catalog import ComponentVersion, StackCatalog
from .errors import MissingDependency, StackError

log = logging.getLogger(__name__)

DOWNLOAD_HOSTS = ["github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
                  "codeload.github.com", "reshade.me"]
RESHADE_SETUP_MAX = 64 * 1024 * 1024
LOCAL_KEY = "stack-local-versions"
NR_DLL = "nvngx_dlssnr.dll"
SR_DLL = "nvngx_dlss.dll"
RENODX_DLL = "renodx-dlss5.addon64"

BINARY_SUFFIXES = (".dll", ".addon64", ".addon32", ".exe", ".asi")


def expected_machine(rel: str) -> str:
    """The bitness a release member must have: the Feeder's ``.addon32`` and dgVoodoo2's ``MS/x86`` files are
    32-bit, everything else (including the 64-bit helper host64\\dlss5-feed-host64.exe) is 64-bit."""
    low = rel.replace("\\", "/").lower()
    return "x86" if low.endswith(".addon32") or "/x86/" in "/" + low else "x64"


# File name a role's single file is staged under.
ROLE_FILE = {"nr_runtime": NR_DLL, "sr_runtime": SR_DLL, "consumer_renodx": RENODX_DLL, "reshade": "ReShade64.dll",
             "reshade32": "ReShade32.dll"}


@dataclass
class Staged:
    version: ComponentVersion
    folder: Path
    files: dict[str, Path] = field(default_factory=dict)       # logical relative name -> file
    hashes: dict[str, str] = field(default_factory=dict)

    def path(self, name: str) -> Path:
        return self.files[name]


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)[:60] or "v"


def _bundle_dirs() -> list[Path]:
    """Folders that may hold the CI-built dependency bundle (installed app, then a source checkout)."""
    out = [resource_dir() / "community" / "dependencies"]
    env = os.environ.get("AKENO_BUNDLE_DIR")
    if env:
        out.insert(0, Path(env))
    return out


def _vendor_dir() -> Path:
    # Source checkout: the reviewed shader sources live in community/vendor at the repository root.
    return Path(__file__).resolve().parents[2] / "community" / "vendor"


def reshade_identity(path: str | Path, machine: str = "x64") -> tuple[bool, str | None, str]:
    """(is full add-on ReShade of this bitness, version, reason)."""
    info = pe.parse(str(path))
    if not info.valid:
        return False, None, "not a valid Windows DLL"
    if info.machine != machine:
        return False, info.file_version, "not a 32-bit ReShade" if machine == "x86" else "not a 64-bit ReShade"
    if (info.strings.get("ProductName") or "").strip() != "ReShade":
        return False, info.file_version, "the file does not identify itself as ReShade"
    if not {"ReShadeRegisterAddon", "ReShadeUnregisterAddon"} <= set(info.exports):
        return False, info.file_version, "this ReShade build has no add-on support"
    return True, info.file_version, "ok"


def dll_version(path: str | Path) -> tuple[str | None, dict[str, str], str | None]:
    info = pe.parse(str(path))
    return info.file_version, dict(info.strings), info.machine if info.valid else None


class ComponentStore:
    def __init__(self, db, catalog: StackCatalog, downloads: DownloadManager | None = None,
                 root: Path | None = None):
        from akeno.paths import cache_dir
        self.db = db
        self.catalog = catalog
        self.root = Path(root or (cache_dir() / "components"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.downloads = downloads or DownloadManager(DOWNLOAD_HOSTS, cache=self.root.parent / "downloads")

    # -- bundled dependency manifest ------------------------------------------------------------
    def bundle(self) -> dict[str, tuple[Path, dict]]:
        """role -> (folder, manifest entry) of the CI-built community dependency bundle."""
        for folder in _bundle_dirs():
            manifest = folder / "manifest.json"
            if manifest.is_file():
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    return {c["role"]: (folder, c) for c in data.get("components", [])}
                except (OSError, ValueError, KeyError) as exc:
                    log.warning("dependency bundle unreadable: %s", exc)
        return {}

    def bundled_archive(self, sha256: str) -> Path | None:
        for folder in _bundle_dirs():
            manifest = folder / "manifest.json"
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for item in data.get("packages", []):
                path = (folder / item.get("path", "")).resolve()
                if item.get("sha256") == sha256 and path.is_file() and path.is_relative_to(folder.resolve()):
                    return path
        return None

    def bundled_available(self, v: ComponentVersion) -> bool:
        if v.bundle_role == "motion_provider":
            return "motion_provider" in self.bundle() or (_vendor_dir() / "dh-motion" / "dh_uber_motion.fx").is_file()
        return v.bundle_role in self.bundle()

    def _stage_bundled(self, v: ComponentVersion) -> Staged:
        entries = self.bundle()
        if v.bundle_role in ("reshade", "reshade32"):
            dll, machine = ("ReShade32.dll", "x86") if v.bundle_role == "reshade32" else ("ReShade64.dll", "x64")
            if v.bundle_role not in entries:
                raise MissingDependency("ReShade (AKENO build)",
                                        msg("This copy of AKENO was built without the ReShade bundle. Choose "
                                            "'official reshade.me add-on setup' or import {dll}.", dll=dll))
            folder, entry = entries[v.bundle_role]
            src = folder / entry["path"]
            self._check_hash(src, entry["sha256"])
            ok, version, why = reshade_identity(src, machine)
            if not ok:
                raise StackError(msg("The bundled ReShade is not usable: {reason}", reason=why), code="reshade_identity")
            return Staged(v, folder, {dll: src}, {dll: entry["sha256"]})
        if v.bundle_role == "motion_provider":
            if "motion_provider" in entries:
                folder, entry = entries["motion_provider"]
                files = {"Shaders/" + Path(entry["destination"]).name: folder / entry["path"]}
                hashes = {"Shaders/" + Path(entry["destination"]).name: entry["sha256"]}
                for a in entry.get("assets", []):
                    name = "Shaders/" + Path(a["destination"]).name
                    files[name], hashes[name] = folder / a["path"], a["sha256"]
            else:
                vendor = _vendor_dir()
                pins = json.loads((vendor / "pins.json").read_text(encoding="utf-8"))["files"]
                files = {"Shaders/dh_uber_motion.fx": vendor / "dh-motion" / "dh_uber_motion.fx",
                         "Shaders/ReShade.fxh": vendor / "reshade-framework" / "ReShade.fxh"}
                hashes = {"Shaders/dh_uber_motion.fx": pins["community/vendor/dh-motion/dh_uber_motion.fx"],
                          "Shaders/ReShade.fxh": pins["community/vendor/reshade-framework/ReShade.fxh"]}
            for name, path in files.items():
                self._check_hash(path, hashes[name])
            return Staged(v, files[next(iter(files))].parent, files, hashes)
        raise StackError(msg("Unknown bundled component {name}.", name=str(v.bundle_role)), code="bundle")

    @staticmethod
    def _check_hash(path: Path, expected: str) -> None:
        if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
            raise StackError(msg("A bundled file is missing or changed: {file}.", file=path.name),
                             "Reinstall AKENO to repair its files.", "bundle_changed")

    # -- staged download folders ------------------------------------------------------------------
    def folder(self, v: ComponentVersion) -> Path:
        tag = (v.sha256 or "unpinned")[:16]
        return self.root / _slug(v.component) / f"{_slug(v.version)}-{tag}"

    def _marker(self, folder: Path) -> Path:
        return folder / ".akeno-staged.json"

    def staged(self, v: ComponentVersion) -> Staged | None:
        """The verified staged copy of a version, or None if it must be obtained first."""
        if v.source == "bundled":
            try:
                return self._stage_bundled(v) if self.bundled_available(v) else None
            except StackError:
                return None
        if v.source == "local":
            return self._stage_local(v)
        folder = self.folder(v)
        marker = self._marker(folder)
        if not marker.is_file():
            return None
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        files, hashes = {}, {}
        for name, sha in data.get("files", {}).items():
            path = folder / name
            if not path.is_file() or path.is_symlink():
                return None
            files[name], hashes[name] = path, sha
        # Re-hash lazily: the installer verifies every byte before copying; here only sizes are checked.
        for name, size in data.get("sizes", {}).items():
            if files[name].stat().st_size != size:
                return None
        return Staged(v, folder, files, hashes)

    def is_ready(self, v: ComponentVersion) -> bool:
        return self.staged(v) is not None

    def download_size(self, v: ComponentVersion) -> int:
        if v.source != "download" or self.is_ready(v):
            return 0
        if v.sha256 and (self.downloads.cached_path(v.sha256, v.asset or "download").is_file()
                         or self.bundled_archive(v.sha256)):
            return 0
        return int(v.size or 0)

    def ensure(self, v: ComponentVersion, progress: Callable[[int, int | None], None] | None = None,
               token: CancelToken | None = None) -> Staged:
        existing = self.staged(v)
        if existing:
            return existing
        if v.source == "bundled":
            return self._stage_bundled(v)
        if v.source == "local":
            raise MissingDependency(v.display, "The imported file was removed from AKENO's library. Import it again.")
        if v.source == "reshade_setup":
            return self._stage_reshade_setup(v, progress, token)
        return self._stage_download(v, progress, token)

    def _stage_download(self, v, progress, token) -> Staged:
        if not v.url:
            raise StackError(msg("{version} has no download source.", version=v.display), code="no_source")
        name = v.asset or os.path.basename(v.url)
        bundled = self.bundled_archive(v.sha256) if v.sha256 else None
        if bundled is not None:
            # The offline dependency bundle carries this exact archive: seed the cache instead of downloading.
            dest = self.downloads.cached_path(v.sha256, name)
            if not dest.is_file() or sha256_file(dest) != v.sha256:
                shutil.copyfile(bundled, dest.with_suffix(".tmp"))
                os.replace(dest.with_suffix(".tmp"), dest)
        if v.sha256:
            res = self.downloads.fetch(v.url, v.sha256, name, v.size, progress, token)
        else:
            # Only unreviewed upstream versions without a published digest get here (explicitly confirmed).
            res = self.downloads.fetch_unpinned(v.url, name, 512 * 1024 * 1024, progress, token)
        if token and token.cancelled:
            raise Cancelled("Cancelled.")
        folder = self.folder(v) if v.sha256 else self.root / _slug(v.component) / f"{_slug(v.version)}-{res.sha256[:16]}"
        tmp = folder.with_name(folder.name + ".partial")
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            if zipfile.is_zipfile(res.path):
                wanted = dict(v.files)
                extracted = archive.extract(res.path, tmp, wanted, v.root)
            else:
                # A single-file asset (not used by the bundled catalog, possible for discovered releases).
                only = next(iter(v.files))
                tmp.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(res.path, tmp / only)
                extracted = {only: tmp / only}
            hashes = {rel: sha256_file(p) for rel, p in extracted.items()}
            self._check_identity(v, extracted)
            sizes = {rel: p.stat().st_size for rel, p in extracted.items()}
            self._marker(tmp).write_text(json.dumps({"component": v.component, "version": v.version,
                                                    "archive_sha256": res.sha256, "files": hashes,
                                                    "sizes": sizes}, indent=1), encoding="utf-8")
            shutil.rmtree(folder, ignore_errors=True)
            folder.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, folder)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        staged = self.staged(v) if v.sha256 else Staged(v, folder, {r: folder / r for r in hashes}, hashes)
        if staged is None:
            raise StackError(msg("{version} could not be staged.", version=v.display), code="stage")
        return staged

    def _check_identity(self, v: ComponentVersion, files: dict[str, Path]) -> None:
        """Name/type checks that matter for unreviewed versions (reviewed ones are pinned by hash anyway)."""
        component = self.catalog.components.get(v.component)
        role = component.role if component else None
        for rel, path in files.items():
            if rel.lower().endswith(BINARY_SUFFIXES):
                version, strings, machine = dll_version(path)
                if machine is None:
                    raise StackError(msg("{file} in {version} is not a valid Windows binary.", file=rel,
                                         version=v.display), code="bad_binary")
                want = expected_machine(rel)
                if machine != want:
                    raise StackError(msg("Selected release does not contain the expected {want} file ({file} is {arch}).",
                                         want=want, file=rel, arch=machine), code="wrong_architecture")
        if role == "nr_runtime" and NR_DLL in files:
            desc = " ".join(dll_version(files[NR_DLL])[1].values()).lower()
            if desc and "dlss" not in desc:
                raise StackError("The selected file is not the DLSS 5 Neural Rendering model.", code="wrong_runtime")

    def _stage_reshade_setup(self, v, progress, token) -> Staged:
        res = self.downloads.fetch_unpinned(v.url, os.path.basename(v.url), RESHADE_SETUP_MAX, progress, token)
        x86 = self.catalog.component(v.component).role == "reshade32"
        name, machine = ("ReShade32.dll", "x86") if x86 else ("ReShade64.dll", "x64")
        folder = self.root / "reshade" / f"setup-{res.sha256[:16]}"
        dll = folder / name
        if not dll.is_file():
            folder.mkdir(parents=True, exist_ok=True)
            try:
                with zipfile.ZipFile(res.path) as z:      # the setup carries a ZIP appended to the executable
                    if name not in z.namelist():
                        raise StackError(msg("The ReShade setup does not contain {name}.", name=name), code="reshade_setup")
                    info = z.getinfo(name)
                    if info.file_size > 64 * 1024 * 1024:
                        raise StackError(msg("{name} in the setup is unexpectedly large.", name=name), code="reshade_setup")
                    with z.open(info) as src, open(dll.with_suffix(".tmp"), "wb") as out:
                        shutil.copyfileobj(src, out)
                    os.replace(dll.with_suffix(".tmp"), dll)
            except zipfile.BadZipFile as exc:
                shutil.rmtree(folder, ignore_errors=True)
                raise StackError("The official ReShade setup format changed; AKENO cannot read it.",
                                 msg("Install ReShade with its own setup once and import {name}.", name=name),
                                 "reshade_setup") from exc
        ok, version, why = reshade_identity(dll, machine)
        if not ok or not str(version or "").startswith(v.version):
            dll.unlink(missing_ok=True)
            raise StackError(msg("The downloaded ReShade is not usable: {reason}",
                                 reason=why if not ok else "version " + str(version)), code="reshade_identity")
        sha = sha256_file(dll)
        return Staged(v, folder, {name: dll}, {name: sha})

    # -- local copies -------------------------------------------------------------------------------
    def local_versions(self) -> list[ComponentVersion]:
        out = []
        for row in self.db.get_setting(LOCAL_KEY, []) or []:
            try:
                out.append(ComponentVersion(component=row["component"], version=row["version"], source="local",
                                            label=row.get("label") or row["version"], channel="local",
                                            sha256=row["sha256"], files={row["file"]: row["sha256"]},
                                            gpus=tuple(row.get("gpus", [])), signed=row.get("signed", "none"),
                                            local_path=row["path"], reviewed=bool(row.get("known")),
                                            notes=tuple(row.get("notes", []))))
            except KeyError:
                continue
        return out

    def _stage_local(self, v: ComponentVersion) -> Staged | None:
        path = Path(v.local_path or "")
        if not path.is_file() or path.is_symlink():
            return None
        name = next(iter(v.files))
        return Staged(v, path.parent, {name: path}, {name: v.sha256 or ""})

    def import_file(self, role: str, source: str | Path, origin: str = "manual") -> ComponentVersion:
        """Copy a file the user already has into AKENO's library after checking what it is."""
        src = Path(source)
        if not src.is_file() or src.is_symlink():
            raise StackError("The selected file does not exist.", code="no_file")
        component = next((c for c in self.catalog.components.values() if c.role == role), None)
        if component is None or role not in ROLE_FILE:
            raise StackError("This component cannot be imported from a file.", code="no_import")
        name = ROLE_FILE[role]
        want = "x86" if role == "reshade32" else "x64"
        version, strings, machine = dll_version(src)
        if machine is None:
            raise StackError("The selected file is not a Windows DLL.", code="bad_binary")
        if machine != want:
            raise StackError(msg("The selected file is a {arch} DLL; AKENO needs the 64-bit version.", arch=machine)
                             if want == "x64" else
                             msg("The selected file is a {arch} DLL; AKENO needs the 32-bit version.", arch=machine),
                             code="wrong_architecture")
        sha = sha256_file(src)
        known = self.catalog.version_by_hash(component.id, name, sha)
        text = " ".join(strings.values()).lower()
        if role in ("reshade", "reshade32"):
            ok, version, why = reshade_identity(src, want)
            if not ok:
                raise StackError(msg("This is not usable ReShade: {reason}.", reason=why),
                                 "Use the 'with full add-on support' ReShade build.", "reshade_identity")
        elif role == "nr_runtime" and not known and "dlssnr" not in text and src.name.lower() != NR_DLL:
            raise StackError("The file is not nvngx_dlssnr.dll (the DLSS 5 Neural Rendering model).",
                             code="wrong_runtime")
        elif role == "sr_runtime" and not known and ("dlssnr" in text or "dlssg" in text or "dlssd" in text
                                                     or "deep learning supersampling" not in text and "dlss" not in text):
            raise StackError("The file is not nvngx_dlss.dll (DLSS Super Resolution).", code="wrong_runtime")
        elif role == "consumer_renodx":
            if not known and b"RenoDX.DLSS5" not in src.read_bytes():
                raise StackError("This add-on is not the RenoDX DLSS 5 add-on.",
                                 "HDR add-ons and other RenoDX mods are different files.", "wrong_consumer")
        dest_dir = self.root / "local" / sha[:16]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        if not dest.is_file() or sha256_file(dest) != sha:
            shutil.copyfile(src, dest.with_suffix(".tmp"))
            os.replace(dest.with_suffix(".tmp"), dest)
        label = (known.display if known else (version or "unknown version")) + " (from this PC)"
        signed = known.signed if known else "none"
        if role in ("nr_runtime", "sr_runtime") and not known and is_windows():
            from akeno.security import authenticode
            signed = "nvidia" if authenticode.verify(str(dest)).is_nvidia else "modified"
        row = {"component": component.id, "version": (known.version if known else (version or "local")),
               "label": label, "sha256": sha, "file": name, "path": str(dest), "origin": origin,
               "gpus": list(known.gpus) if known else [], "signed": signed, "known": bool(known),
               "notes": [f"Imported from {src}."]}
        rows = [r for r in (self.db.get_setting(LOCAL_KEY, []) or []) if r.get("sha256") != sha]
        rows.append(row)
        self.db.set_setting(LOCAL_KEY, rows)
        return next(v for v in self.local_versions() if v.sha256 == sha)

    def forget_local(self, sha: str) -> None:
        rows = [r for r in (self.db.get_setting(LOCAL_KEY, []) or []) if r.get("sha256") != sha]
        self.db.set_setting(LOCAL_KEY, rows)
        shutil.rmtree(self.root / "local" / sha[:16], ignore_errors=True)

    def discover(self, game_dirs=(), token: CancelToken | None = None) -> list[ComponentVersion]:
        """Find NR/SR runtimes already on this PC (RHI cache, NGX store, driver store, game folders).

        Every NR model found is imported (they are rare and each build matters). Games ship many SR copies,
        so only catalog-known builds and the newest other one are imported."""
        from akeno.backends import runtimes
        found: list[ComponentVersion] = []
        for role, name in (("nr_runtime", NR_DLL), ("sr_runtime", SR_DLL)):
            component = next(c for c in self.catalog.components.values() if c.role == role)
            unknown: list[tuple[tuple, str, str]] = []
            seen: set[str] = set()
            for root in runtimes.search_roots(name, list(game_dirs)):
                for path, compressed in runtimes._walk(root):
                    if token and token.cancelled:
                        raise Cancelled("Search cancelled.")
                    if compressed or not path.lower().endswith(".dll"):
                        continue
                    try:
                        if os.path.getsize(path) > 512 * 1024 * 1024:
                            continue
                        sha = sha256_file(path)
                        if sha in seen:
                            continue
                        seen.add(sha)
                        if role == "sr_runtime" and not self.catalog.version_by_hash(component.id, name, sha):
                            from akeno.backends.runtimes import _version_key
                            unknown.append((_version_key(dll_version(path)[0]), path, root.source))
                            continue
                        found.append(self.import_file(role, path, origin=root.source))
                    except (StackError, OSError) as exc:
                        log.info("skipped %s: %s", path, exc)
            if unknown:
                _, path, origin = max(unknown)
                try:
                    found.append(self.import_file(role, path, origin=origin))
                except (StackError, OSError) as exc:
                    log.info("skipped %s: %s", path, exc)
        return found
