"""Reviewed artifact catalog (pinned download URLs and SHA-256 hashes)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from akeno.core.models import TrustState
from akeno.paths import resource_dir

_SHA = re.compile(r"^[0-9a-f]{64}$")


class CatalogError(ValueError):
    pass


@dataclass
class Package:
    id: str
    backend: str
    name: str
    version: str
    upstream: str
    url: str | None
    sha256: str | None
    license: str
    trust: TrustState
    reviewed: str | None = None
    review_notes: str = ""
    size: int | None = None
    root: str = ""
    files: dict[str, str] = field(default_factory=dict)
    apis: list[str] = field(default_factory=list)
    architectures: list[str] = field(default_factory=list)

    @property
    def installable(self) -> bool:
        return self.trust == TrustState.VERIFIED and bool(self.url and self.sha256)


@dataclass
class Tool:
    id: str
    name: str
    version: str
    url: str
    sha256: str
    license: str
    file_name: str
    size: int | None = None
    trust: TrustState = TrustState.VERIFIED
    upstream: str = ""
    reviewed: str | None = None


class Catalog:
    def __init__(self, data: dict[str, Any]):
        self.validate(data)
        self.version = data.get("catalog_version", "?")
        self.allowed_hosts = [h.lower() for h in data.get("allowed_hosts", [])]
        self.packages = [self._pkg(p) for p in data.get("packages", [])]
        self.tools = [Tool(id=t["id"], name=t["name"], version=t["version"], url=t["url"], sha256=t["sha256"],
                           license=t.get("license", "?"), file_name=t["file_name"], size=t.get("size"),
                           trust=TrustState(t.get("trust", "verified")), upstream=t.get("upstream", ""),
                           reviewed=t.get("reviewed")) for t in data.get("tools", [])]

    @staticmethod
    def _pkg(p: dict[str, Any]) -> Package:
        return Package(id=p["id"], backend=p["backend"], name=p["name"], version=p["version"],
                       upstream=p.get("upstream", ""), url=p.get("url"), sha256=p.get("sha256"),
                       license=p.get("license", "?"), trust=TrustState(p.get("trust", "unverified")),
                       reviewed=p.get("reviewed"), review_notes=p.get("review_notes", ""), size=p.get("size"),
                       root=p.get("root", ""), files=dict(p.get("files", {})), apis=list(p.get("apis", [])),
                       architectures=list(p.get("architectures", [])))

    @staticmethod
    def validate(data: dict[str, Any]) -> None:
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise CatalogError("unsupported catalog schema")
        for p in data.get("packages", []) + data.get("tools", []):
            for k in ("id", "name", "version"):
                if not p.get(k):
                    raise CatalogError(f"catalog entry missing {k}")
            trust = p.get("trust", "verified" if "file_name" in p else "unverified")
            if trust == "verified":
                if not (p.get("url") or "").startswith("https://"):
                    raise CatalogError(f"{p['id']}: verified entries need an https URL")
                if not _SHA.match(p.get("sha256") or ""):
                    raise CatalogError(f"{p['id']}: verified entries need a pinned SHA-256")
            for name, h in (p.get("files") or {}).items():
                if not _SHA.match(h) or ".." in name.replace("\\", "/").split("/"):
                    raise CatalogError(f"{p['id']}: bad file entry {name}")

    @classmethod
    def load_bundled(cls) -> "Catalog":
        return cls(json.loads(Path(resource_dir(), "catalog.json").read_text(encoding="utf-8")))

    def package(self, package_id: str) -> Package | None:
        return next((p for p in self.packages if p.id == package_id), None)

    def packages_for(self, backend: str) -> list[Package]:
        return [p for p in self.packages if p.backend == backend]

    def best_package(self, backend: str) -> Package | None:
        pk = self.packages_for(backend)
        installable = [p for p in pk if p.installable]
        return installable[0] if installable else (pk[0] if pk else None)

    def tool(self, tool_id_prefix: str) -> Tool | None:
        return next((t for t in self.tools if t.id.startswith(tool_id_prefix)), None)
