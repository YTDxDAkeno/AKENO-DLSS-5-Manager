"""Qt UI contract tests with explicit API/native stand-ins; no Windows or Internet tunnel claim."""
from types import SimpleNamespace

from akeno.network.browser import RoomBrowser
from akeno.network.vpn import VPNClient
from akeno.ui.network_vpn import AutomaticVPN


class Parent:
    window=SimpleNamespace(license=object())
    modules=SimpleNamespace(enabled=lambda _:True)

    def work(self,fn,done):
        done(fn())

    def say(self,*args):
        self.message=args


def test_country_list_only_current_api_exits_and_clears_offline_country(qtbot,monkeypatch):
    parent=Parent()
    widget=AutomaticVPN(parent)
    qtbot.addWidget(widget)
    monkeypatch.setattr(widget,'client',lambda:SimpleNamespace(catalog=lambda:{
        'countries':['US'],'servers':[{'id':'ab'*16,'country':'US'}]}))
    widget.built=True
    widget.refresh()
    assert widget.country.count()==1 and widget.country.currentData()=='US'
    assert widget.connect_button.isEnabled()
    monkeypatch.setattr(widget,'client',lambda:SimpleNamespace(catalog=lambda:{'countries':[],'servers':[]}))
    widget.refresh()
    assert widget.country.count()==0 and not widget.connect_button.isEnabled()
    assert 'No authorized healthy' in widget.summary.text()


def test_country_selection_alone_cannot_start_native_vpn(qtbot,monkeypatch):
    widget=AutomaticVPN(Parent())
    qtbot.addWidget(widget)
    widget.built=True
    widget.country.addItem('Turkey','TR')
    import akeno.network.helper as helper
    monkeypatch.setattr(helper,'invoke',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not invoke')))
    widget.connect()
    assert not widget.connect_button.isEnabled()


def test_automatic_vpn_start_requires_explicit_confirmation(qtbot,monkeypatch):
    parent=Parent();widget=AutomaticVPN(parent);qtbot.addWidget(widget)
    widget.built=True;widget.servers=[{'country':'US'}];widget.country.addItem('United States','US')
    import akeno.ui.network_vpn as ui
    import akeno.network.helper as helper
    monkeypatch.setattr(ui,'confirm',lambda *a:False)
    monkeypatch.setattr(helper,'invoke',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not invoke')))
    widget.connect()


def test_catalog_refresh_does_not_provision_tunnel(qtbot,monkeypatch):
    parent=Parent();widget=AutomaticVPN(parent);qtbot.addWidget(widget)
    calls=[]
    monkeypatch.setattr(widget,'client',lambda:SimpleNamespace(catalog=lambda:calls.append('catalog') or
                        {'countries':[],'servers':[]}))
    widget.refresh()
    assert calls==['catalog']


def test_module_off_prevents_catalog_request(qtbot,monkeypatch):
    parent=Parent();parent.modules=SimpleNamespace(enabled=lambda _:False)
    widget=AutomaticVPN(parent);qtbot.addWidget(widget)
    monkeypatch.setattr(widget,'client',lambda:(_ for _ in ()).throw(AssertionError('must not call')))
    widget.refresh()
