"""Where animated wallpapers come from, and how AKENO gets them (Wallpapers → Find wallpapers).

* **Pixabay** has an official API (https://pixabay.com/api/docs/) for free videos under the Pixabay Content License.
  AKENO uses it as the documentation asks:
  - with the user's own free API key, stored encrypted (DPAPI on Windows) and never in the program;
  - search results show that they come from Pixabay, and name each video's creator;
  - requests are cached for 24 hours and stay under the rate limit (100 per minute);
  - a video is downloaded only when the user chooses it, never in bulk.
* **MotionBGs, MyLiveWallpapers and DesktopHut** publish no interface for apps, and much of their content shows
  third-party characters whose rights AKENO cannot check. AKENO only opens their pages in the browser. The user
  downloads there, under each site's own terms, and imports the file here ("Recent downloads"). AKENO never
  scrapes, embeds or redistributes their content.

Network rules for Pixabay:
- HTTPS only;
- the API on ``pixabay.com`` and files on ``cdn.pixabay.com``, nothing else, redirects included;
- size limits on every response.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from akeno import __version__
from akeno.i18n import msg

log = logging.getLogger(__name__)

API_URL = "https://pixabay.com/api/videos/"
API_HOSTS = {"pixabay.com"}
FILE_HOSTS = {"cdn.pixabay.com"}
CACHE_SECONDS = 24 * 3600                  # the API documentation asks for 24 hours
RATE_LIMIT = (100, 60.0)                   # requests per seconds, per API key
MAX_JSON = 4 * 1024 * 1024
MAX_THUMB = 2 * 1024 * 1024
MAX_VIDEO = 2 * 1024 * 1024 * 1024         # 4K clips run to a few hundred MB
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".m4v"}
IMPORT_EXT = VIDEO_EXT | {".gif"}
KEY_RE = re.compile(r"^[A-Za-z0-9-]{10,80}$")

# quality choice -> Pixabay size key, in the order AKENO falls back to when a size is missing
QUALITIES = {"4k": ("large", "medium", "small"), "1080p": ("medium", "small"), "720p": ("small", "tiny")}
QUALITY_TEXT = {"4k": msg("4K"), "1080p": msg("1080p"), "720p": msg("720p")}
KIND_TEXT = {"all": msg("All videos"), "animation": msg("Animations"), "film": msg("Filmed")}
# quick searches: Pixabay has no "gaming" or "anime" category, these are search words
TOPICS = {"gaming": msg("Gaming"), "anime": msg("Anime"), "neon": msg("Neon"), "space": msg("Space"),
          "abstract": msg("Abstract"), "city night": msg("City at night"), "nature": msg("Nature"),
          "rain": msg("Rain")}


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    url: str
    about: str


# Sites without an API: AKENO links to them and imports what the user downloaded there.
SITES = (
    Site("motionbgs", "MotionBGs", "https://motionbgs.com/",
         msg("Animated wallpapers of games, anime and films, many in 4K.")),
    Site("mylivewallpapers", "MyLiveWallpapers", "https://mylivewallpapers.com/",
         msg("Live wallpapers sorted by game, anime and theme.")),
    Site("desktophut", "DesktopHut", "https://www.desktophut.com/",
         msg("Live wallpapers for Windows, with games and anime.")),
)


class SourceError(Exception):
    """A readable reason a search or download did not work; ``text()`` is it in the user's language."""

    def text(self) -> str:
        from akeno.i18n import t
        return t(self.args[0]) if self.args else ""


@dataclass(frozen=True)
class VideoFile:
    size_key: str
    url: str
    width: int
    height: int
    bytes: int
    thumbnail: str

    @property
    def label(self) -> str:
        return f"{self.width}×{self.height}"


