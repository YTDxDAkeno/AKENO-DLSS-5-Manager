"""The community index: a reviewed list of packages and themes, published in the public release repository.

Moderation happens there: a package gets into the index through a reviewed pull request, a report is a GitHub
issue the user opens in the browser (nothing is sent by AKENO), and a package found to be bad is removed and its
SHA-256 put on the index's block list, which AKENO also applies to files imported by hand.

    {"format": "akeno-community-index", "version": 1, "updated": "...",
     "packages": [{"id", "name", "kind", "author", "description", "license", "url", "sha256", "size",
                   "game_ids"}],
     "blocked": ["<sha256>", ...]}

Nothing is fetched until the user opens the Community page and refreshes it.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from akeno import __version__
from akeno.distribution import HOSTS
from akeno.packages import packs
from akeno.paths import sub

FORMAT = "akeno-community-index"
MAX_INDEX = 2 * 2 ** 20
SIZE_LIMITS = {"theme": 1024 * 2 ** 20, **{k: packs.MAX_BYTES for k in packs.KINDS}}
SHA = re.compile(r"^[0-9a-f]{64}$")


class IndexError_(ValueError):
    pass


@dataclass
class Entry:
    id: str
    name: str
    kind: str
    url: str
    sha256: str
    size: int
    author: str = ""
    description: str = ""
    license: str = ""
    game_ids: list = field(default_factory=list)


@dataclass
class Index:
    entries: list
    blocked: set
    updated: str = ""
    fetched: float = 0.0


def _https_github(url: str) -> bool:
    u = urllib.parse.urlsplit(url)
    return u.scheme == "https" and (u.hostname or "").lower() in HOSTS


def parse(raw) -> Index:
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise IndexError_("not an AKENO community index")
    entries = []
    for item in (raw.get("packages") or [])[:2000]:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        url, sha, size = item.get("url"), item.get("sha256"), item.get("size")
        if kind not in SIZE_LIMITS or not isinstance(url, str) or not _https_github(url) or \
                not isinstance(sha, str) or not SHA.match(sha) or not isinstance(size, int) or \
                not 0 < size <= SIZE_LIMITS[kind]:
            continue
        text = {k: (item.get(k) if isinstance(item.get(k), str) else "")[:limit]
                for k, limit in (("id", 80), ("name", 60), ("author", 60), ("description", 600), ("license", 60))}
        if not text["id"] or not text["name"]:
            continue
        ids = [g for g in (item.get("game_ids") or []) if isinstance(g, str) and packs.GAME_ID.match(g)][:10]
        entries.append(Entry(text["id"], text["name"], kind, url, sha, size, text["author"], text["description"],
                             text["license"], ids))
    blocked = {b for b in (raw.get("blocked") or []) if isinstance(b, str) and SHA.match(b)}
    return Index(entries, blocked, str(raw.get("updated") or "")[:30])


def cache_path() -> Path:
    return sub("community") / "index.json"


def cached() -> Index | None:
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
        index = parse(data["index"])
        index.fetched = float(data.get("fetched", 0))
        return index
    except (OSError, ValueError, KeyError):
        return None


def blocked() -> set[str]:
    index = cached()
    return index.blocked if index else set()


def _get(url: str, limit: int, timeout: float = 20) -> bytes:
    if not _https_github(url):
        raise IndexError_(f"refusing {url}: not the public release repository")

    class Redirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg_, headers, newurl):
            if not _https_github(newurl):
                raise IndexError_(f"refused redirect to {urllib.parse.urlsplit(newurl).hostname}")
            return super().redirect_request(req, fp, code, msg_, headers, newurl)
    opener = urllib.request.build_opener(Redirects)
    req = urllib.request.Request(url, headers={"User-Agent": f"AKENO/{__version__}"})
    with opener.open(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise IndexError_("the download is larger than announced")
    return data


def refresh(url: str) -> Index:
    raw = json.loads(_get(url, MAX_INDEX).decode("utf-8"))
    index = parse(raw)
    index.fetched = time.time()
    cache_path().write_text(json.dumps({"fetched": index.fetched, "index": raw}), encoding="utf-8")
    return index


def download(entry: Entry, folder: Path | None = None) -> Path:
    """The entry's file, verified against the index's SHA-256."""
    data = _get(entry.url, entry.size)
    if hashlib.sha256(data).hexdigest() != entry.sha256:
        raise IndexError_("the download does not match the index (SHA-256)")
    folder = Path(folder or sub("community") / "downloads")
    folder.mkdir(parents=True, exist_ok=True)
    suffix = ".akenotheme" if entry.kind == "theme" else packs.SUFFIX
    target = folder / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', entry.id)}{suffix}"
    target.write_bytes(data)
    return target


def report_url(repo: str, name: str, sha256: str) -> str:
    """A GitHub issue form the user opens in the browser to report a package (nothing is sent by AKENO)."""
    query = urllib.parse.urlencode({"title": f"Report: {name}", "labels": "community-report",
                                    "body": f"Package: {name}\nSHA-256: {sha256}\n\nWhat is wrong with it?\n"})
    return f"https://github.com/{repo}/issues/new?{query}"
