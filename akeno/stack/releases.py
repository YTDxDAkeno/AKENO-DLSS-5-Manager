"""GitHub release discovery for the catalog's original repositories.

Discovery tells the user that a newer upstream release exists and lets them install it as an
*unreviewed* version. It never changes a reviewed catalog pin. Responses are cached per repository
(with ETags, so unchanged pages cost no rate limit) and the cache keeps working offline.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from akeno import __version__
from akeno.security.downloads import CancelToken, Cancelled

from .catalog import Component, ComponentVersion, _check_asset_url
from .errors import StackError

API = "https://api.github.com"
API_LIMIT = 8 * 1024 * 1024
MAX_PAGES = 3
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise StackError("GitHub reports that this repository moved.",
                         "AKENO keeps the reviewed versions; update AKENO for the new repository location.",
                         "repository_moved")


@dataclass
class RefreshResult:
    repository: str
    ok: bool
    releases: int = 0
    error: str = ""
    from_cache: bool = False


class GitHubReleases:
    def __init__(self, db, repositories, opener=None, token_getter=None):
        self.db = db
        self.allowed = frozenset(repositories)
        self.opener = opener or urllib.request.build_opener(_NoRedirect())
        self.token_getter = token_getter or (lambda: os.environ.get("GITHUB_TOKEN") or "")

    def _key(self, repo: str) -> str:
        return "stack-releases:" + repo

    def cached(self, repo: str) -> dict:
        return self.db.get_setting(self._key(repo), {}) or {}

    def releases(self, repo: str) -> list[dict]:
        return list(self.cached(repo).get("releases", []))

    def _request(self, url: str, etag: str | None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": f"AKENO-DLSS5-Manager/{__version__}"}
        token = (self.token_getter() or "").strip()
        if token:
            headers["Authorization"] = "Bearer " + token
        if etag:
            headers["If-None-Match"] = etag
        return self.opener.open(urllib.request.Request(url, headers=headers), timeout=30)

    def refresh(self, repo: str, token: CancelToken | None = None) -> list[dict]:
        if repo not in self.allowed or not _REPO.fullmatch(repo):
            raise StackError("This repository is not part of AKENO's catalog.", code="not_allowed")
        old = self.cached(repo)
        etags, pages = dict(old.get("etags", {})), dict(old.get("pages", {}))
        new_pages: dict[str, list] = {}
        new_etags: dict[str, str] = {}
        for page in range(1, MAX_PAGES + 1):
            if token and token.cancelled:
                raise Cancelled("Release check cancelled.")
            url = f"{API}/repos/{repo}/releases?per_page=100&page={page}"
            try:
                with self._request(url, etags.get(str(page))) as resp:
                    final = urllib.parse.urlsplit(resp.geturl())
                    if final.netloc != "api.github.com" or final.path != f"/repos/{repo}/releases":
                        raise StackError("Unexpected GitHub API response source.", code="api_source")
                    body = resp.read(API_LIMIT + 1)
                    if len(body) > API_LIMIT:
                        raise StackError("Release metadata is unexpectedly large.", code="api_size")
                    rows = json.loads(body)
                    new_etags[str(page)] = resp.headers.get("ETag", "")
            except urllib.error.HTTPError as exc:
                if exc.code == 304:
                    rows = pages.get(str(page), [])
                    new_etags[str(page)] = etags.get(str(page), "")
                elif exc.code in (403, 429):
                    reset = exc.headers.get("X-RateLimit-Reset") if exc.headers else None
                    when = ""
                    if reset and reset.isdigit():
                        when = " (resets at " + datetime.fromtimestamp(int(reset)).strftime("%H:%M") + ")"
                    raise StackError("GitHub rate limit reached" + when + ".",
                                     "Cached versions stay available. Add a GitHub token in Settings to check more often.",
                                     "rate_limited") from exc
                elif exc.code == 404:
                    raise StackError("The upstream repository is no longer available.",
                                     "Reviewed versions and cached downloads keep working.", "repo_missing") from exc
                else:
                    raise
            if not isinstance(rows, list) or len(rows) > 100:
                raise StackError("GitHub returned an invalid release list.", code="api_invalid")
            new_pages[str(page)] = rows
            if len(rows) < 100:
                break
        releases = []
        for rows in new_pages.values():
            for row in rows:
                if row.get("draft"):
                    continue
                assets = []
                for item in (row.get("assets") or [])[:256]:
                    name, url = item.get("name"), item.get("browser_download_url")
                    if not isinstance(name, str) or not name or "/" in name or "\\" in name or not isinstance(url, str):
                        continue
                    try:
                        _check_asset_url(repo, url)
                    except ValueError:
                        continue
                    digest = item.get("digest") or ""
                    pin = digest[7:] if digest.startswith("sha256:") and _SHA.fullmatch(digest[7:]) else None
                    assets.append({"name": name, "url": url, "size": item.get("size"), "sha256": pin,
                                   "updated_at": item.get("updated_at")})
                releases.append({"tag": str(row.get("tag_name") or ""), "name": str(row.get("name") or ""),
                                 "prerelease": row.get("prerelease") is True,
                                 "published_at": row.get("published_at"), "url": row.get("html_url"),
                                 "assets": assets})
        # Only a complete, validated refresh replaces the cache.
        self.db.set_setting(self._key(repo), {"checked_at": datetime.now(timezone.utc).isoformat(),
                                              "etags": new_etags, "pages": new_pages, "releases": releases})
        return releases

    def refresh_all(self, token: CancelToken | None = None, progress=None) -> list[RefreshResult]:
        out = []
        repos = sorted(self.allowed)
        for i, repo in enumerate(repos):
            if token and token.cancelled:
                raise Cancelled("Release check cancelled.")
            try:
                out.append(RefreshResult(repo, True, len(self.refresh(repo, token))))
            except Cancelled:
                raise
            except StackError as exc:
                out.append(RefreshResult(repo, False, len(self.releases(repo)), str(exc), True))
                if exc.code == "rate_limited":
                    # Further requests would fail the same way.
                    out.extend(RefreshResult(r, False, len(self.releases(r)), str(exc), True) for r in repos[i + 1:])
                    break
            except (urllib.error.URLError, OSError, ValueError) as exc:
                out.append(RefreshResult(repo, False, len(self.releases(repo)),
                                         "No connection to GitHub; cached versions are shown.", True))
            if progress:
                progress(i + 1, len(repos))
        self.db.set_setting("stack-releases-last", [r.__dict__ for r in out])
        return out

    # -- mapping releases to installable (unreviewed) versions -----------------------------------
    def discovered_versions(self, component: Component) -> list[ComponentVersion]:
        rule = component.release or {}
        if not component.repository or not rule:
            return []
        tag_rx = re.compile(rule["tag"])
        asset_rx = re.compile(rule["asset"])
        known_tags = {v.tag for v in component.versions if v.tag}
        out = []
        for rel in self.releases(component.repository):
            m = tag_rx.match(rel["tag"])
            if not m or rel["tag"] in known_tags:
                continue
            asset = next((a for a in rel["assets"] if asset_rx.match(a["name"])), None)
            if asset is None:
                continue
            version = m.group(1)
            out.append(ComponentVersion(
                component=component.id, version=version, source="download", label=version,
                channel="unreviewed", url=asset["url"], sha256=asset.get("sha256"), size=asset.get("size"),
                tag=rel["tag"], asset=asset["name"], root=rule.get("root", ""),
                files={name: None for name in rule.get("files", [])},
                published=(rel.get("published_at") or "")[:10] or None, reviewed=False,
                notes=("Newer upstream release, not yet reviewed by AKENO. GitHub's published digest is used "
                       "when available; the files are checked by name and type only.",)))
        return out