@dataclass
class Video:
    id: int
    page_url: str
    tags: str
    duration: int
    user: str
    kind: str
    files: dict[str, VideoFile] = field(default_factory=dict)

    def best(self, quality: str) -> VideoFile | None:
        for key in QUALITIES.get(quality, QUALITIES["1080p"]):
            if key in self.files:
                return self.files[key]
        return None

    @property
    def thumbnail(self) -> str:
        for key in ("small", "medium", "tiny", "large"):
            f = self.files.get(key)
            if f and f.thumbnail:
                return f.thumbnail
        return ""

    @property
    def title(self) -> str:
        words = [w.strip() for w in self.tags.split(",") if w.strip()]
        return ", ".join(words[:3]) or f"Pixabay {self.id}"


@dataclass
class Results:
    total: int
    videos: list[Video]
    page: int
    per_page: int
    from_cache: bool = False

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // max(1, self.per_page)))


def _https_on(url: str, hosts: set[str]) -> bool:
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return u.scheme == "https" and (u.hostname or "").lower() in hosts


def parse(data: object, page: int = 1, per_page: int = 24) -> Results:
    """Pixabay's video search answer -> Results. Anything malformed or pointing elsewhere than Pixabay is dropped."""
    if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
        raise SourceError(msg("Pixabay sent an answer AKENO does not understand."))
    videos = []
    for hit in data["hits"]:
        if not isinstance(hit, dict) or not isinstance(hit.get("videos"), dict):
            continue
        try:
            vid = int(hit["id"])
        except (KeyError, TypeError, ValueError):
            continue
        page_url = str(hit.get("pageURL") or "")
        if not _https_on(page_url, API_HOSTS):
            page_url = f"https://pixabay.com/videos/id-{vid}/"
        files = {}
        for key in ("large", "medium", "small", "tiny"):
            f = hit["videos"].get(key)
            if not isinstance(f, dict):
                continue
            url = str(f.get("url") or "")
            if not url or not _https_on(url, FILE_HOSTS):        # "large" is empty when there is no 4K version
                continue
            thumb = str(f.get("thumbnail") or "")
            try:
                files[key] = VideoFile(key, url, int(f.get("width") or 0), int(f.get("height") or 0),
                                       int(f.get("size") or 0), thumb if _https_on(thumb, FILE_HOSTS) else "")
            except (TypeError, ValueError):
                continue
        if not files:
            continue
        videos.append(Video(vid, page_url, str(hit.get("tags") or "")[:200], int(hit.get("duration") or 0),
                            str(hit.get("user") or "")[:80], str(hit.get("type") or ""), files))
    try:
        total = int(data.get("totalHits", len(videos)))
    except (TypeError, ValueError):
        total = len(videos)
    return Results(total, videos, page, per_page)


class _OnlyHosts(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts: set[str]):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, message, headers, newurl):
        if not _https_on(newurl, self.hosts):
            raise SourceError(msg("Pixabay redirected to a place AKENO does not trust; nothing was loaded."))
        return super().redirect_request(req, fp, code, message, headers, newurl)


class KeyStore:
    """The user's Pixabay API key: DPAPI-encrypted on Windows, an owner-only file elsewhere. Never in settings."""

    def __init__(self, directory: Path | None = None):
        from akeno.licensing.storage import SecureStore
        from akeno.paths import sub
        self.store = SecureStore(directory or sub("pixabay"))

    def get(self) -> str:
        return str(self.store.load().get("api_key") or "")

    def set(self, key: str) -> None:
        key = key.strip()
        if not KEY_RE.match(key):
            raise SourceError(msg("That does not look like a Pixabay API key."))
        self.store.save({"api_key": key})

    def clear(self) -> None:
        self.store.clear()


