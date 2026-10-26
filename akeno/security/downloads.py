"""Secure download manager.

* HTTPS only, every request and every redirect hop must target an allow-listed host.
* Size limits (declared size from the catalog, plus a hard ceiling).
* Downloads stream to a ``.part`` file, are hashed while streaming, and are only moved into the
  content-addressed cache when the SHA-256 matches the pinned hash from the reviewed catalog.
* Cancellation and bounded retries with backoff.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from akeno import __version__
from akeno.core.db import iter_chunks
from akeno.i18n import msg
from akeno.paths import cache_dir

log = logging.getLogger(__name__)

HARD_LIMIT = 256 * 1024 * 1024


class DownloadError(Exception):
    pass


class VerificationError(DownloadError):
    pass


class Cancelled(DownloadError):
    pass


class ThreatDetected(DownloadError):
    """The antivirus recognised a verified download as harmful; it is not installed (the file is left for the
    antivirus to handle - AKENO deletes nothing)."""

    def __init__(self, message: str, threats: list[str] | None = None):
        super().__init__(message)
        self.threats = threats or []


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    for chunk in iter_chunks(path):
        h.update(chunk)
    return h.hexdigest()


@dataclass
class DownloadResult:
    path: Path
    sha256: str
    size: int
    from_cache: bool


class _AllowlistRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, check: Callable[[str], None]):
        self.check = check

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class CancelToken:
    def __init__(self):
        self._ev = threading.Event()

    def cancel(self) -> None:
        self._ev.set()

    @property
    def cancelled(self) -> bool:
        return self._ev.is_set()


class DownloadManager:
    def __init__(self, allowed_hosts: list[str], cache: Path | None = None, opener=None,
                 retries: int = 3, timeout: float = 30.0, hard_limit: int = HARD_LIMIT):
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.hard_limit = hard_limit
        self.cache = Path(cache or cache_dir())
        self.cache.mkdir(parents=True, exist_ok=True)
        self.retries = retries
        self.timeout = timeout
        self._opener = opener
        # Optional antivirus check after the hash check (Security Center): (path, token) -> verdict dict.
        # It never replaces the SHA-256 and signature checks; it only adds a veto on a confirmed detection.
        self.antivirus: Callable[[Path, CancelToken | None], dict] | None = None
        self._av_clean: set[str] = set()

    def check_url(self, url: str) -> None:
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https":
            raise DownloadError(f"Refusing non-HTTPS URL: {url}")
        host = (u.hostname or "").lower()
        if host not in self.allowed_hosts:
            raise DownloadError(f"Host '{host}' is not on AKENO's allow-list of official sources.")

    def opener(self):
        if self._opener is None:
            ctx = ssl.create_default_context()
            self._opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                                       _AllowlistRedirects(self.check_url))
        return self._opener

    def cached_path(self, sha256: str, name: str) -> Path:
        safe = "".join(c for c in os.path.basename(name) if c.isalnum() or c in "._-") or "download"
        return self.cache / f"{sha256[:16]}-{safe}"

    def fetch(self, url: str, sha256: str, name: str, expected_size: int | None = None,
              progress: Callable[[int, int | None], None] | None = None,
              token: CancelToken | None = None) -> DownloadResult:
        """Download ``url`` and verify it against the pinned ``sha256``. Returns the cached file."""
        sha256 = sha256.lower()
        self.check_url(url)
        dest = self.cached_path(sha256, name)
        if dest.is_file():
            if sha256_file(dest) == sha256:
                return self._scanned(DownloadResult(dest, sha256, dest.stat().st_size, True), token)
            log.warning("cached file %s is corrupt; downloading again", dest)
            dest.unlink()
        limit = min(self.hard_limit, int(expected_size * 1.05) + 4096) if expected_size else self.hard_limit
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            if token and token.cancelled:
                raise Cancelled("Download cancelled.")
            try:
                res = self._attempt(url, sha256, dest, limit, progress, token)
            except (Cancelled, VerificationError):
                raise
            except DownloadError as exc:
                if "allow-list" in str(exc) or "non-HTTPS" in str(exc):
                    raise
                last = exc
            except urllib.error.HTTPError as exc:
                if 400 <= exc.code < 500 and exc.code not in (408, 416, 429):
                    raise          # 404 / 403: retrying cannot help, and the caller explains it
                last = exc
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                last = exc
            else:
                return self._scanned(res, token)
            log.info("download attempt %d failed: %s", attempt, last)
            if attempt < self.retries:
                for _ in range(int(2 ** attempt * 10)):
                    if token and token.cancelled:
                        raise Cancelled("Download cancelled.")
                    time.sleep(0.1)
        raise DownloadError(f"Download failed after {self.retries} attempts: {last}")

    def _attempt(self, url, sha256, dest: Path, limit, progress, token) -> DownloadResult:
        """One HTTP attempt. A partial ``.part`` file from an interrupted attempt is resumed with a Range
        request; it is discarded only when its bytes cannot belong to the pinned file."""
        part = dest.with_suffix(dest.suffix + ".part")
        h = hashlib.sha256()
        size = 0
        if part.is_file() and 0 < part.stat().st_size < limit:
            for chunk in iter_chunks(part):
                h.update(chunk)
                size += len(chunk)
        else:
            part.unlink(missing_ok=True)
        headers = {"User-Agent": f"AKENO-DLSS5-Manager/{__version__}"}
        if size:
            headers["Range"] = f"bytes={size}-"
        req = urllib.request.Request(url, headers=headers)
        keep_part = True
        try:
            try:
                resp = self.opener().open(req, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code == 416 and size:      # the part is already complete (or bogus): restart cleanly
                    part.unlink(missing_ok=True)
                raise
            with resp:
                self.check_url(resp.geturl())
                status = getattr(resp, "status", None) or getattr(resp, "code", None) or 200
                if size and status != 206:
                    h, size = hashlib.sha256(), 0      # server ignored the range: start over
                    mode = "wb"
                else:
                    mode = "ab" if size else "wb"
                total = resp.headers.get("Content-Length")
                total_i = int(total) + size if total and total.isdigit() else None
                if total_i and total_i > limit:
                    keep_part = False
                    raise DownloadError(f"File is larger than expected ({total_i} bytes); refusing it.")
                with open(part, mode) as out:
                    while True:
                        if token and token.cancelled:
                            raise Cancelled("Download cancelled.")
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > limit:
                            keep_part = False
                            raise DownloadError("Download exceeded its size limit; refusing it.")
                        h.update(chunk)
                        out.write(chunk)
                        if progress:
                            progress(size, total_i)
            digest = h.hexdigest()
            if digest != sha256:
                keep_part = False
                raise VerificationError(
                    f"SHA-256 mismatch: expected {sha256}, got {digest}. The file was discarded.")
            os.replace(part, dest)
            return DownloadResult(dest, digest, size, False)
        finally:
            if not keep_part and part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass

    def fetch_unpinned(self, url: str, name: str, max_size: int,
                       progress: Callable[[int, int | None], None] | None = None,
                       token: CancelToken | None = None) -> DownloadResult:
        """Download a file whose hash is not known in advance (identity is checked by the caller).

        Used only for the official ReShade setup, whose bytes AKENO then inspects. The result is stored under
        its own SHA-256 so later runs reuse it."""
        self.check_url(url)
        tmp = self.cache / ("unpinned-" + "".join(c for c in name if c.isalnum() or c in "._-"))
        req = urllib.request.Request(url, headers={"User-Agent": f"AKENO-DLSS5-Manager/{__version__}"})
        h = hashlib.sha256()
        size = 0
        try:
            with self.opener().open(req, timeout=self.timeout) as resp, open(tmp, "wb") as out:
                self.check_url(resp.geturl())
                total = resp.headers.get("Content-Length")
                total_i = int(total) if total and total.isdigit() else None
                while True:
                    if token and token.cancelled:
                        raise Cancelled("Download cancelled.")
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_size:
                        raise DownloadError("Download exceeded its size limit; refusing it.")
                    h.update(chunk)
                    out.write(chunk)
                    if progress:
                        progress(size, total_i)
            digest = h.hexdigest()
            dest = self.cached_path(digest, name)
            os.replace(tmp, dest)
            return self._scanned(DownloadResult(dest, digest, size, False), token)
        finally:
            tmp.unlink(missing_ok=True)

    def _scanned(self, res: DownloadResult, token: CancelToken | None) -> DownloadResult:
        """The antivirus verdict on a verified download: a detection stops here, anything else passes (a PC
        without a usable antivirus keeps working; the hash check above already ran)."""
        if self.antivirus is None or res.sha256 in self._av_clean:
            return res
        verdict = self.antivirus(res.path, token) or {}
        if verdict.get("status") == "threats":
            names = [str(t.get("name") or "?") for t in verdict.get("threats", [])] or ["?"]
            raise ThreatDetected(msg("{engine} reports {threat} in {file}. AKENO does not install it.",
                                     engine=verdict.get("engine") or "The antivirus", threat=", ".join(names),
                                     file=res.path.name), names)
        if verdict.get("status") == "clean":
            self._av_clean.add(res.sha256)
        return res

    def cache_size(self) -> int:
        return sum(p.stat().st_size for p in self.cache.glob("*") if p.is_file())

    def clear_cache(self) -> int:
        n = 0
        for p in self.cache.glob("*"):
            if p.is_file():
                p.unlink()
                n += 1
            elif p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
                n += 1
        return n

    def enforce_limit(self, limit_bytes: int) -> None:
        files = sorted((p for p in self.cache.glob("*") if p.is_file()), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        while files and total > limit_bytes:
            p = files.pop(0)
            total -= p.stat().st_size
            p.unlink()
