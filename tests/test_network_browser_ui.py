"""Public room browser widget with an API stand-in; no server or relay claim."""
from types import SimpleNamespace

from akeno.ui.network_browser import PublicRooms


class Page:
    modules = SimpleNamespace(enabled=lambda _: True)

    def __init__(self):
        self.messages, self.room = [], None

    def work(self, fn, done):
        done(fn())

    def say(self, text, kind='info'):
        self.messages.append((text, kind))

    def current_room(self):
        return self.room


ROOM = {'room_id': 'ab' * 16, 'name': 'Friday', 'game_id': 'openttd', 'region': 'eu', 'players': 2, 'capacity': 16,
        'status': 'open', 'password_required': True}


def browser(qtbot, monkeypatch, calls):
    page = Page()
    widget = PublicRooms(page)
    qtbot.addWidget(widget)
    api = SimpleNamespace(browse=lambda **kw: calls.append(('browse', kw)) or
                          {'rooms': [ROOM], 'offset': 0, 'next_offset': None},
                          create=lambda *a: calls.append(('create', a)) or {'room_id': ROOM['room_id']},
                          publish=lambda *a, **kw: calls.append(('publish', a, kw)))
    monkeypatch.setattr(widget, 'client', lambda: api)
    return page, widget


def test_default_filters_list_all_games_and_regions_and_show_names(qtbot, monkeypatch):
    calls = []
    page, widget = browser(qtbot, monkeypatch, calls)
    widget.refresh(0)
    query = calls[0][1]
    assert query['game_id'] == '' and query['region'] == '' and query['minimum_players'] == 0
    row = [widget.table.item(0, col).text() for col in range(6)]
    assert row == ['Friday', 'OpenTTD', 'Europe', '2/16', 'open', 'Yes']
    assert 'No player public IP addresses' in widget.summary.text()


def test_public_listing_needs_a_concrete_game_and_region(qtbot, monkeypatch):
    calls = []
    page, widget = browser(qtbot, monkeypatch, calls)
    import akeno.ui.network_browser as ui
    monkeypatch.setattr(ui, 'confirm', lambda *a: True)
    page.room_name = SimpleNamespace(text=lambda: 'Friday')
    page.create_password = SimpleNamespace(text=lambda: '')
    page.room_changed = lambda result: calls.append(('changed', result))
    widget.create()
    assert not any(c[0] == 'create' for c in calls) and 'game and a region' in page.messages[-1][0]
    widget.game.setCurrentIndex(widget.game.findData('openttd'))
    widget.region.setCurrentIndex(widget.region.findData('eu'))
    widget.create()
    assert ('create', ('Friday', 'openttd', 'eu', '')) in calls
