"""Checking, downloading and starting an update of AKENO itself.

* The channel manifest comes from the build's distribution (the public release repository on GitHub, or AKENO's
  own release server on akeno.online): no account, login or token. Only HTTPS hosts of that distribution are
  contacted, redirects included.
* It must carry AKENO's release signature (see manifest.py); every download must match the SHA-256 it lists.
* The portable package is unpacked into ``%LOCALAPPDATA%\\AKENO\\updates\\staging`` and its own AKENO.exe applies it
  (apply.py), for installed and portable copies alike (the installer installs per user, without administrator
  rights). Licenses, settings, game records and backups stay where they are and keep working with the new version.
* The previous version is kept for a rollback.
* When the running AKENO.exe carries a valid Authenticode signature, the new one must carry a valid signature of the
  same signer (an unsigned build relies on the release signature and the checksums alone).
* Automatic checks run at most every ``CHECK_HOURS``; "Later" keeps a version from being announced again.
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
CHECK_HOURS = 6                      # automatic checks: at most this often (a manual check always runs)


class UpdateError(Exception):
    pass


class ServiceUnavailable(UpdateError):
    """The update service could not be reached (offline, timeout, server error): nothing is wrong with AKENO."""


class NoRelease(UpdateError):
    """The channel has no published release yet (HTTP 404): there is nothing newer to offer."""


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    return Path(sys.executable).resolve().parent


def fetch_json(url: str, trusted=None) -> dict:
    """A channel manifest over verified HTTPS from a trusted host (redirects only to trusted hosts)."""
    import ssl
    import urllib.error
    import urllib.request
    trusted = trusted or distribution.load().trusted
    if not trusted(url):
        raise UpdateError(f"refusing {url}: not an official AKENO release address")

    class Redirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg_, headers, newurl):
            if not trusted(newurl):
                raise UpdateError("refused a redirect away from the official release addresses")
            return super().redirect_request(req, fp, code, msg_, headers, newurl)
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()),
                                         Redirects)
    req = urllib.request.Request(url, headers={"User-Agent": f"AKENO/{__version__}", "Accept": "application/json"})
    try:
        with opener.open(req, timeout=20) as r:
            data = r.read(MAX_MANIFEST + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NoRelease("no release has been published on this channel yet") from exc
        raise ServiceUnavailable(f"the update service answered {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ServiceUnavailable(f"the update service cannot be reached ({getattr(exc, 'reason', exc)})") from exc
    if len(data) > MAX_MANIFEST:
        raise UpdateError("the release manifest is larger than allowed")
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise UpdateError(f"the release manifest is not readable: {exc}") from exc


def signature_check(new_exe: Path, running_exe: Path | None = None) -> str:
    """The Authenticode rule for the new AKENO.exe. Returns a note for the log, raises UpdateError on a mismatch."""
    from akeno.security import authenticode
    running = authenticode.verify(str(running_exe or sys.executable))
    if not running.checked:
        return "Authenticode not checked (not Windows)"
    if not running.valid:
        return "this build is not code-signed: release signature and SHA-256 only"
    new = authenticode.verify(str(new_exe))
    if not new.valid:
        raise UpdateError("the new AKENO.exe has no valid code signature, although this version has one")
    if " ".join((new.signer or "").casefold().split()) != " ".join((running.signer or "").casefold().split()):
        raise UpdateError(f"the new AKENO.exe is signed by {new.signer!r}, not by {running.signer!r}")
    return f"code signature valid ({new.signer})"


class UpdateService:
    def __init__(self, settings=None, dist: distribution.Distribution | None = None, keys: dict | None = None,
                 fetch=fetch_json, downloads=None, version: str = __version__, folder: Path | None = None):
        self.settings = settings
        self.dist = dist or distribution.load()
        self.keys = manifest.release_keys() if keys is None else keys
        self.fetch = fetch if fetch is not fetch_json else (lambda url: fetch_json(url, self.dist.trusted))
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

    def can_install(self, release: manifest.Release | None = None) -> tuple[bool, str]:
        if release is not None and release.file("portable") is None:
            return False, ("This release is offered as an installer on the release page: download it there "
                           "(AKENO installs only portable packages itself, so it can roll back).")
        if not frozen():
            return False, "Updates install into the built program only (this copy runs from the source code)."
        if not self.keys:
            return False, "This build has no release verification key: download updates from the release page."
        target = install_dir()
        if not os.access(target, os.W_OK) or not os.access(target.parent, os.W_OK):
            return False, f"AKENO cannot write to {target}: run the installer from the release page."
        return True, ""

    # -- check -------------------------------------------------------------------------------------------------------
    def due(self, hours: float = CHECK_HOURS, now: float | None = None) -> bool:
        """An automatic check is due: the last one (successful or not) is at least ``hours`` old."""
        last = max(self.state().get("last_check") or 0, self.state().get("last_attempt") or 0)
        return (time.time() if now is None else now) - last >= hours * 3600

    def snooze(self, version: str) -> None:
        """"Later": this version is not announced again (the Updates page still shows it)."""
        self._store(snoozed=version)

    def snoozed(self, version: str) -> bool:
        return self.state().get("snoozed") == version

    def check(self) -> manifest.Release | None:
        """The newer release of the chosen channel, or None. Raises UpdateError / ManifestError."""
        self._store(last_attempt=time.time())
        try:
            if self.dist.source == "akeno-api":
                if self.channel != "stable":
                    raise UpdateError("the AKENO release server offers only the stable channel")
                release = manifest.parse_api(self.fetch(self.dist.channel_manifest("stable")), self.keys, self.dist)
            else:
                doc = self.fetch(self.dist.channel_manifest(self.channel))
                release = manifest.parse(doc, self.keys, self.channel, self.dist)
        except NoRelease:
            self._store(last_check=time.time())
            return None                                   # nothing published yet: up to date
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
            self._downloads = DownloadManager(sorted(self.dist.hosts), cache=self.folder / "downloads")
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
        if frozen() and sys.platform == "win32":
            self._store(signature=signature_check(root / "AKENO.exe"))
        return root

    def prepare(self, release: manifest.Release, emit=None, token=None) -> dict:
        """Download, verify and unpack ``release``; returns the plan for :meth:`launch`. ``emit`` receives the
        phases: ``("download", done, total)``, ``("verified",)``, ``("unpack",)``, ``("signature", note)``."""
        emit = emit or (lambda *a: None)
        archive = self.download(release, lambda done, total: emit("download", done, total), token)
        emit("verified")                    # the download manager accepted it only with the manifest's SHA-256
        emit("unpack")
        staged = self.stage(archive, release)
        emit("signature", self.state().get("signature") or "")
        return self.plan(release, staged)

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