class Pixabay:
    """Search and download through Pixabay's official API. ``opener`` and ``clock`` are replaceable for tests."""

    def __init__(self, key: str, cache_dir: Path | None = None, opener=None, clock: Callable[[], float] = time.time,
                 timeout: float = 20.0):
        from akeno.paths import cache_dir as default_cache
        self.key = key
        self.cache = Path(cache_dir or (default_cache() / "pixabay"))
        self.cache.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.timeout = timeout
        self._opener = opener
        self._sent: list[float] = []

    # -- transport --------------------------------------------------------------------------------------------
    def opener(self):
        if self._opener is None:
            self._opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=ssl.create_default_context()),
                _OnlyHosts(API_HOSTS | FILE_HOSTS))
        return self._opener

    def _open(self, url: str, hosts: set[str]):
        if not _https_on(url, hosts):
            raise SourceError(msg("AKENO only talks to Pixabay over HTTPS."))
        req = urllib.request.Request(url, headers={"User-Agent": f"AKENO-Gaming-Suite/{__version__}"})
        try:
            resp = self.opener().open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403):
                raise SourceError(msg("Pixabay did not accept the API key.")) from None
            if exc.code == 429:
                raise SourceError(msg("Pixabay asks to wait a moment (too many requests).")) from None
            raise SourceError(msg("Pixabay answered with an error ({code}).", code=exc.code)) from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise SourceError(msg("Pixabay cannot be reached: {e}", e=str(getattr(exc, "reason", exc)))) from None
        if not _https_on(resp.geturl(), hosts):
            resp.close()
            raise SourceError(msg("Pixabay redirected to a place AKENO does not trust; nothing was loaded."))
        return resp

    def _throttle(self) -> None:
        limit, window = RATE_LIMIT
        now = self.clock()
        self._sent = [t for t in self._sent if now - t < window]
        if len(self._sent) >= limit:
            raise SourceError(msg("Pixabay allows {n} searches a minute; wait a moment.", n=limit))
        self._sent.append(now)

    # -- search -----------------------------------------------------------------------------------------------
    def search_url(self, query: str, kind: str = "all", page: int = 1, per_page: int = 24,
                   min_width: int = 0) -> str:
        params = {"key": self.key, "q": query.strip()[:100], "video_type": kind if kind in KIND_TEXT else "all",
                  "safesearch": "true", "order": "popular", "page": max(1, int(page)),
                  "per_page": max(3, min(200, int(per_page)))}
        if min_width:
            params["min_width"] = int(min_width)
        return API_URL + "?" + urllib.parse.urlencode(params)

    def search(self, query: str, kind: str = "all", page: int = 1, per_page: int = 24, quality: str = "1080p",
               ) -> Results:
        if not self.key:
            raise SourceError(msg("Add your free Pixabay API key first."))
        url = self.search_url(query, kind, page, per_page, 3840 if quality == "4k" else 0)
        cached = self.cache / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".json")
        try:
            if self.clock() - cached.stat().st_mtime < CACHE_SECONDS:
                res = parse(json.loads(cached.read_text(encoding="utf-8")), page, per_page)
                res.from_cache = True
                return res
        except (OSError, ValueError, SourceError):
            pass
        self._throttle()
        with self._open(url, API_HOSTS) as resp:
            raw = resp.read(MAX_JSON + 1)
        if len(raw) > MAX_JSON:
            raise SourceError(msg("Pixabay sent an answer AKENO does not understand."))
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise SourceError(msg("Pixabay sent an answer AKENO does not understand.")) from None
        res = parse(data, page, per_page)
        tmp = cached.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, cached)
        return res

    def thumbnail(self, url: str) -> bytes:
        """A preview picture (small JPEG), cached like the searches."""
        if not url:
            return b""
        cached = self.cache / ("t-" + hashlib.sha256(url.encode()).hexdigest()[:32] + ".jpg")
        try:
            if self.clock() - cached.stat().st_mtime < CACHE_SECONDS:
                return cached.read_bytes()
        except OSError:
            pass
        with self._open(url, FILE_HOSTS) as resp:
            data = resp.read(MAX_THUMB + 1)
        if len(data) > MAX_THUMB:
            raise SourceError(msg("The preview picture is too large."))
        cached.write_bytes(data)
        return data

    # -- download ---------------------------------------------------------------------------------------------
    def download(self, video: Video, quality: str, library: Path,
                 progress: Callable[[int, int | None], None] | None = None, token=None) -> Path:
        """Download the chosen size of ``video`` into ``library`` and record where it came from next to it."""
        chosen = video.best(quality)
        if chosen is None:
            raise SourceError(msg("This video has no file in that quality."))
        library.mkdir(parents=True, exist_ok=True)
        dest = library / f"pixabay-{video.id}-{chosen.size_key}.mp4"
        if dest.is_file() and (not chosen.bytes or dest.stat().st_size == chosen.bytes):
            return dest
        limit = min(MAX_VIDEO, int(chosen.bytes * 1.05) + 65536) if chosen.bytes else MAX_VIDEO
        part = dest.with_suffix(".part")
        size = 0
        try:
            with self._open(chosen.url, FILE_HOSTS) as resp, open(part, "wb") as out:
                total = resp.headers.get("Content-Length") if getattr(resp, "headers", None) else None
                total_i = int(total) if total and str(total).isdigit() else (chosen.bytes or None)
                if total_i and total_i > limit:
                    raise SourceError(msg("The video is larger than Pixabay said; it was not saved."))
                head = b""
                while True:
                    if token is not None and token.cancelled:
                        raise SourceError(msg("Download cancelled."))
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    if len(head) < 12:
                        head += chunk[:12]
                    size += len(chunk)
                    if size > limit:
                        raise SourceError(msg("The video is larger than Pixabay said; it was not saved."))
                    out.write(chunk)
                    if progress:
                        progress(size, total_i)
            if head[4:8] != b"ftyp":                             # an MP4 starts with its "ftyp" box
                raise SourceError(msg("The download is not a video; it was not saved."))
            os.replace(part, dest)
        finally:
            part.unlink(missing_ok=True)
        credit = {"source": "Pixabay", "license": "Pixabay Content License", "page": video.page_url,
                  "creator": video.user, "id": video.id, "size": chosen.label, "downloaded": int(self.clock())}
        dest.with_suffix(".json").write_text(json.dumps(credit, indent=1), encoding="utf-8")
        return dest


