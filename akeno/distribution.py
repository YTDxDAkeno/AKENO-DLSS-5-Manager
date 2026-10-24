"""Where AKENO's public files come from: the public release repository on GitHub.

The source code repository is private. Everything a user's PC downloads (installers, portable packages, the
signed update manifest, the compatibility database, the community index) lives in a separate *public* release
repository, so no GitHub account, token or login is ever needed and none is ever built into the program.

The repository is set when the program is built (``akeno/data/distribution.json``, written by CI from the
repository variable ``AKENO_RELEASE_REPO``); without it the defaults below apply.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from akeno.paths import resource_dir

DEFAULT_RELEASE_REPO = "YTDxDAkeno/AKENO-Releases"
REPO = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
# hosts a GitHub release download or raw file may come from (redirects included)
HOSTS = {"github.com", "objects.githubusercontent.com", "raw.githubusercontent.com",
         "release-assets.githubusercontent.com", "api.github.com"}


@dataclass(frozen=True)
class Distribution:
    release_repo: str

    @property
    def releases(self) -> str:
        return f"https://github.com/{self.release_repo}/releases"

    def asset(self, tag: str, name: str) -> str:
        return f"https://github.com/{self.release_repo}/releases/download/{tag}/{name}"

    def channel_manifest(self, channel: str) -> str:
        """The signed manifest of a channel, always at the same address (a file in the repository)."""
        return f"https://raw.githubusercontent.com/{self.release_repo}/main/channels/{channel}.json"

    @property
    def compat_db(self) -> str:
        return f"https://raw.githubusercontent.com/{self.release_repo}/main/compatibility/compatibility.json"

    @property
    def community_index(self) -> str:
        return f"https://raw.githubusercontent.com/{self.release_repo}/main/community/index.json"


def load() -> Distribution:
    repo = DEFAULT_RELEASE_REPO
    try:
        data = json.loads((resource_dir() / "distribution.json").read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("release_repo"), str) and REPO.match(data["release_repo"]):
            repo = data["release_repo"]
    except (OSError, ValueError):
        pass
    return Distribution(repo)
