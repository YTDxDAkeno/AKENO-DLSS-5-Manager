"""Cloud Sync, local first.

AKENO's own data becomes small JSON items in a folder the user picks, usually one that a cloud drive (OneDrive,
Dropbox, Google Drive) or a NAS already copies between PCs. AKENO never uploads anything itself and the VPS is
not involved: it is the license server only. Without the folder everything keeps working; a sync only merges.

    <folder>/AKENO Sync/akeno-sync.json      format, devices, encryption check
    <folder>/AKENO Sync/items/<id>.json      one item: settings, a game's notes, a benchmark, ...

Each PC remembers the version of every item it last synced (the base). Changed only here: written to the folder.
Changed only on the other PC: taken over. Changed on both: merged field by field; a field changed differently on
both PCs is a conflict the user decides. Deleting here deletes there (a tombstone), never the other way round
without that base.

Never synced: license state and tokens, passwords, the GitHub token, program files and DLLs, game files,
wallpaper media, logs and caches. Items are built from fixed lists of fields, and every item is checked for
secret-looking keys before it is written. With a passphrase the items are encrypted (AES-256-GCM, scrypt key).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import platform
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from akeno.i18n import msg
from akeno.paths import sub

log = logging.getLogger(__name__)

FORMAT = "akeno-sync"
VERSION = 1
FOLDER = "AKENO Sync"
ITEM_LIMIT = 1 * 2 ** 20
DEFAULT_QUOTA_MB = 25
FORBIDDEN = re.compile(r"token|passw|secret|refresh|lease|certificate|private|cookie|api_?key", re.I)
CATEGORIES = {"settings": "Settings", "games": "Game notes, favourites, categories and playtime",
              "dlss": "DLSS 5 choices per game", "booster": "FPS Booster choices per game",
              "benchmarks": "Benchmark results", "desktop": "Widgets, pause rules and dock style"}


class SyncError(Exception):
    pass


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def guard(value, where: str = "") -> None:
    """Refuse anything that looks like a secret or is not plain JSON data."""
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str) or FORBIDDEN.search(k):
                raise SyncError(f"refused field {where}{k}")
            guard(v, f"{where}{k}.")
    elif isinstance(value, list):
        for v in value:
            guard(v, where)
    elif isinstance(value, str):
        if len(value) > 200_000:
            raise SyncError(f"text too long in {where.rstrip('.')}")
    elif not isinstance(value, (int, float, bool, type(None))):
        raise SyncError(f"not plain data in {where.rstrip('.')}")


PER_DEVICE = {"playtime", "launches"}          # each PC writes only its own entry


def merge(base: dict | None, local: dict, remote: dict) -> tuple[dict, list[str]]:
    """Three-way merge of two versions of one item; returns the merged item and the conflicting fields."""
    base = base or {}
    out, conflicts = {}, []
    for key in sorted(set(local) | set(remote) | set(base)):
        b, mine, theirs = base.get(key), local.get(key), remote.get(key)
        if mine == theirs:
            value = mine
        elif mine == b:
            value = theirs
        elif theirs == b:
            value = mine
        elif key in PER_DEVICE and isinstance(mine, dict) and isinstance(theirs, dict):
            value = {d: max(int(mine.get(d, 0) or 0), int(theirs.get(d, 0) or 0)) for d in set(mine) | set(theirs)}
        elif key == "last_played":
            value = max(x for x in (mine, theirs) if x) if (mine or theirs) else None
        else:
            conflicts.append(key)
            value = b
        if value is not None or key in local or key in remote:
            out[key] = value
    return out, conflicts


@dataclass
class Report:
    pushed: int = 0
    pulled: int = 0
    deleted: int = 0
    merged: int = 0
    conflicts: int = 0
    skipped: list = field(default_factory=list)

    def text(self):
        return msg("{p} sent, {r} received, {m} merged, {c} conflict(s)", p=self.pushed, r=self.pulled,
                   m=self.merged, c=self.conflicts)


class Cipher:
    """AES-256-GCM with a key derived from the passphrase (scrypt, n=2^15)."""

    def __init__(self, passphrase: str, salt: bytes):
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        self.key = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode("utf-8"))
        self.name_key = hashlib.sha256(b"akeno-sync-names" + self.key).digest()

    def seal(self, data: bytes) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        return base64.b64encode(nonce + AESGCM(self.key).encrypt(nonce, data, b"akeno-sync")).decode()

    def open(self, text: str) -> bytes:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw = base64.b64decode(text)
        try:
            return AESGCM(self.key).decrypt(raw[:12], raw[12:], b"akeno-sync")
        except InvalidTag as exc:
            raise SyncError("wrong passphrase") from exc


class FolderStore:
    """The shared folder. Every write is atomic (temporary file, then rename), so a cloud drive never uploads
    half an item."""

    def __init__(self, folder: Path, passphrase: str | None = None, create: bool = True):
        self.root = Path(folder) / FOLDER
        self.items = self.root / "items"
        self.meta_path = self.root / "akeno-sync.json"
        self.cipher: Cipher | None = None
        if not Path(folder).is_dir():
            raise SyncError("the sync folder does not exist")
        if self.meta_path.exists():
            self.meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            if self.meta.get("format") != FORMAT or int(self.meta.get("version", 0)) > VERSION:
                raise SyncError("the folder holds a sync of a newer AKENO")
            if self.meta.get("encrypted"):
                if not passphrase:
                    raise SyncError("this sync folder needs its passphrase")
                self.cipher = Cipher(passphrase, base64.b64decode(self.meta["salt"]))
                if self.cipher.open(self.meta["check"]) != b"akeno-sync":
                    raise SyncError("wrong passphrase")
        elif create:
            self.meta = {"format": FORMAT, "version": VERSION, "encrypted": bool(passphrase), "devices": {}}
            if passphrase:
                salt = os.urandom(16)
                self.cipher = Cipher(passphrase, salt)
                self.meta.update(salt=base64.b64encode(salt).decode(), check=self.cipher.seal(b"akeno-sync"))
            self.items.mkdir(parents=True, exist_ok=True)
            self.write_meta()
        else:
            raise SyncError("no AKENO sync in this folder")
        self.items.mkdir(parents=True, exist_ok=True)

    def write_meta(self) -> None:
        self._atomic(self.meta_path, json.dumps(self.meta, indent=1, sort_keys=True).encode("utf-8"))

    @staticmethod
    def _atomic(path: Path, data: bytes) -> None:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def file_for(self, key: str) -> Path:
        if self.cipher:
            name = hashlib.sha256(self.cipher.name_key + key.encode()).hexdigest()[:40]
        else:
            name = hashlib.sha256(key.encode()).hexdigest()[:40]
        return self.items / f"{name}.json"

    def read_all(self) -> tuple[dict[str, dict], list[str]]:
        """{key: record} of every readable item, and the files that could not be read (yet)."""
        out, unreadable = {}, []
        for f in sorted(self.items.glob("*.json")):
            try:
                record = json.loads(f.read_text(encoding="utf-8"))
                if self.cipher:
                    record = json.loads(self.cipher.open(record["enc"]))
                if isinstance(record, dict) and isinstance(record.get("key"), str) and \
                        self.file_for(record["key"]).name == f.name:
                    out[record["key"]] = record
            except (OSError, ValueError, KeyError, SyncError) as exc:
                unreadable.append(f"{f.name}: {exc}")
        return out, unreadable

    def write(self, record: dict) -> int:
        data = canonical(record)
        if self.cipher:
            data = canonical({"enc": self.cipher.seal(data)})
        if len(data) > ITEM_LIMIT:
            raise SyncError(f"{record['key']} is larger than 1 MB")
        self._atomic(self.file_for(record["key"]), data)
        return len(data)

    def usage(self) -> int:
        return sum(f.stat().st_size for f in self.items.glob("*.json"))


def device() -> tuple[str, str]:
    """(short id, readable name) of this PC: the licensing device fingerprint, so the same PC keeps its id."""
    from akeno.licensing import device as dev
    try:
        ident = dev.fingerprint()
    except Exception:                       # no fingerprint: a stable id of the host name
        ident = hashlib.sha256(platform.node().encode()).hexdigest()
    # a hash of the fingerprint: the folder never carries the value the license server knows the PC by
    return hashlib.sha256(b"akeno-sync|" + ident.encode()).hexdigest()[:16], dev.label() or platform.node() or "PC"


class SyncEngine:
    def __init__(self, data, state_path: Path | None = None, secrets=None, device_info: tuple[str, str] | None = None):
        self.data = data
        self.state_path = Path(state_path or sub("sync") / "state.json")
        if secrets is None:
            from akeno.licensing.storage import SecureStore
            secrets = SecureStore(self.state_path.parent)          # the passphrase: DPAPI on Windows
        self.secrets = secrets
        self.device_id, self.device_name = device_info or device()
        self.lock = threading.Lock()
        self.state = self._load()

    # -- state -------------------------------------------------------------------------------------------------------
    def _load(self) -> dict:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        state.setdefault("enabled", False)
        state.setdefault("folder", "")
        state.setdefault("categories", list(CATEGORIES))
        state.setdefault("quota_mb", DEFAULT_QUOTA_MB)
        state.setdefault("base", {})
        state.setdefault("conflicts", {})
        state.setdefault("others", {})
        state.setdefault("last_sync", None)
        state.setdefault("last_report", None)
        return state

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def passphrase(self) -> str | None:
        return self.secrets.load().get("passphrase") or None

    def configure(self, folder: str | None = None, enabled: bool | None = None, categories: list | None = None,
                  quota_mb: int | None = None, passphrase: str | None = None) -> None:
        """A new folder starts from scratch: nothing of the old one counts as synced."""
        with self.lock:
            if folder is not None and folder != self.state["folder"]:
                self.state.update(folder=folder, base={}, conflicts={}, others={}, last_sync=None)
            if passphrase is not None:
                self.secrets.save({"passphrase": passphrase} if passphrase else {})
            if enabled is not None:
                self.state["enabled"] = bool(enabled)
            if categories is not None:
                self.state["categories"] = [c for c in categories if c in CATEGORIES]
            if quota_mb is not None:
                self.state["quota_mb"] = max(1, min(1024, int(quota_mb)))
            self.save()

    def store(self, create: bool = True) -> FolderStore:
        if not self.state["folder"]:
            raise SyncError("no sync folder chosen")
        return FolderStore(Path(self.state["folder"]), self.passphrase(), create=create)

    # -- sync --------------------------------------------------------------------------------------------------------
    def _category(self, key: str) -> str:
        return {"settings": "settings", "desktop": "desktop"}.get(key) or {
            "game": "games", "dlss": "dlss", "booster": "booster", "bench": "benchmarks"}[key.split("/", 1)[0]]

    def sync(self) -> Report:
        with self.lock:
            if not self.state["enabled"]:
                raise SyncError("Cloud Sync is off")
            store = self.store()
            report = Report()
            remote, unreadable = store.read_all()
            report.skipped.extend(unreadable)
            wanted = set(self.state["categories"])
            local = {k: v for k, v in self.data.collect(wanted, self).items()}
            base = self.state["base"]
            quota = int(self.state["quota_mb"]) * 2 ** 20
            used = store.usage()
            keys = {k for k in set(local) | set(remote) | set(base) if self._category(k) in wanted}
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            def push(key: str, payload, deleted: bool = False) -> bool:
                nonlocal used
                record = {"key": key, "device": self.device_id, "updated": now, "deleted": deleted,
                          "hash": digest(payload) if not deleted else None, "data": None if deleted else payload}
                try:
                    if not deleted:
                        guard(payload)
                    size = len(canonical(record))
                    if used + size > quota:
                        report.skipped.append(f"{key}: quota of {self.state['quota_mb']} MB reached")
                        return False
                    used += store.write(record)
                except SyncError as exc:
                    report.skipped.append(f"{key}: {exc}")
                    return False
                return True

            for key in sorted(keys):
                if key in self.state["conflicts"]:
                    continue                              # waits for the user's decision
                b = base.get(key)
                mine = local.get(key)
                rec = remote.get(key)
                theirs = None if rec is None or rec.get("deleted") else rec.get("data")
                remote_deleted = rec is not None and rec.get("deleted")
                b_hash = digest(b) if b is not None else None
                m_hash = digest(mine) if mine is not None else None
                t_hash = digest(theirs) if theirs is not None else None
                if m_hash == t_hash:
                    if mine is not None:
                        base[key] = mine
                    else:
                        base.pop(key, None)
                    continue
                if t_hash == b_hash and not (remote_deleted and b is not None):
                    if mine is None and b is not None:        # deleted here
                        if push(key, None, deleted=True):
                            report.deleted += 1
                            base.pop(key, None)
                    elif mine is not None and push(key, mine):
                        report.pushed += 1
                        base[key] = mine
                    continue
                if m_hash == b_hash:                           # changed (or deleted) only on the other PC
                    if theirs is None:
                        if remote_deleted and mine is not None:
                            self.data.remove(key, self)
                            report.deleted += 1
                        base.pop(key, None)
                    elif self.data.apply(key, theirs, self):
                        report.pulled += 1
                        base[key] = theirs
                    continue
                if mine is None or theirs is None:            # deleted on one PC, changed on the other: keep it
                    keep = mine if mine is not None else theirs
                    if mine is None:
                        if self.data.apply(key, keep, self):
                            report.pulled += 1
                            base[key] = keep
                    elif push(key, keep):
                        report.pushed += 1
                        base[key] = keep
                    continue
                if isinstance(mine, dict) and isinstance(theirs, dict):
                    # never synced before (a PC joining): the defaults stand in for the common version
                    common = b if isinstance(b, dict) else self.data.baseline(key)
                    merged, fields = merge(common, mine, theirs)
                    if not fields:
                        if self.data.apply(key, merged, self) and push(key, merged):
                            report.merged += 1
                            base[key] = merged
                        continue
                else:
                    fields = ["value"]
                self.state["conflicts"][key] = {"fields": fields, "local": mine, "remote": theirs,
                                                "device": rec.get("device"), "updated": rec.get("updated")}
                report.conflicts += 1
            store.meta.setdefault("devices", {})[self.device_id] = {"name": self.device_name, "last_sync": now}
            store.write_meta()
            self.state.update(last_sync=now, last_report={"pushed": report.pushed, "pulled": report.pulled,
                                                          "merged": report.merged, "deleted": report.deleted,
                                                          "conflicts": len(self.state["conflicts"]),
                                                          "skipped": report.skipped[:20]})
            self.save()
            return report

    def resolve(self, key: str, keep: str) -> None:
        """keep = "local" or "remote" for the conflicting fields; the rest stays merged."""
        with self.lock:
            conflict = self.state["conflicts"].pop(key, None)
            if conflict is None:
                return
            mine, theirs = conflict["local"], conflict["remote"]
            if isinstance(mine, dict) and isinstance(theirs, dict):
                merged, fields = merge(self.state["base"].get(key) or self.data.baseline(key), mine, theirs)
                source = mine if keep == "local" else theirs
                for f in fields:
                    if f in source:
                        merged[f] = source[f]
                    else:
                        merged.pop(f, None)
            else:
                merged = mine if keep == "local" else theirs
            self.data.apply(key, merged, self)
            record = {"key": key, "device": self.device_id, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                                      time.gmtime()),
                      "deleted": False, "hash": digest(merged), "data": merged}
            guard(merged)
            self.store().write(record)
            self.state["base"][key] = merged
            self.save()

    def devices(self) -> dict:
        try:
            return dict(self.store(create=False).meta.get("devices", {}))
        except (SyncError, OSError, ValueError):
            return {}

    def usage(self) -> int:
        try:
            return self.store(create=False).usage()
        except (SyncError, OSError, ValueError):
            return 0


def describe(key: str, titles: dict | None = None) -> str:
    """A readable name of an item for the conflict list."""
    kind, _, rest = key.partition("/")
    titles = titles or {}
    name = titles.get(rest.split("/")[-1] if kind == "bench" else rest, rest)
    return {"settings": "Settings", "desktop": "Desktop"}.get(kind) or f"{kind}: {name}"

