"""Dependency resolver: what an installation needs, where each piece can lawfully come from, and getting it.

For every dependency of the selected backend the resolver reports one state:

``ok``                    present and verified now (nothing planned counts as done)
``resolvable_local``      a verified copy exists on this PC; "Resolve" imports it into AKENO's store
``resolvable_download``   a reviewed, hash-pinned redistributable; "Resolve" downloads and verifies it
``manual``                needs a step AKENO may not automate (licence/host not reviewed); a guided path is given
``blocked``               cannot be obtained lawfully or technically right now; the exact reason is given
``unknown``               not searched yet

Sources, by dependency (see docs/DEPENDENCIES.md for the licensing review):

* ``package``      — OptiScaler DLSS 5 / DLSS5-Feeder release from the official GitHub release URL pinned in
  ``data/catalog.json`` (open source, redistributable; SHA-256 recorded at review time).
* ``nr_runtime``   — ``nvngx_dlssnr.dll`` from the user's own NVIDIA driver installation only (no lawful download
  exists). Must be NVIDIA-signed and unmodified.
* ``dlss_runtime`` — ``nvngx_dlss.dll``: an NVIDIA-signed copy already on this PC (another DLSS game, NGX store).
* ``reshade``      — ReShade with add-on support (BSD-3-Clause) from reshade.me: guided until a build is reviewed
  and pinned in the catalog.
* ``consumer``     — the DLSS 5 neural add-on DLSS5-Feeder drives: guided (distributed via Discord, not reviewable).
"""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

from akeno.backends import runtimes
from akeno.backends.base import BackendAdapter
from akeno.backends.runtimes import DLSS_RUNTIME, NR_RUNTIME, DiscoveryReport, RuntimeCandidate
from akeno.core.models import Game, TrustState
from akeno.i18n import msg
from akeno.security.catalog import Package
from akeno.security.downloads import CancelToken, Cancelled, DownloadError, VerificationError, sha256_file

log = logging.getLogger(__name__)

OK, LOCAL, DOWNLOAD, MANUAL, BLOCKED, UNKNOWN = ("ok", "resolvable_local", "resolvable_download", "manual",
                                                 "blocked", "unknown")
# How each state is drawn (status badge): never green for something that is only planned.
STATE_BADGE = {OK: "ok", LOCAL: "info", DOWNLOAD: "info", MANUAL: "warn", BLOCKED: "fail", UNKNOWN: "pending"}
STATE_LABEL = {OK: "Ready", LOCAL: "Found on this PC — will be imported", DOWNLOAD: "Will be downloaded and verified",
               MANUAL: "Needs a manual step", BLOCKED: "Blocked", UNKNOWN: "Not searched yet"}

NVIDIA_DRIVERS_URL = "https://www.nvidia.com/en-us/drivers/"
RESHADE_URL = "https://reshade.me/"
FEEDER_CONSUMER_URL = "https://github.com/jlrouzies-fr/DLSS5-Feeder#2-you-need-one-neural-add-on-installed-next-to-this-one"
RUNTIME_KEYS = {"nr_runtime": NR_RUNTIME, "dlss_runtime": DLSS_RUNTIME}

Progress = Callable[[str, int, "int | None"], None]      # (dependency key, done, total)


@dataclass
class GuideLink:
    label: str
    url: str | None = None          # official web page (opened in the browser)
    action: str | None = None       # in-app action: import_nr_runtime | import_dlss_runtime | open_library


@dataclass
class Dependency:
    key: str
    title: str
    required: bool
    state: str
    detail: str
    action: str = ""                # what "Resolve Dependencies" will do (resolvable states only)
    source: str = ""                # provenance or host
    license: str = ""
    size: int | None = None
    note: str = ""                  # extra information (e.g. documented runtime behaviour on this GPU)
    guide: list[GuideLink] = field(default_factory=list)
    candidate: RuntimeCandidate | None = field(default=None, repr=False)
    package: Package | None = field(default=None, repr=False)

    @property
    def satisfied(self) -> bool:
        return self.state == OK

    @property
    def resolvable(self) -> bool:
        return self.state in (LOCAL, DOWNLOAD)

    @property
    def badge(self) -> str:
        return STATE_BADGE[self.state] if self.required or self.state != BLOCKED else "warn"


