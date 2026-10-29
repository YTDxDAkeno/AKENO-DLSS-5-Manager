"""VPN locations: only reviewed providers with a real server are connectable; everything else says why not."""
import json

from akeno.network import providers as P


def test_no_fake_locations_only_own_profiles_connect(tmp_path):
    items = P.locations([{'id': 'a' * 32, 'name': 'Home server'}], P.Preferences(tmp_path / 'p.json'))
    available = [loc for loc in items if loc.available]
    assert [loc.kind for loc in available] == [P.OWN] and available[0].profile_id == 'a' * 32
    for code in ('TR', 'US', 'DE', 'NL', 'GB', 'FR', 'JP', 'CA'):
        loc = next(loc for loc in items if loc.country == code and loc.kind == P.AKENO)
        assert not loc.available and 'No AKENO VPN server' in loc.reason
    gate = next(loc for loc in items if loc.provider == 'vpngate')
    assert not gate.available and 'Free VPN' in gate.reason


def test_shipped_catalog_has_no_akeno_servers():
    # Changing this needs a running server and the right to use it (docs/AKENO_VPN_PROVIDERS.md).
    assert P.catalog()['akeno_servers'] == []


def test_catalog_entry_without_signed_profile_never_becomes_connectable(tmp_path):
    path = tmp_path / 'cat.json'
    path.write_text(json.dumps({'countries': {'DE': 'Germany'}, 'akeno_servers': [{'country': 'DE'}]}))
    assert not any(loc.available for loc in P.locations([], None, path))


def test_unknown_provider_is_disabled():
    assert not P.provider('cheap-free-vpn').enabled and 'not been reviewed' in P.provider('x').reason


def test_preferences_favourites_country_tags_and_history_without_activity(tmp_path):
    prefs = P.Preferences(tmp_path / 'p.json')
    assert prefs.toggle_favourite('akeno:DE') and prefs.is_favourite('akeno:DE')
    prefs.set_country('b' * 32, 'nl')
    for i in range(30):
        prefs.record('Home server', 1000 + i, 1060 + i)
    again = P.Preferences(tmp_path / 'p.json')
    assert again.country_of('b' * 32) == 'NL' and again.is_favourite('akeno:DE')
    assert len(again.history()) == P.Preferences.HISTORY
    assert set(again.history()[0]) == {'location', 'started', 'ended'}
    items = P.locations([{'id': 'b' * 32, 'name': 'NL box'}], again)
    assert items[0].country == 'NL'
    fav = next(loc for loc in items if loc.id == 'akeno:DE')
    assert fav.favourite
    assert [loc.id for loc in P.search(items, 'nl box')] == ['own:' + 'b' * 32]


def test_search_by_translated_country_name(tmp_path):
    items = P.locations([], None)
    hits = P.search(items, 'türkei', lambda code: {'TR': 'Türkei'}.get(code, ''))
    assert [loc.country for loc in hits] == ['TR']
