"""Checking, downloading and starting an update of AKENO itself.

* The channel manifest comes from the public release repository: no GitHub account, login or token.
* It must carry AKENO's release signature (see manifest.py); every download must match the SHA-256 it lists.
* The portable package is unpacked into ``%LOCALAPPDATA%\\AKENO\\updates\\staging`` and its own AKENO.exe applies it
  (apply.py), for installed and portable copies alike (the installer installs per user, without administrator
  rights). Licenses, settings, game records and backups stay where they are and keep working with the new version.
* The previous version is kept for a rollback.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

from akeno import __version__, distribution
from akeno.paths import app_home, sub
from akeno.updater import manifest

MAX_MANIFEST = 1 * 2 ** 20


class UpdateError(Exception):
    pass


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    return Path(sys.executable).resolve().parent


def fetch_json(url: str) -> dict:
    from akeno.packages.index import _get
    try:
        return json.loads(_get(url, MAX_MANIFEST).decode("utf-8"))
    except ValueError as exc:
        raise UpdateError(f"the release manifest is not readable: {exc}") from exc


class UpdateService:
    def __init__(self, settings=None, dist: distribution.Distribution | None = None, keys: dict | None = None,
                 fetch=fetch_json, downloads=None, version: str = __version__, folder: Path | None = None):
        self.settings = settings
        self.dist = dist or distribution.load()
        self.keys = manifest.release_keys() if keys is None else keys
        self.fetch = fetch
        self.version = version
        self.folder = Path(folder or sub("updates"))
        self._downloads = downloads

    # -- state -------------------------------------------------------------------------------------------------------
    @property
    def state_path(self) -> Path:
        return self.folder / "state.json"

    def state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _store(self, **values) -> None:
        state = self.state()
        state.update(values)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")

    @property
    def channel(self) -> str:
        value = self.settings.get("update_channel") if self.settings is not None else "stable"
        return value if value in manifest.CHANNELS else "stable"

    def last_outcome(self) -> dict | None:
        try:
            return json.loads((self.folder / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def can_install(self) -> tuple[bool, str]:
        if not frozen():
            return False, "Updates install into the built program only (this copy runs from the source code)."
        if not self.keys:
            return False, "This build has no release verification key: download updates from the release page."
        target = install_dir()
        if not os.access(target, os.W_OK) or not os.access(target.parent, os.W_OK):
            return False, f"AKENO cannot write to {target}: run the installer from the release page."
        return True, ""

    # -- check -------------------------------------------------------------------------------------------------------
    def check(self) -> manifest.Release | None:
        """The newer release of the chosen channel, or None. Raises UpdateError / ManifestError."""
        doc = self.fetch(self.dist.channel_manifest(self.channel))
        release = manifest.parse(doc, self.keys, self.channel)
        key = f"highest_seen_{self.channel}"
        seen = self.state().get(key)
        # a manifest older than one already seen is a replay or a freeze attempt: not offered
        if seen and manifest.newer(seen, release.release):
            raise manifest.ManifestError(f"the channel offers {release.release}, older than {seen} seen before")
        self._store(last_check=time.time(), **{key: release.release if not seen or
                                               manifest.newer(release.release, seen) else seen})
        if not manifest.newer(release.release, self.version):
            return None
        if release.release in (self.state().get("failed") or []):
            return None                                   # this version did not start here: not offered again
        return release

    # -- download and stage ------------------------------------------------------------------------------------------
    def downloads(self):
        if self._downloads is None:
            from akeno.security.downloads import DownloadManager
            self._downloads = DownloadManager(sorted(distribution.HOSTS), cache=self.folder / "downloads")
        return self._downloads

    def download(self, release: manifest.Release, progress=None, token=None) -> Path:
        package = release.file("portable")
        if package is None:
            raise UpdateError("the release has no portable package")
        return Path(self.downloads().fetch(package.url, package.sha256, package.name, package.size, progress,
                                           token).path)

    def stage(self, archive: Path, release: manifest.Release) -> Path:
        """Unpack the verified package (no paths outside the folder, no links) and find its AKENO.exe."""
        staging = self.folder / "staging" / release.release
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                name = PurePosixPath(info.filename.replace("\\", "/"))
                if name.is_absolute() or ".." in name.parts or (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise UpdateError(f"refused path in the package: {info.filename}")
            z.extractall(staging)
        root = staging
        entries = [p for p in staging.iterdir()]
        if len(entries) == 1 and entries[0].is_dir():
            root = entries[0]                              # the package has one top folder
        if not (root / "AKENO.exe").is_file() and not (root / "AKENO").is_file():
            raise UpdateError("the package has no AKENO.exe")
        return root

    def plan(self, release: manifest.Release, staged: Path, target: Path | None = None) -> dict:
        target = Path(target or install_dir())
        keep = []
        home = app_home().resolve()
        if home.is_relative_to(target.resolve()):
            keep.append(str(home.relative_to(target.resolve())).split(os.sep)[0])   # a portable data folder
        return {"pid": os.getpid(), "target": str(target), "source": str(staged),
                "backup": str(self.folder / "previous"), "keep": keep, "version": release.release,
                "previous_version": self.version, "marker": str(self.folder / "started.marker"),
                "result": str(self.folder / "result.json"), "start": ["{target}" + os.sep + "AKENO.exe"],
                "timeout": 90}

    def launch(self, plan: dict) -> None:
        """Start the updater (the staged new AKENO.exe); the caller then quits AKENO."""
        plan_file = self.folder / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=1), encoding="utf-8")
        exe = Path(plan["source"]) / "AKENO.exe"
        flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0
        subprocess.Popen([str(exe), "--apply-update", str(plan_file)], creationflags=flags, close_fds=True,
                         cwd=str(Path(plan["source"])))
        self._store(pending=plan["version"])

    # -- after a start -----------------------------------------------------------------------------------------------
    def after_start(self) -> dict | None:
        """Called once the suite is up: the outcome of an update that just ran, remembered failures."""
        outcome = self.last_outcome()
        state = self.state()
        if not outcome or state.get("reported") == outcome.get("at"):
            return None
        if not outcome.get("ok"):
            self._store(failed=sorted(set(state.get("failed") or []) | {outcome.get("version")}))
        self._store(reported=outcome.get("at"), pending=None)
        staging = self.folder / "staging"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        return outcome

    def previous(self) -> str | None:
        """The version kept for a rollback."""
        backup = self.folder / "previous"
        outcome = self.last_outcome() or {}
        return outcome.get("previous") if backup.is_dir() and outcome.get("ok") else None

    def rollback_plan(self) -> dict:
        backup = self.folder / "previous"
        if not backup.is_dir():
            raise UpdateError("no previous version is kept")
        staged = self.folder / "staging" / "rollback"
        if staged.exists():
            shutil.rmtree(staged)
        shutil.copytree(backup, staged)
        previous = self.previous() or "previous"
        release = manifest.Release(self.channel, previous, "", "", "", "")
        plan = self.plan(release, staged)
        plan["backup"] = str(self.folder / "rolled-back")
        return plan


def write_start_marker() -> None:
    """The new version is up: tell the waiting updater (AKENO_UPDATE_MARKER)."""
    marker = os.environ.get("AKENO_UPDATE_MARKER")
    if marker:
        try:
            Path(marker).write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass
