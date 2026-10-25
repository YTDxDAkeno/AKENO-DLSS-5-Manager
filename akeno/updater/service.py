"""Checking, downloading and starting an update of AKENO itself.

* The release information comes from the build's distribution: the AKENO Release Manager on the production API
  (``GET /api/v1/releases/stable``, schema 2, see ``api_v2.py``), or the older static or GitHub channel files. No
  account, login or token. Only HTTPS hosts of that distribution are contacted, redirects included.
* It must carry AKENO's release signature (Ed25519, keys built into the program); only signed values are used.
* Replays and freezes are refused: the signed ``sequence`` never goes back (the same sequence must describe the
  same release), the version never goes back, and expired information is not offered. The clock used for the
  expiry check never goes back behind the newest signed issue time already seen.
* The portable package must match its signed SHA-256 and size. It is unpacked within the signed limits
  (``staging.py``) into ``%LOCALAPPDATA%\\AKENO\\updates\\staging``, and its own AKENO.exe applies it (``apply.py``)
  with a journal, a health check and an automatic rollback. Licenses, settings, game records and backups stay
  where they are and keep working with the new version.
* When the running AKENO.exe carries a valid Authenticode signature, the new one must carry a valid signature of the
  same signer. An unsigned build relies on the release signature and the checksums (Authenticode is optional).
* Nothing is installed without the user's "Install now" and the confirmation that follows. Automatic checks run at
  most every ``CHECK_HOURS``; "Remind me later" keeps a version from being announced again.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from akeno import __version__, distribution
from akeno.paths import app_home, sub
from akeno.updater import manifest, staging

MAX_MANIFEST = 1 * 2 ** 20
MAX_PACKAGE = 600 * 2 ** 20          # above the Release Manager's 512 MiB limit for a portable package
CHECK_HOURS = 6                      # automatic checks: at most this often (a manual check always runs)
SPACE_MARGIN = 128 * 2 ** 20


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
    """Release information over verified HTTPS from a trusted host (redirects only to trusted hosts)."""
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
    req = urllib.request.Request(url, headers={"User-Agent": f"AKENO/{__version__}", "Accept": "application/json",
                                               "Cache-Control": "no-cache"})
    try:
        with opener.open(req, timeout=20) as r:
            if not trusted(r.geturl()):
                raise UpdateError("refused an answer from outside the official release addresses")
            data = r.read(MAX_MANIFEST + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NoRelease("no release has been published on this channel yet") from exc
        raise ServiceUnavailable(f"the update service answered {exc.code}") from exc
    except UpdateError:
        raise
    except (urllib.error.URLError, OSError) as exc:
        raise ServiceUnavailable(f"the update service cannot be reached ({getattr(exc, 'reason', exc)})") from exc
    if len(data) > MAX_MANIFEST:
        raise UpdateError("the release information is larger than allowed")
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise UpdateError(f"the release information is not readable: {exc}") from exc


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


def same_drive(a: Path, b: Path) -> bool:
    def existing(p: Path) -> Path:
        p = Path(p).absolute()
        while not p.exists() and p.parent != p:
            p = p.parent
        return p
    try:
        return os.stat(existing(a)).st_dev == os.stat(existing(b)).st_dev
    except OSError:
        return True


class UpdateService:
    def __init__(self, settings=None, dist: distribution.Distribution | None = None, keys: dict | None = None,
                 fetch=fetch_json, downloads=None, version: str = __version__, folder: Path | None = None,
                 clock=time.time):
        self.settings = settings
        self.dist = dist or distribution.load()
        self.keys = manifest.release_keys() if keys is None else keys
        self.fetch = fetch if fetch is not fetch_json else (lambda url: fetch_json(url, self.dist.trusted))
        self.version = version
        self.folder = Path(folder or sub("updates"))
        self._downloads = downloads
        self.clock = clock

    # -- state -------------------------------------------------------------------------------------------------------
    @property
    def state_path(self) -> Path:
        return self.folder / "state.json"

    def state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _store(self, **values) -> None:
        state = self.state()
        state.update(values)
        staging.write_atomic(self.state_path, json.dumps(state, indent=1).encode("utf-8"))

    @property
    def channel(self) -> str:
        value = self.settings.get("update_channel") if self.settings is not None else "stable"
        return value if value in manifest.CHANNELS else "stable"

    def setting(self, key: str, default=True):
        if self.settings is None:
            return default
        value = self.settings.get(key)
        return default if value is None else value

    def last_outcome(self) -> dict | None:
        try:
            return json.loads((self.folder / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def journal(self) -> dict:
        from akeno.updater import apply
        return apply.Journal(self.folder / "journal.json").load()

    def now(self) -> float:
        """The time for expiry checks: never earlier than the newest signed issue time seen (a clock set back
        cannot make expired release information valid again)."""
        return max(self.clock(), float(self.state().get("newest_issued") or 0))

    def can_install(self, release: manifest.Release | None = None) -> tuple[bool, str]:
        if release is not None:
            details = getattr(release, "v2", None)
            if details is not None:
                why = details.in_app_refusal(self.version)
                if why:
                    return False, why
            elif release.file("portable") is None:
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
        """"Remind me later": this version is not announced again (the Updates page still shows it)."""
        self._store(snoozed=version)

    def snoozed(self, version: str) -> bool:
        return self.state().get("snoozed") == version

    def _anti_replay(self, release: manifest.Release) -> None:
        """Schema 2: the signed sequence never goes back; one sequence is one release. Remembered here."""
        details = release.v2
        package = release.file("portable") or release.file("installer")
        fingerprint = [release.release, details.request_id, package.sha256 if package else ""]
        seen = self.state().get("sequence_seen") or {}
        last = int(seen.get("sequence") or 0)
        if details.sequence < last:
            raise manifest.ManifestError(f"the release information is older than information seen before "
                                         f"(sequence {details.sequence} < {last}): refused as a replay")
        if details.sequence == last and seen.get("fingerprint") != fingerprint:
            raise manifest.ManifestError(f"two different releases carry the same sequence {last}: refused")
        self._store(sequence_seen={"sequence": details.sequence, "fingerprint": fingerprint},
                    newest_issued=max(details.issued_ts, float(self.state().get("newest_issued") or 0)))

    def check(self) -> manifest.Release | None:
        """The newer release of the chosen channel, or None. Raises UpdateError / ManifestError."""
        self._store(last_attempt=time.time())
        try:
            if self.dist.source == "akeno-api":
                if self.channel != "stable":
                    raise UpdateError("the AKENO release server offers only the stable channel")
                release = manifest.parse_api(self.fetch(self.dist.channel_manifest("stable")), self.keys, self.dist,
                                             self.now())
            else:
                doc = self.fetch(self.dist.channel_manifest(self.channel))
                release = manifest.parse(doc, self.keys, self.channel, self.dist, self.now())
        except NoRelease:
            self._store(last_check=time.time(), last_success=time.time(), latest=None)
            return None                                   # nothing published yet: up to date
        key = f"highest_seen_{self.channel}"
        seen = self.state().get(key)
        # a version older than one already seen is a replay or a freeze attempt: not offered
        if seen and manifest.newer(seen, release.release):
            raise manifest.ManifestError(f"the channel offers {release.release}, older than {seen} seen before")
        if getattr(release, "v2", None) is not None:
            self._anti_replay(release)
        self._store(last_check=time.time(), last_success=time.time(), latest=release.release,
                    **{key: release.release if not seen or manifest.newer(release.release, seen) else seen})
        if not manifest.newer(release.release, self.version):
            return None
        if release.release in (self.state().get("failed") or []):
            return None                                   # this version did not start here: not offered again
        return release

    # -- download and stage ------------------------------------------------------------------------------------------
    def downloads(self):
        if self._downloads is None:
            from akeno.security.downloads import DownloadManager
            self._downloads = DownloadManager(sorted(self.dist.hosts), cache=self.folder / "downloads",
                                              hard_limit=MAX_PACKAGE)
        return self._downloads

    def space_needed(self, release: manifest.Release, target: Path | None = None) -> list[tuple[Path, int]]:
        """Free space each drive needs: the download and the unpacked copy (updates folder), one more copy next to
        the program folder (both on one drive: the sum)."""
        package = release.file("portable")
        details = getattr(release, "v2", None)
        unpacked = details.portable.uncompressed_size if details is not None and details.portable else \
            (package.size * 3 if package else 0)
        target = Path(target or install_dir())
        here = (package.size if package else 0) + unpacked + SPACE_MARGIN
        there = unpacked + SPACE_MARGIN
        if same_drive(self.folder, target.parent):
            return [(self.folder, here + there)]
        return [(self.folder, here), (target.parent, there)]

    def check_space(self, release: manifest.Release, target: Path | None = None) -> None:
        from akeno.updater.apply import free_bytes
        for where, need in self.space_needed(release, target):
            free = free_bytes(where)
            if free < need:
                raise UpdateError(f"not enough free space on the drive of {where}: {need // 2 ** 20} MB needed, "
                                  f"{free // 2 ** 20} MB free")

    def download(self, release: manifest.Release, progress=None, token=None) -> Path:
        """The portable package, checked against its signed SHA-256 and size (cached: a second call is instant)."""
        package = release.file("portable")
        if package is None:
            raise UpdateError("the release has no portable package")
        result = self.downloads().fetch(package.url, package.sha256, package.name, package.size, progress, token)
        if result.size != package.size:
            Path(result.path).unlink(missing_ok=True)
            raise UpdateError("the downloaded package does not have its signed size; it was discarded")
        self._store(downloaded={"version": release.release, "sha256": package.sha256})
        return Path(result.path)

    def downloaded(self, release: manifest.Release) -> bool:
        """The verified package of ``release`` is already in the download cache."""
        package = release.file("portable")
        if package is None:
            return False
        cached = self.downloads().cached_path(package.sha256, package.name)
        return cached.is_file() and (self.state().get("downloaded") or {}).get("sha256") == package.sha256

    def stage(self, archive: Path, release: manifest.Release, progress=None) -> Path:
        """Unpack the verified package within its signed limits and find its AKENO.exe (see ``staging.py``)."""
        details = getattr(release, "v2", None)
        portable = details.portable if details is not None else None
        root_dir = self.folder / "staging"
        root_dir.mkdir(parents=True, exist_ok=True)
        staging.no_links_on_the_way(root_dir, app_home())
        dest = root_dir / release.release
        if dest.exists():
            shutil.rmtree(dest)
        try:
            content = staging.extract(archive, dest, portable.file_count if portable else staging.HARD_FILES,
                                      portable.uncompressed_size if portable else staging.HARD_BYTES, progress)
            root = staging.program_root(dest)
            content = staging.relative_to_root(content, root.relative_to(dest).as_posix() if root != dest else "")
        except staging.StagingError as exc:
            shutil.rmtree(dest, ignore_errors=True)
            raise UpdateError(str(exc)) from exc
        if not (root / "AKENO.exe").is_file():
            shutil.rmtree(dest, ignore_errors=True)
            raise UpdateError("the package has no AKENO.exe")
        content["pe_files"] = staging.pe_count(root, content)
        if portable is not None:
            content["release"] = {"pe_files": portable.pe_files, "file_count": portable.file_count,
                                  "uncompressed_size": portable.uncompressed_size,
                                  "content_sha256": portable.content_sha256}
        staging.save(content, self.content_path(release))
        if frozen() and sys.platform == "win32":
            self._store(signature=signature_check(root / "AKENO.exe"))
        return root

    def content_path(self, release: manifest.Release) -> Path:
        return self.folder / "staging" / f"{release.release}.content.json"

    def prepare(self, release: manifest.Release, emit=None, token=None) -> dict:
        """Download, verify and unpack ``release``; returns the plan for :meth:`launch`. ``emit`` receives the
        phases: ``("space",)``, ``("download", done, total)``, ``("verified",)``, ``("unpack", done, total)``,
        ``("signature", note)``."""
        from akeno.updater import apply
        emit = emit or (lambda *a: None)
        with apply.lock(self.folder / "prepare.lock"):
            if self.journal().get("phase") in apply.UNFINISHED:
                raise UpdateError("an earlier update is not finished yet: restart AKENO first")
            emit("space")
            self.check_space(release)
            archive = self.download(release, lambda done, total: emit("download", done, total), token)
            emit("verified")              # the download manager accepted it only with the signed SHA-256
            emit("unpack", 0, 0)
            staged = self.stage(archive, release, lambda done, total: emit("unpack", done, total))
            emit("signature", self.state().get("signature") or "")
            return self.plan(release, staged)

    def plan(self, release: manifest.Release, staged: Path, target: Path | None = None) -> dict:
        target = Path(target or install_dir())
        keep = []
        home = app_home().resolve()
        if home.is_relative_to(target.resolve()):
            keep.append(str(home.relative_to(target.resolve())).split(os.sep)[0])   # a portable data folder
        journal = self.folder / "journal.json"
        content = self.content_path(release)
        exe = Path(staged) / "AKENO.exe"
        return {"pid": os.getpid(), "target": str(target), "source": str(staged),
                "backup": str(self.folder / "previous"), "keep": keep, "version": release.release,
                "previous_version": self.version, "marker": str(self.folder / "started.marker"),
                "result": str(self.folder / "result.json"), "journal": str(journal),
                "lock": str(self.folder / "apply.lock"),
                "content": str(content) if content.is_file() else "",
                "start": ["{target}" + os.sep + "AKENO.exe"], "timeout": 120,
                "recovery_command": subprocess.list2cmdline([str(exe), "--recover-update", str(journal),
                                                             "--no-start"])}

    def launch(self, plan: dict) -> None:
        """Start the updater (the staged new AKENO.exe); the caller then quits AKENO."""
        plan_file = self.folder / "plan.json"
        staging.write_atomic(plan_file, json.dumps(plan, indent=1).encode("utf-8"))
        exe = Path(plan["source"]) / "AKENO.exe"
        flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0
        subprocess.Popen([str(exe), "--apply-update", str(plan_file)], creationflags=flags, close_fds=True,
                         cwd=str(Path(plan["source"])))
        self._store(pending=plan["version"])

    # -- after a start -----------------------------------------------------------------------------------------------
    def after_start(self) -> dict | None:
        """Called once the suite is up: the outcome of an update that just ran, remembered failures, clean-up."""
        from akeno.updater import apply
        outcome = self.last_outcome()
        state = self.state()
        if self.journal().get("phase") in apply.UNFINISHED:
            return None                   # the staged updater is still needed to finish (or recover) it
        if not outcome or state.get("reported") == outcome.get("at"):
            return None
        if not outcome.get("ok"):
            self._store(failed=sorted(set(state.get("failed") or []) | {outcome.get("version")}))
        self._store(reported=outcome.get("at"), pending=None)
        staging_dir = self.folder / "staging"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
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
        plan["content"] = ""
        return plan


HEALTH_CHECKS = ("version", "program_data", "release_keys", "license_keys", "settings_database", "license_state",
                 "window")


def health_report(expected: str | None = None) -> dict:
    """This version's own start check, for the updater's health check. Reads only; changes nothing."""
    import sqlite3

    from akeno.paths import db_path, resource_dir
    checks: dict[str, bool | str] = {}

    def check(name, fn):
        try:
            result = fn()
            checks[name] = True if result is None or result is True else (result or False)
        except Exception as exc:                      # every failure is a result, never a crash
            checks[name] = f"{type(exc).__name__}: {exc}"[:200]

    def program_data():
        for name in ("i18n_de.json", "distribution.json", "licensing.json", "release-keys.json"):
            if not (resource_dir() / name).is_file():
                return f"{name} missing"
        return True

    def license_keys():
        from akeno.licensing import config
        cfg = config.load()
        return bool(cfg.keys) or "no license verification key"

    def settings_database():
        path = db_path()
        if not path.exists():
            return True
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
        finally:
            con.close()
        return row is not None and row[0] == "ok" or f"quick_check: {row}"

    def license_state():
        from akeno.licensing.storage import SecureStore
        store = SecureStore()
        if not store.path.exists():
            return True
        store._decode(store.path.read_bytes())        # decrypt only: never moves or rewrites the state
        return True

    check("version", lambda: expected in (None, "", __version__) or f"this is {__version__}")
    check("program_data", program_data)
    check("release_keys", lambda: bool(manifest.release_keys()) or "no release verification key")
    check("license_keys", license_keys)
    check("settings_database", settings_database)
    check("license_state", license_state)
    checks["window"] = True                           # called from the running event loop, after the window
    return {"pid": os.getpid(), "version": __version__, "ok": all(v is True for v in checks.values()),
            "checks": checks, "at": time.time()}


def write_health_report() -> None:
    """The new version is up: tell the waiting updater (``AKENO_UPDATE_MARKER``) how its start went."""
    marker = os.environ.pop("AKENO_UPDATE_MARKER", None)
    expected = os.environ.pop("AKENO_UPDATE_EXPECT", None)   # not passed on to the desktop runtime
    if not marker:
        return
    try:
        staging.write_atomic(Path(marker), json.dumps(health_report(expected)).encode("utf-8"))
    except OSError:
        pass


write_start_marker = write_health_report
