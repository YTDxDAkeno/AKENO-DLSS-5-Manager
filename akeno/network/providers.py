"""VPN locations and the providers behind them.

A location is connectable only when a provider that AKENO has verified supplies a real server for it, today that
means one of the user's own imported WireGuard profiles. Every other location is listed with the real reason it is
not available. Providers that are not in ``VERIFIED`` stay disabled whatever a catalog or setting says, so a new
provider has to be reviewed (terms, logging, protocol, cost) and added in code before anything can connect to it.

Nothing here touches the network or Windows routes: it only describes what exists.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from akeno.i18n import msg

DATA = Path(__file__).resolve().parents[1] / 'data' / 'vpn_locations.json'

# Kinds a user sees next to each location.
AKENO, OWN, THIRD_PARTY = 'akeno', 'own', 'third_party'
# display names of the shipped target countries (the catalog's English names, translatable)
COUNTRY_NAMES = {'TR': msg('Turkey'), 'US': msg('United States'), 'DE': msg('Germany'), 'NL': msg('Netherlands'),
                 'GB': msg('United Kingdom'), 'FR': msg('France'), 'JP': msg('Japan'), 'CA': msg('Canada')}
KIND_NAMES = {AKENO: msg('AKENO server'), OWN: msg('Your own server'), THIRD_PARTY: msg('Third-party provider')}


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    kind: str
    protocols: tuple[str, ...]
    enabled: bool
    reason: str = ''                    # why it is disabled (English source text)


@dataclass(frozen=True)
class Location:
    id: str
    country: str | None                 # ISO 3166-1 alpha-2, None when unknown (an own profile without a tag)
    name: str
    provider: str
    kind: str
    protocol: str
    available: bool
    reason: str = ''                    # why it cannot be connected (English source text); empty when available
    profile_id: str | None = None
    favourite: bool = False


# The only providers AKENO can connect through. Adding one requires a review recorded in
# docs/AKENO_VPN_PROVIDERS.md; a provider id that is not here is treated as disabled.
VERIFIED = {
    'own-wireguard': Provider('own-wireguard', 'WireGuard (own profile)', OWN, ('wireguard',), True),
    'akeno': Provider('akeno', 'AKENO', AKENO, ('wireguard',), True),
    'vpngate': Provider('vpngate', 'VPN Gate', THIRD_PARTY, ('openvpn',), False, msg(
        'VPN Gate is used by the Free VPN above: choose a country there and AKENO picks a server automatically.')),
}


def provider(ident: str) -> Provider:
    return VERIFIED.get(ident) or Provider(ident, ident, THIRD_PARTY, (), False,
                                           msg('Disabled: this provider has not been reviewed by AKENO.'))


def catalog(path: Path = DATA) -> dict:
    """The shipped location catalog: target countries and AKENO servers (none run yet)."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'countries': {}, 'akeno_servers': []}
    return {'countries': dict(data.get('countries') or {}), 'akeno_servers': list(data.get('akeno_servers') or [])}


def locations(profiles: list[dict], prefs: 'Preferences | None' = None, path: Path = DATA) -> list[Location]:
    """Every location in display order: connectable ones first, then by favourite and name.

    ``profiles`` is ``ProfileStore.metadata()`` (names and ids only, never keys)."""
    prefs = prefs or Preferences(None)
    data = catalog(path)
    names = data['countries']
    out: list[Location] = []
    own = provider('own-wireguard')
    for p in profiles:
        country = prefs.country_of(p['id'])
        out.append(Location('own:' + p['id'], country, p['name'], own.id, OWN, 'WireGuard', True,
                            profile_id=p['id']))
    akeno = provider('akeno')
    served = {}
    for server in data['akeno_servers']:
        # A shipped entry alone never makes a location connectable: it needs a verified, enabled provider and a
        # signed profile delivered by the AKENO server, which does not exist yet.
        served.setdefault(server.get('country'), server)
    for code, name in names.items():
        if code in served and akeno.enabled and served[code].get('profile'):
            out.append(Location('akeno:' + code, code, name, akeno.id, AKENO, 'WireGuard', True))
        else:
            out.append(Location('akeno:' + code, code, name, akeno.id, AKENO, 'WireGuard', False,
                                msg('No AKENO VPN server runs in this country yet.')))
    gate = provider('vpngate')
    out.append(Location('vpngate', None, 'VPN Gate', gate.id, THIRD_PARTY, 'OpenVPN', gate.enabled, gate.reason))
    out = [Location(**{**loc.__dict__, 'favourite': prefs.is_favourite(loc.id)}) for loc in out]
    return sorted(out, key=lambda loc: (not loc.available, not loc.favourite, loc.kind != OWN, loc.name.lower()))


def search(items: list[Location], text: str, country_name=lambda code: code or '') -> list[Location]:
    needle = (text or '').strip().lower()
    if not needle:
        return items
    return [loc for loc in items if needle in loc.name.lower() or needle in (loc.country or '').lower()
            or needle in country_name(loc.country).lower()]


class Preferences:
    """Favourites, the country a user tagged an own profile with, and a short connection history.

    The history keeps only when, which location and how long: no addresses, traffic or visited sites. It is a plain
    JSON file because it holds nothing secret; at most ``HISTORY`` entries are kept."""
    HISTORY = 20

    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self.data = {'favourites': [], 'countries': {}, 'history': []}
        if self.path and self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding='utf-8'))
                self.data = {'favourites': [str(x) for x in raw.get('favourites', [])][:200],
                             'countries': {str(k): str(v)[:2].upper() for k, v in raw.get('countries', {}).items()},
                             'history': [h for h in raw.get('history', []) if isinstance(h, dict)][-self.HISTORY:]}
            except (OSError, ValueError, AttributeError):
                pass

    def _save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.data, indent=1), encoding='utf-8')
            tmp.replace(self.path)

    def is_favourite(self, ident: str) -> bool:
        return ident in self.data['favourites']

    def toggle_favourite(self, ident: str) -> bool:
        fav = self.data['favourites']
        if ident in fav:
            fav.remove(ident)
        else:
            fav.append(ident)
        self._save()
        return ident in fav

    def country_of(self, profile_id: str) -> str | None:
        return self.data['countries'].get(profile_id)

    def set_country(self, profile_id: str, code: str | None):
        if code:
            if len(code) != 2 or not code.isalpha():
                raise ValueError('Invalid country code.')
            self.data['countries'][profile_id] = code.upper()
        else:
            self.data['countries'].pop(profile_id, None)
        self._save()

    def record(self, location: str, started: float, ended: float | None = None):
        entry = {'location': str(location)[:64], 'started': int(started),
                 'ended': int(ended) if ended else None}
        history = self.data['history']
        if history and history[-1].get('started') == entry['started']:
            history[-1] = entry
        else:
            history.append(entry)
        self.data['history'] = history[-self.HISTORY:]
        self._save()

    def history(self) -> list[dict]:
        return list(reversed(self.data['history']))

    def clear_history(self):
        self.data['history'] = []
        self._save()
