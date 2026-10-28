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
    # The eight planned countries are listed before any request, none of them connectable.
    assert set(widget.tiles)=={'TR','US','DE','NL','GB','FR','JP','CA'}
    assert not any(tile.available for tile in widget.tiles.values()) and not widget.connect_button.isEnabled()
    monkeypatch.setattr(widget,'client',lambda:SimpleNamespace(catalog=lambda:{
        'countries':['US'],'servers':[{'id':'ab'*16,'country':'US'}]}))
    widget.built=True
    widget.refresh()
    assert widget.selected=='US' and widget.tiles['US'].available and widget.tiles['US'].isChecked()
    assert not widget.tiles['TR'].available and 'No verified AKENO exit' in widget.tiles['TR'].line
    assert widget.connect_button.isEnabled()
    widget.select('TR')
    assert not widget.connect_button.isEnabled() and 'No verified AKENO exit' in widget.summary.text()
    monkeypatch.setattr(widget,'client',lambda:SimpleNamespace(catalog=lambda:{'countries':[],'servers':[]}))
    widget.refresh()
    assert widget.selected is None and not widget.connect_button.isEnabled()
    assert not widget.tiles['US'].available and 'No authorised healthy' in widget.summary.text()


def test_catalog_country_outside_the_plan_gets_its_own_tile(qtbot,monkeypatch):
    widget=AutomaticVPN(Parent());qtbot.addWidget(widget)
    monkeypatch.setattr(widget,'client',lambda:SimpleNamespace(catalog=lambda:{
        'countries':['SE'],'servers':[{'id':'cd'*16,'country':'SE'}]}))
    widget.refresh()
    assert widget.tiles['SE'].available and widget.selected=='SE'


def test_country_selection_alone_cannot_start_native_vpn(qtbot,monkeypatch):
    widget=AutomaticVPN(Parent())
    qtbot.addWidget(widget)
    widget.built=True
    widget.select('TR')
    import akeno.network.helper as helper
    monkeypatch.setattr(helper,'invoke',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not invoke')))
    widget.connect()
    assert not widget.connect_button.isEnabled()


def test_automatic_vpn_start_requires_explicit_confirmation(qtbot,monkeypatch):
    parent=Parent();widget=AutomaticVPN(parent);qtbot.addWidget(widget)
    widget.built=True;widget.servers=[{'country':'US'}];widget.select('US')
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


def test_connected_needs_verified_routes_dns_and_observed_exit(qtbot):
    widget=AutomaticVPN(Parent());qtbot.addWidget(widget)
    now=1_000_000.0
    base={'state':'connected','updated':now,'country':'DE','exit_ipv4':'203.0.113.9','exit_ipv6':'2001:db8::9',
          'routing_verified':True,'dns_verified':True,'connected_since':now-75}
    widget.show_status(base,now=now)
    assert widget.state_title.text()=='Connected' and widget.fact_ip.value.text()=='203.0.113.9'
    assert widget.fact_time.value.text()=='1:15' and widget.fact_protection.value.text()=='Verified'
    assert widget.fact_latency.value.text()=='—'                         # never invented
    for missing in ('dns_verified','routing_verified','exit_ipv4'):
        widget.show_status({k:v for k,v in base.items() if k!=missing},now=now)
        assert widget.state_title.text()!='Connected' and widget.fact_ip.value.text()=='—'


def test_stale_status_of_a_vanished_helper_is_never_connected(qtbot):
    widget=AutomaticVPN(Parent());qtbot.addWidget(widget)
    status={'state':'connected','updated':100.0,'exit_ipv4':'203.0.113.9','routing_verified':True,
            'dns_verified':True}
    widget.show_status(status,now=100.0+31)
    assert widget.state_title.text()=='Needs recovery' and 'Recover' in widget.state.text()


def test_session_errors_are_translated_sentences_not_codes(qtbot):
    from akeno.ui.network_vpn import ERRORS
    widget=AutomaticVPN(Parent());qtbot.addWidget(widget)
    for code,sentence in ERRORS.items():
        widget.show_status({'state':'failed','error':code,'updated':1.0},now=2.0)
        assert widget.state.text()==sentence and code not in widget.state.text()


def test_lan_allow_game_reuses_nested_library_executable_and_reviewed_profile(qtbot,monkeypatch,tmp_path):
    from PySide6.QtWidgets import QWidget
    import akeno.ui.pages.akeno_network as ui
    import akeno.network.helper as helper
    exe=tmp_path/'Factorio'/'bin'/'x64'/'factorio.exe'
    exe.parent.mkdir(parents=True);exe.write_bytes(b'fixture only')
    game=SimpleNamespace(name='Factorio (Steam)',install_dir=str(tmp_path/'Factorio'),exe_path=str(exe))
    widget=QWidget();qtbot.addWidget(widget)
    widget.session={'state':'active'}
    widget.ctl=SimpleNamespace(s=SimpleNamespace(library=SimpleNamespace(games=lambda:[game])))
    widget.room=lambda:'ab'*16
    widget.say=lambda *args:None
    widget.show_room=lambda:None
    monkeypatch.setattr(ui.QInputDialog,'getItem',lambda *args:('Factorio',True))
    monkeypatch.setattr(ui.QFileDialog,'getOpenFileName',lambda *args:(_ for _ in ()).throw(
        AssertionError('Installed game must not require a manual executable picker')))
    calls=[]
    monkeypatch.setattr(helper,'allow_game',lambda *args:calls.append(args))
    ui.AkenoNetworkPage.allow_game(widget)
    assert calls==[('ab'*16,str(exe),'factorio')] and widget.game_allowed is True
