"""Entitlements: what an account may use.

* ``akeno-suite`` (AKENO Full) is the account's ``License`` row, exactly as before; it includes every module.
* Every other entitlement is a ``FeatureGrant`` row for one module (AKENO VPN, AKENO LAN Play, later others).
  A feature grant never stands for the suite: an old app that only knows ``akeno-suite`` never sees one.

The ids are stable: they are signed into access leases and certificates and checked by the Windows app.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from akeno_server.clock import utcnow

SUITE = "akeno-suite"
VPN = "akeno-vpn"
LAN_PLAY = "akeno-lan-play"


@dataclass(frozen=True)
class Entitlement:
    id: str
    name: str
    modules: tuple[str, ...]
    offered: bool          # administrators can create codes for it (planned modules are listed, not offered)


CATALOG: dict[str, Entitlement] = {e.id: e for e in (
    Entitlement(SUITE, "AKENO Full", ("*",), True),
    Entitlement(VPN, "AKENO VPN", ("vpn",), True),
    Entitlement(LAN_PLAY, "AKENO LAN Play", ("lan_play",), True),
    # planned single modules: the app already maps them, the server can grant them once they are offered
    Entitlement("akeno-desktop-studio", "Desktop Studio", ("desktop",), False),
    Entitlement("akeno-dlss", "DLSS Manager", ("dlss",), False),
    Entitlement("akeno-performance", "Performance Tools", ("performance",), False),
    Entitlement("akeno-gaming-tools", "Gaming Tools", ("gaming",), False),
)}
FEATURES = tuple(k for k in CATALOG if k != SUITE)


def known(ent: str) -> bool:
    return ent in CATALOG


def offered() -> list[Entitlement]:
    return [e for e in CATALOG.values() if e.offered]


def name(ent: str) -> str:
    return CATALOG[ent].name if ent in CATALOG else ent


def grant_of(db: Session, user_id: uuid.UUID, ent: str, lock: bool = False):
    from akeno_server.models import FeatureGrant
    q = select(FeatureGrant).where(FeatureGrant.user_id == user_id, FeatureGrant.entitlement == ent)
    if lock:
        q = q.with_for_update()
    return db.scalars(q).first()


def has(db: Session, user_id: uuid.UUID, ent: str, now: datetime | None = None) -> bool:
    """The account may use ``ent`` right now: a running suite license, or a running grant of exactly ``ent``."""
    from akeno_server.licensing import service as lic
    now = now or utcnow()
    suite = lic.license_of(db, user_id)
    if suite is not None and suite.running(now):
        return True
    if ent == SUITE:
        return False
    grant = grant_of(db, user_id, ent)
    return grant is not None and grant.running(now)
