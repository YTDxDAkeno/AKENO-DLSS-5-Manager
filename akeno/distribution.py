"""Where AKENO's public files come from: the public release repository on GitHub, or AKENO's own servers.

The source code repository is private. Everything a user's PC downloads (installers, portable packages, the
signed update manifest, the compatibility database, the community index) lives in a public place, so no account,
token or login is ever needed and none is ever built into the program.

The place is set when the program is built (``akeno/data/distribution.json``, written by CI)::

    {"release_repo": "OWNER/AKENO-Releases"}                          # GitHub (the default)

    {"release_repo": "OWNER/AKENO-Releases",                          # AKENO's own release server
     "update_source": "akeno",
     "release_base": "https://downloads.akeno.online/releases",       # serves channels/<channel>.json
     "release_page": "https://akeno.online/download",
     "download_hosts": ["downloads.akeno.online"]}

Whatever the source, an update is installed only when its manifest carries AKENO's release signature (Ed25519,
keys built into the program) and every file matches the SHA-256 the manifest lists. The source only decides where
files are fetched from. Hosts outside GitHub's release hosts must belong to ``akeno.online``: a build cannot be
configured to fetch updates from anywhere else.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field

from akeno.paths import resource_dir

DEFAULT_RELEASE_REPO = "YTDxDAkeno/AKENO-Releases"
REPO = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
# hosts a GitHub release download or raw file may come from (redirects included)
HOSTS = {"github.com", "objects.githubusercontent.com", "raw.githubusercontent.com",
         "release-assets.githubusercontent.com", "api.github.com"}
OFFICIAL_DOMAIN = "akeno.online"
SOURCES = ("github", "akeno")


def official_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return host == OFFICIAL_DOMAIN or host.endswith("." + OFFICIAL_DOMAIN)


def _https_official(url: str) -> bool:
    u = urllib.parse.urlsplit(url or "")
    return u.scheme == "https" and official_host(u.hostname or "") and not u.username and not u.password


@dataclass(frozen=True)
class Distribution:
    release_repo: str
    source: str = "github"
    release_base: str = ""
    release_page: str = ""
    extra_hosts: frozenset = field(default_factory=frozenset)

    @property
    def hosts(self) -> set[str]:
        """Every host an update file or manifest may be fetched from (redirects included)."""
        return set(HOSTS) | set(self.extra_hosts)

    def trusted(self, url: str) -> bool:
        u = urllib.parse.urlsplit(url or "")
        return u.scheme == "https" and (u.hostname or "").lower() in self.hosts and not u.username

    @property
    def releases(self) -> str:
        if self.source == "akeno":
            return self.release_page or self.release_base
        return f"https://github.com/{self.release_repo}/releases"

    @property
    def label(self) -> str:
        """Where updates come from, for the Updates page."""
        if self.source == "akeno":
            return urllib.parse.urlsplit(self.release_base).hostname or OFFICIAL_DOMAIN
        return "GitHub · " + self.release_repo

    def asset(self, tag: str, name: str) -> str:
        return f"https://github.com/{self.release_repo}/releases/download/{tag}/{name}"

    def channel_manifest(self, channel: str) -> str:
        """The signed manifest of a channel, always at the same address."""
        if self.source == "akeno":
            return f"{self.release_base}/channels/{channel}.json"
        return f"https://raw.githubusercontent.com/{self.release_repo}/main/channels/{channel}.json"

    @property
    def compat_db(self) -> str:
        return f"https://raw.githubusercontent.com/{self.release_repo}/main/compatibility/compatibility.json"

    @property
    def community_index(self) -> str:
        return f"https://raw.githubusercontent.com/{self.release_repo}/main/community/index.json"


def from_dict(data: dict) -> Distribution:
    """A distribution from build data; anything not allowed falls back to GitHub (never to an unknown host)."""
    repo = DEFAULT_RELEASE_REPO
    if isinstance(data.get("release_repo"), str) and REPO.match(data["release_repo"]):
        repo = data["release_repo"]
    if data.get("update_source") != "akeno":
        return Distribution(repo)
    base = str(data.get("release_base") or "").rstrip("/")
    page = str(data.get("release_page") or "").rstrip("/")
    if not _https_official(base):
        return Distribution(repo)                   # not HTTPS on akeno.online: refused, GitHub stays
    hosts = {urllib.parse.urlsplit(base).hostname.lower()}
    for host in data.get("download_hosts") or []:
        if isinstance(host, str) and official_host(host):
            hosts.add(host.lower())
    return Distribution(repo, "akeno", base, page if _https_official(page) else "", frozenset(hosts))


def load() -> Distribution:
    try:
        data = json.loads((resource_dir() / "distribution.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return from_dict(data if isinstance(data, dict) else {})
