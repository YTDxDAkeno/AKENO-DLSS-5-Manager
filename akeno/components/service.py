"""Persistent optional-component activation, independent of resettable application settings.

Security Center is the first physically separable component. Other suite features still share the Core
bundle and are deliberately not advertised as separately downloadable packages. The build inventory is
trusted as part of the installed application; remote inventories MUST arrive inside the signed release ZIP.
"""
from __future__ import annotations

import configparser
import json
import sqlite3
import sys
from pathlib import Path

from akeno import __version__
from akeno.paths import app_home
from akeno.updater import staging

INVENTORY = "akeno-components.json"
SELECTION = "akeno-components.ini"
SECURITY_FILES = (
    "akeno/ui/pages/security.py",
    "akeno/system/securitycenter/__init__.py",
    "akeno/system/securitycenter/defender.py",
    "akeno/system/securitycenter/filecheck.py",
    "akeno/system/securitycenter/processes.py",
    "akeno/system/securitycenter/providers.py",
    "akeno/system/securitycenter/service.py",
    "akeno/system/securitycenter/wsc.py",
)


class ComponentError(RuntimeError):
    pass


def selection(root: Path) -> bool:
    """Legacy installs keep their existing Security Center; new Core-only installs explicitly opt out."""
    path = Path(root) / SELECTION
    if not path.exists():
        return True
    cfg = configparser.ConfigParser()
    try:
        cfg.read_string(path.read_text(encoding="utf-8-sig"))
        return cfg.getboolean("components", "security_center")
    except (OSError, ValueError, configparser.Error) as exc:
        raise ComponentError("Component selection is damaged; run the installer to repair it.") from exc


def inventory(root: Path) -> dict:
    """Validate an installed/build inventory; only the fixed Security Center file set can be optional."""
    try:
        data = json.loads((Path(root) / INVENTORY).read_text(encoding="utf-8"))
        files = data["security_center"]["files"]
        expected = {"_internal/" + p for p in SECURITY_FILES}
        if data["schema"] != 1 or set(files) != expected or not isinstance(data["version"], str):
            raise ValueError("unsupported component inventory")
        for rel, record in files.items():
            staging.safe_name(rel)
            if set(record) != {"sha256", "size"} or not isinstance(record["size"], int) or record["size"] < 0 \
                    or not isinstance(record["sha256"], str) or len(record["sha256"]) != 64 \
                    or any(c not in "0123456789abcdef" for c in record["sha256"]):
                raise ValueError("invalid file record")
        return data
    except (OSError, KeyError, TypeError, ValueError, staging.StagingError) as exc:
        raise ComponentError("Component inventory is missing or damaged; run the installer to repair it.") from exc


def selected_content(source: Path, installed: Path, content: dict) -> dict:
    """Project an ALREADY VERIFIED complete release onto the installed component selection.

    Called only after staging.verify(source, content). The signed archive/content aggregate is checked by
    UpdateService before apply starts. The original content list is never rewritten. Missing new inventories
    fail closed for Core-only installations so an older monolithic release cannot reinstall Secure Center.
    """
    if selection(installed):
        return content
    data = inventory(source)
    optional = data["security_center"]["files"]
    for rel, record in optional.items():
        if content["files"].get(rel) != record:
            raise ComponentError("Component inventory differs from the verified release.")
    files = {rel: record for rel, record in content["files"].items() if rel not in optional}
    return {**content, "files": files, "file_count": len(files),
            "bytes": sum(r["size"] for r in files.values())}


class ComponentService:
    def __init__(self, root: Path | None = None, state: Path | None = None, frozen: bool | None = None):
        self.frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        self.root = Path(root) if root else (Path(sys.executable).resolve().parent if self.frozen
                                           else Path(__file__).resolve().parents[2])
        self.state = Path(state) if state else app_home() / "components.sqlite3"
        self.state.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS components (id TEXT PRIMARY KEY, enabled INTEGER NOT NULL)")

    def _db(self):
        return sqlite3.connect(self.state, timeout=10)

    def status(self) -> dict:
        """A missing, blocked or altered file is never reported as an installed component."""
        result = {"id": "security_center", "version": __version__, "state": "error", "detail": "",
                  "packaging": "separate" if self.frozen else "source", "dependencies": ["core"],
                  "requires_admin": False}
        try:
            selected = selection(self.root) if self.frozen else True
            if self.frozen:
                data = inventory(self.root)
                if data["version"] != __version__:
                    result.update(state="incompatible", detail="Component and Core versions differ.")
                    return result
                files = data["security_center"]["files"]
            else:
                files = {p: None for p in SECURITY_FILES}
            found = [rel for rel in files if (self.root / rel).exists()]
            if not found and not selected:
                result.update(state="not_installed", detail="Run the AKENO installer to add Security Center.")
                return result
            for rel, record in files.items():
                path = self.root / rel
                staging.no_links_on_the_way(path, self.root)
                if not path.is_file() or record is not None and (path.stat().st_size != record["size"] or
                                                               staging.sha256_of(path) != record["sha256"]):
                    raise ComponentError("Security Center files are missing, blocked or changed. Repair with the installer.")
            with self._db() as db:
                row = db.execute("SELECT enabled FROM components WHERE id='security_center'").fetchone()
            enabled = selected and (bool(row[0]) if row else True)
            result.update(state="active" if enabled else "disabled")
        except (ComponentError, OSError, sqlite3.Error, staging.StagingError) as exc:
            result["detail"] = str(exc)
        return result

    def active(self) -> bool:
        return self.status()["state"] == "active"

    def set_enabled(self, enabled: bool) -> None:
        if self.status()["state"] not in ("active", "disabled"):
            raise ComponentError("Security Center must be installed and verified before activation.")
        if enabled and self.frozen and not selection(self.root):
            raise ComponentError("Run the installer and select Security Center first.")
        with self._db() as db:
            db.execute("INSERT INTO components VALUES ('security_center', ?) "
                       "ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled", (int(bool(enabled)),))