# -- files the user downloaded from a site without an API ----------------------------------------------------
def recent_downloads(folder: Path | None = None, days: int = 14, limit: int = 8, now: float | None = None
                     ) -> list[Path]:
    """Video and GIF files in the Downloads folder from the last ``days`` days, newest first."""
    if folder is None:
        from akeno.desktop.sensors import downloads_dir
        folder = downloads_dir()
    now = time.time() if now is None else now
    found = []
    try:
        for entry in os.scandir(folder):
            if not entry.is_file(follow_symlinks=False) or Path(entry.name).suffix.lower() not in IMPORT_EXT:
                continue
            mtime = entry.stat().st_mtime
            if now - mtime <= days * 86400:
                found.append((mtime, Path(entry.path)))
    except OSError:
        return []
    return [p for _, p in sorted(found, reverse=True)[:limit]]


def import_file(path: Path, library: Path) -> Path:
    """Copy a downloaded wallpaper into AKENO's wallpaper library, so cleaning the Downloads folder keeps it."""
    path = Path(path)
    if path.suffix.lower() not in IMPORT_EXT or not path.is_file():
        raise SourceError(msg("Only video files (MP4, WebM, MKV, MOV) and GIFs can be imported here."))
    library.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in path.stem if c.isalnum() or c in " ._-").strip()[:80] or "wallpaper"
    dest = library / f"{safe}{path.suffix.lower()}"
    n = 2
    while dest.exists():
        if dest.stat().st_size == path.stat().st_size:
            return dest                                          # imported before
        dest = library / f"{safe} ({n}){path.suffix.lower()}"
        n += 1
    tmp = dest.with_name(dest.name + ".part")
    shutil.copyfile(path, tmp)
    os.replace(tmp, dest)
    return dest


def library_dir() -> Path:
    from akeno.paths import sub
    return sub("wallpapers")