@dataclass
class DependencyPlan:
    game_id: str | None
    backend_id: str | None
    items: list[Dependency] = field(default_factory=list)
    searched: bool = False
    searched_at: float | None = None

    def get(self, key: str) -> Dependency | None:
        return next((d for d in self.items if d.key == key), None)

    @property
    def blockers(self) -> list[Dependency]:
        """Required items that 'Resolve' cannot fix by itself."""
        return [d for d in self.items if d.required and d.state in (MANUAL, BLOCKED, UNKNOWN)]

    @property
    def resolvable(self) -> list[Dependency]:
        return [d for d in self.items if d.resolvable]

    @property
    def downloads(self) -> list[Dependency]:
        return [d for d in self.items if d.state == DOWNLOAD]

    @property
    def ready(self) -> bool:
        return all(d.satisfied for d in self.items if d.required)

    @property
    def ready_after_resolve(self) -> bool:
        return not self.blockers


@dataclass
class ResolveResult:
    ok: bool
    messages: list[str]
    plan: DependencyPlan
    cancelled: bool = False


class DependencyResolver:
    def __init__(self, services, verifier: runtimes.Verifier | None = None):
        self.s = services
        self.verifier = verifier
        self.reports: dict[str, DiscoveryReport] = {}
        self.searched_at: float | None = None
        self.last_errors: dict[str, str] = {}

    # -- discovery -------------------------------------------------------------------------------
    def game_dirs(self, exclude: Game | None = None) -> list[str]:
        """Folders of library games where the analyzer saw an NVIDIA runtime (no new disk walk)."""
        out: list[str] = []
        for g in self.s.library.games():
            if exclude is not None and g.id == exclude.id:
                continue
            for m in g.existing_mods:
                if m.kind in ("dlss", "nvidia_nr_runtime") and m.path:
                    d = os.path.dirname(m.path)
                    if d not in out:
                        out.append(d)
        return out

    def discover(self, names: tuple[str, ...] = runtimes.SUPPORTED_RUNTIMES) -> dict[str, DiscoveryReport]:
        dirs = self.game_dirs()
        for name in names:
            self.reports[name] = runtimes.discover_report(name, dirs, verifier=self.verifier)
        self.searched_at = time.time()
        return self.reports

    # -- plan --------------------------------------------------------------------------------------
    def plan(self, game: Game | None, adapter: BackendAdapter | None) -> DependencyPlan:
        plan = DependencyPlan(game.id if game else None, adapter.id if adapter else None,
                              searched=bool(self.reports), searched_at=self.searched_at)
        if game is None or adapter is None:
            return plan
        for key, required in adapter.dependency_specs(game):
            builder = getattr(self, f"_dep_{key}", None)
            if builder is None:
                continue
            dep = builder(game, adapter, bool(required))
            if key in self.last_errors and dep.state in (DOWNLOAD, LOCAL):
                dep.detail += " " + msg("Last attempt failed: {e}", e=self.last_errors[key])
            plan.items.append(dep)
        return plan

    def _dep_package(self, game: Game, adapter: BackendAdapter, required: bool) -> Dependency:
        pkg = self.s.package_for(adapter)
        title = msg("{b} package", b=adapter.short)
        if pkg is None:
            return Dependency("package", title, required, BLOCKED, msg("No package is listed for this backend."))
        host = urllib.parse.urlsplit(pkg.url or "").hostname or ""
        base = Dependency("package", msg("{n} {v}", n=pkg.name, v=pkg.version), required, BLOCKED, "",
                          source=pkg.upstream, license=pkg.license, size=pkg.size, package=pkg)
        if not pkg.installable:
            base.detail = msg("Not a reviewed, hash-pinned release ({s}). Automatic installation stays off until a "
                              "catalog update pins it.", s=pkg.trust.value.replace("_", " "))
            base.guide = [GuideLink(msg("Open the project page"), pkg.upstream)]
            return base
        cached = self.s.downloads.cached_path(pkg.sha256, os.path.basename(pkg.url))
        if cached.is_file() and sha256_file(cached) == pkg.sha256:
            base.state = OK
            base.detail = msg("Downloaded and verified: SHA-256 matches the release reviewed on {d}.",
                              d=pkg.reviewed or "?")
            return base
        base.state = DOWNLOAD
        base.detail = msg("{mb} MB from {h} (official release). Verified against the SHA-256 pinned at review "
                          "({d}) before use.", mb=f"{(pkg.size or 0) / 1e6:.1f}", h=host, d=pkg.reviewed or "?")
        base.action = msg("Download {n} {v} and verify it", n=pkg.name, v=pkg.version)
        return base

    def _runtime_dep(self, key: str, game: Game, required: bool) -> Dependency:
        name = RUNTIME_KEYS[key]
        title = msg("NVIDIA runtime {f}", f=name)
        in_game = next((m for m in game.existing_mods if m.name.lower() == name), None)
        if in_game is not None:
            return Dependency(key, title, required, OK, msg("The game already has {f}; it is used as it is.", f=name),
                              source=in_game.path or "")
        rec = self.s.runtime_lookup(name)
        if rec:
            d = Dependency(key, title, required, OK,
                           msg("Imported: version {v}, signed by {s}.", v=rec.get("version") or "?",
                               s=rec.get("signer") or "?"), source=rec.get("original_path") or "")
            d.note = self._runtime_note(key, rec.get("version"))
            return d
        rep = self.reports.get(name)
        if rep is None:
            return Dependency(key, title, required, UNKNOWN,
                              msg("Not searched yet. 'Resolve Dependencies' searches your NVIDIA driver and games."))
        best = rep.best()
        if best is not None:
            d = Dependency(key, title, required, LOCAL,
                           msg("NVIDIA-signed copy {v} found: {src}.", v=best.version or "?", src=best.source_label),
                           action=msg("Import the verified copy of {f} into AKENO", f=name),
                           source=best.extracted_from or best.path, candidate=best)
            d.note = self._runtime_note(key, best.version)
            return d
        d = Dependency(key, title, required, BLOCKED, "", guide=self._runtime_guide(key))
        unverified = [c for c in rep.candidates if c.trust == TrustState.UNVERIFIED]
        if unverified:
            d.detail = msg("{n} copy/copies found, but the NVIDIA signature can only be checked on Windows.",
                           n=len(unverified))
        elif rep.refused:
            first = rep.refused[0]
            d.detail = msg("{n} copy/copies found, none is an unmodified NVIDIA file (e.g. {p}: {r}). Modified NVIDIA "
                           "files are never installed.", n=len(rep.refused), p=first.path, r=first.detail)
        elif key == "nr_runtime":
            d.detail = msg("Not found on this PC. NVIDIA offers no separate download of {f}; it is installed with "
                           "NVIDIA's display driver. Update the driver, restart, then resolve again.", f=name)
        else:
            d.detail = msg("Not found on this PC. {f} comes with games that use DLSS and with NVIDIA's DLSS updates; "
                           "AKENO does not download it (NVIDIA's SDK licence forbids stand-alone distribution).", f=name)
        return d

    def _runtime_guide(self, key: str) -> list[GuideLink]:
        if key == "nr_runtime":
            return [GuideLink(msg("Get the latest NVIDIA driver"), NVIDIA_DRIVERS_URL),
                    GuideLink(msg("Import file…"), action="import_nr_runtime")]
        return [GuideLink(msg("Scan the game library again"), action="open_library"),
                GuideLink(msg("Import file…"), action="import_dlss_runtime")]

    def _runtime_note(self, key: str, version: str | None) -> str:
        if key != "nr_runtime":
            return ""
        from akeno.hardware.support import runtime_verdict
        gpu = self.s.selected_gpu()
        v = runtime_verdict(version, gpu.generation if gpu else None, self.s.compat_db.nr_runtimes())
        return v.text if v.state in ("works", "fails") else ""

    def _dep_nr_runtime(self, game: Game, adapter: BackendAdapter, required: bool) -> Dependency:
        return self._runtime_dep("nr_runtime", game, required)

    def _dep_dlss_runtime(self, game: Game, adapter: BackendAdapter, required: bool) -> Dependency:
        d = self._runtime_dep("dlss_runtime", game, required)
        if not required and d.state != OK:
            d.note = msg("Optional: the game has its own DLSS. Needed only without one.")
        return d

    def _dep_reshade(self, game: Game, adapter: BackendAdapter, required: bool) -> Dependency:
        title = msg("ReShade with add-on support")
        if any(m.kind == "reshade" for m in game.existing_mods):
            return Dependency("reshade", title, required, OK, msg("ReShade was found next to the game executable."),
                              license="BSD-3-Clause")
        return Dependency("reshade", title, required, MANUAL,
                          msg("Install ReShade \"with full add-on support\" for this game from reshade.me. AKENO "
                              "downloads only reviewed, hash-pinned builds and has none for ReShade yet."),
                          license="BSD-3-Clause", guide=[GuideLink(msg("Open reshade.me"), RESHADE_URL)])

    def _dep_consumer(self, game: Game, adapter: BackendAdapter, required: bool) -> Dependency:
        title = msg("DLSS 5 neural add-on (exactly one)")
        kinds = [m.kind for m in game.existing_mods]
        n = kinds.count("neural_consumer") + (1 if "optiscaler_nr_forwarder" in kinds else 0)
        if n == 1:
            return Dependency("consumer", title, required, OK, msg("One neural add-on is installed."))
        if n > 1:
            return Dependency("consumer", title, required, BLOCKED,
                              msg("More than one neural add-on is installed; they disable each other. Keep exactly one."))
        return Dependency("consumer", title, required, MANUAL,
                          msg("DLSS5-Feeder needs one neural add-on (Deep Fried Chicken or renodx-dlss5). They are "
                              "distributed through Discord, so AKENO cannot review or download them."),
                          guide=[GuideLink(msg("Read the DLSS5-Feeder instructions"), FEEDER_CONSUMER_URL)])

    # -- resolve -----------------------------------------------------------------------------------
    def resolve(self, game: Game, adapter: BackendAdapter, progress: Progress | None = None,
                token: CancelToken | None = None, rediscover: bool = True) -> ResolveResult:
        """Do every permitted automatic step: search, import verified local runtimes, download verified
        packages. Never touches the game folder. Returns the refreshed plan."""
        messages: list[str] = []
        if rediscover or not self.reports:
            if progress:
                progress("search", 0, None)
            self.discover()
            messages.append(msg("Searched your NVIDIA driver, NGX store and games for NVIDIA runtimes."))
        plan = self.plan(game, adapter)
        ok = True
        for dep in plan.items:
            if token and token.cancelled:
                return ResolveResult(False, messages + [msg("Cancelled. Nothing was changed in the game folder.")],
                                     self.plan(game, adapter), cancelled=True)
            if dep.state == LOCAL and dep.candidate is not None:
                try:
                    # Re-evaluate right before import: the file may have changed since the search.
                    fresh = runtimes.evaluate(dep.candidate.path, dep.candidate.file_name, dep.candidate.source,
                                              self.verifier)
                    if fresh.trust != TrustState.VERIFIED or fresh.sha256 != dep.candidate.sha256:
                        raise OSError(msg("{f} changed since it was found and is no longer a verified copy.",
                                          f=dep.candidate.file_name))
                    fresh.extracted_from = dep.candidate.extracted_from
                    runtimes.import_candidate(self.s.db, fresh)
                    self.last_errors.pop(dep.key, None)
                    messages.append(msg("Imported {f} {v} (NVIDIA signature valid) from {src}.",
                                        f=fresh.file_name, v=fresh.version or "", src=fresh.source_label))
                except OSError as exc:
                    ok = False
                    self.last_errors[dep.key] = str(exc)
                    messages.append(msg("Could not import {f}: {e}", f=dep.candidate.file_name, e=str(exc)))
            elif dep.state == DOWNLOAD and dep.package is not None:
                pkg = dep.package
                try:
                    self.s.downloads.fetch(pkg.url, pkg.sha256, os.path.basename(pkg.url), pkg.size,
                                           (lambda n, total, k=dep.key: progress(k, n, total)) if progress else None,
                                           token)
                    self.last_errors.pop(dep.key, None)
                    messages.append(msg("Downloaded {n} {v}; SHA-256 matches the reviewed release.", n=pkg.name,
                                        v=pkg.version))
                except Cancelled:
                    return ResolveResult(False, messages + [msg("Download cancelled. Nothing was changed.")],
                                         self.plan(game, adapter), cancelled=True)
                except VerificationError as exc:
                    ok = False
                    self.last_errors[dep.key] = msg("the download did not match the reviewed release and was discarded")
                    messages.append(msg("{n}: the downloaded file is corrupt or not the reviewed release; it was "
                                        "discarded ({e}).", n=pkg.name, e=str(exc)))
                except DownloadError as exc:
                    ok = False
                    self.last_errors[dep.key] = str(exc)
                    messages.append(msg("{n}: download failed ({e}). Check your internet connection and try again.",
                                        n=pkg.name, e=str(exc)))
        after = self.plan(game, adapter)
        for dep in after.blockers:
            messages.append(msg("Still needed: {t} — {d}", t=dep.title, d=dep.detail))
        return ResolveResult(ok and after.ready, messages, after)
