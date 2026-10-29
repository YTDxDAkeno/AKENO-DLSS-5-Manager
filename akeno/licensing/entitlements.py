"""Which AKENO modules an entitlement unlocks, and which module each page belongs to.

The server signs entitlement ids (``akeno-suite``, ``akeno-vpn`` …) into the access lease and certificate; this
table turns them into modules. AKENO Full (``akeno-suite``) unlocks every module, including modules added later.
A page, a background task or a helper action that belongs to a module runs only when ``allows(module)``.
"""

from __future__ import annotations

SUITE = "akeno-suite"
VPN = "akeno-vpn"
LAN_PLAY = "akeno-lan-play"
ALL = "*"

NAMES = {SUITE: "AKENO Full", VPN: "AKENO VPN", LAN_PLAY: "AKENO LAN Play",
         "akeno-desktop-studio": "Desktop Studio", "akeno-dlss": "DLSS Manager",
         "akeno-performance": "Performance Tools", "akeno-gaming-tools": "Gaming Tools"}

# entitlement -> modules (an id this build does not know unlocks nothing)
MODULES = {SUITE: (ALL,), VPN: ("vpn",), LAN_PLAY: ("lan_play",), "akeno-desktop-studio": ("desktop",),
           "akeno-dlss": ("dlss",), "akeno-performance": ("performance",), "akeno-gaming-tools": ("gaming",)}

# page -> module. Pages that are not listed (account, license, updates, settings) belong to every license.
PAGES = {
    "home": "dashboard", "library": "gaming", "recent": "gaming", "booster": "gaming", "benchmark": "gaming",
    "fivem": "dlss", "downloads": "dlss", "backups": "dlss", "hardware": "dlss", "advanced": "dlss",
    "dashboard": "dlss", "profiles": "dlss", "install": "dlss", "installed": "dlss",
    "doctor": "performance", "cleaner": "performance", "network": "performance", "security": "performance",
    "wallpapers": "desktop", "dock": "desktop", "widgets": "desktop", "themes": "desktop",
    "community": "online", "sync": "online",
    "akeno_network": ("vpn", "lan_play"),       # open with either; each tab checks its own module
}
ALWAYS = ("account", "license", "updates", "settings")


def modules(ents) -> set[str]:
    out: set[str] = set()
    for ent in ents:
        out.update(MODULES.get(ent, ()))
    return out


def allows(ents, module: str) -> bool:
    found = modules(ents)
    return ALL in found or module in found


def page_allowed(ents, page: str) -> bool:
    if page in ALWAYS:
        return bool(ents)
    need = PAGES.get(page)
    if need is None:
        return allows(ents, ALL)              # a page this table does not know needs AKENO Full
    if isinstance(need, tuple):
        return any(allows(ents, m) for m in need)
    return allows(ents, need)


def name(ent: str) -> str:
    return NAMES.get(ent, ent)
