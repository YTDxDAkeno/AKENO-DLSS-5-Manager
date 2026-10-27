"""AKENO Network: authenticated room controls, own VPN profiles, explicit elevation and honest status."""
import sys
from pathlib import Path
from PySide6.QtWidgets import (QComboBox,QFileDialog,QHBoxLayout,QInputDialog,QLineEdit,QPlainTextEdit,QVBoxLayout,QWidget)
from akeno.i18n import t
from akeno.paths import sub
from akeno.licensing.storage import SecureStore
from akeno.network.client import LanClient
from akeno.network.profiles import ProfileStore
from akeno.ui.widgets import Card,FlowLayout,PageHeader,button,confirm,info,label


class AkenoNetworkPage(QWidget):
    def __init__(self,ctl,window):
        super().__init__();self.ctl,self.window=ctl,window
        lay=QVBoxLayout(self);lay.setContentsMargins(0,0,0,0)
        lay.addWidget(PageHeader(t('AKENO Network')))
        self.note=label(t('Room controls require the opt-in AKENO LAN API. Peer transport integration is still experimental. '
                          'VPN profiles use the official WireGuard Windows manager and an explicit UAC helper.'),'dim',wrap=True)
        lay.addWidget(self.note)
        lan=Card(t('LAN Play'),'wifi');lay.addWidget(lan)
        self.room_name=QLineEdit();self.room_name.setPlaceholderText(t('Room name'))
        self.invite=QLineEdit();self.invite.setPlaceholderText(t('Invitation code'))
        self.password=QLineEdit();self.password.setEchoMode(QLineEdit.EchoMode.Password);self.password.setPlaceholderText(t('Optional room password'))
        for w in (self.room_name,self.invite,self.password):lan.body.addWidget(w)
        actions=FlowLayout(spacing=8)
        for text,action in (('Create room',self.create),('Join room',self.join),('Refresh members',self.members),('Leave room',self.leave)):
            b=button(t(text),'wifi',on_click=action);b.setEnabled(sys.platform=='win32');actions.addWidget(b)
        lan.body.addLayout(actions)
        self.room_status=label(t('No room selected.'),'muted',wrap=True);lan.body.addWidget(self.room_status)
        self.peer_list=QPlainTextEdit();self.peer_list.setReadOnly(True);self.peer_list.setMaximumHeight(130);lan.body.addWidget(self.peer_list)
        vpn=Card(t('Own WireGuard profiles'),'shield');lay.addWidget(vpn)
        self.profiles=QComboBox();vpn.body.addWidget(self.profiles)
        actions=FlowLayout(spacing=8)
        for text,action in (('Import WireGuard profile',self.import_profile),('Rename profile',self.rename_profile),('Remove profile',self.remove_profile)):
            b=button(t(text),'file',on_click=action);b.setEnabled(sys.platform=='win32');actions.addWidget(b)
        vpn.body.addLayout(actions)
        actions=FlowLayout(spacing=8)
        for text,action in (('Connect VPN','connect'),('Disconnect / recover','disconnect'),('Check tunnel status','status'),('Check exit IP','exit_ip')):
            b=button(t(text),'wifi',on_click=lambda _=False,a=action:self.vpn(a))
            b.setEnabled(sys.platform=='win32' and bool(getattr(sys,'frozen',False)));actions.addWidget(b)
        vpn.body.addLayout(actions)
        self.vpn_status=label(t('Disconnected. Imported profiles do not prove connectivity.'),'muted',wrap=True);vpn.body.addWidget(self.vpn_status)
        vpn.body.addWidget(label(t('Full tunnels require IPv4, IPv6 and DNS. The official manager restores its tunnel routes '
                                  'and DNS on removal; real Windows restoration and leak checks remain required.'),'muted',wrap=True))
        gate=Card(t('VPN Gate (experimental catalog)'),'globe');lay.addWidget(gate)
        gate.body.addWidget(label(t('VPN Gate connections are disabled pending terms, privacy and OpenVPN transport review. '
                                   'Catalog entries do not guarantee availability or no-logs protection.'),'dim',wrap=True))
        gate.body.addWidget(button(t('Fetch official server catalog'),'refresh',on_click=self.catalog))
        self.countries=QComboBox();gate.body.addWidget(self.countries)
        self.work_status=label('','muted',wrap=True);lay.addWidget(self.work_status);lay.addStretch(1)
        self.refresh_profiles()

    def store(self):return ProfileStore(sub('network')/'profiles.sqlite3')
    def lan(self):return LanClient(self.window.license,SecureStore(sub('network')/'lan-identity'))
    def room(self):
        room=SecureStore(sub('network')/'lan-identity').load().get('room_id')
        if not room:raise ValueError(t('No room selected.'))
        return room

    def work(self,fn,done):
        self.work_status.setText(t('Working…'))
        def complete(value):self.work_status.setText('');done(value)
        def failed(exc):self.work_status.setText(str(exc))
        if not self.ctl.run('akeno-network',fn,complete,failed):self.work_status.setText(t('A network operation is already running.'))

    def create(self):
        name,password=self.room_name.text(),self.password.text()
        self.work(lambda:self.lan().create(name,password),self.room_changed)
    def join(self):
        invite,password=self.invite.text(),self.password.text()
        self.work(lambda:self.lan().join(invite,password),self.room_changed)
    def room_changed(self,result):
        self.password.clear();self.room_status.setText(t('Room registered; tunnel connectivity is not established.'))
        if result.get('invite'):self.invite.setText(result['invite'])
    def members(self):
        def show(result):
            self.peer_list.setPlainText('\n'.join(p['virtual_ip']+'  '+p['status'] for p in result['members']))
            self.room_status.setText(t('Peer credentials verified. Transport is not connected by this control.'))
        self.work(lambda:self.lan().heartbeat(self.room()),show)
    def leave(self):self.work(lambda:self.lan().leave(self.room()),lambda _:self.room_status.setText(t('No room selected.')))

    def refresh_profiles(self):
        self.profiles.clear()
        if sys.platform!='win32':return
        try:
            for p in self.store().metadata():self.profiles.addItem(p['name'],p['id'])
        except Exception as exc:self.vpn_status.setText(str(exc))
    def import_profile(self):
        path,_=QFileDialog.getOpenFileName(self,t('Import WireGuard profile'),'','WireGuard (*.conf)')
        if not path:return
        try:
            with open(path,'rb') as f:raw=f.read(65537)
            if len(raw)>65536:raise ValueError(t('Profile is too large.'))
            self.store().add(Path(path).stem,raw.decode('utf-8-sig'));self.refresh_profiles()
        except Exception as exc:info(self,t('AKENO Network'),str(exc),error=True)
    def rename_profile(self):
        ident=self.profiles.currentData()
        if not ident:return
        name,ok=QInputDialog.getText(self,t('Rename profile'),t('Profile name'))
        if ok:
            try:self.store().rename(ident,name);self.refresh_profiles()
            except Exception as exc:info(self,t('AKENO Network'),str(exc),error=True)
    def remove_profile(self):
        ident=self.profiles.currentData()
        if not ident:return
        state=SecureStore(sub('network')/'tunnel').load()
        if state.get('profile_id')==ident and state.get('phase')!='disconnected':
            info(self,t('AKENO Network'),t('Disconnect or recover this profile before removing it.'));return
        if confirm(self,t('Remove profile'),t('Remove the encrypted VPN profile?')):
            try:self.store().remove(ident);self.refresh_profiles()
            except Exception as exc:info(self,t('AKENO Network'),str(exc),error=True)
    def vpn(self,action):
        ident=self.profiles.currentData()
        if action=='connect' and (not ident or not confirm(self,t('Connect VPN'),t('Allow the official WireGuard service '
                'to apply this profile and change its routes/DNS? The connection persists until you disconnect it.'))):return
        from akeno.network.helper import invoke
        def show(result):
            self.vpn_status.setText(str(result['state'])+((' · '+result['exit_ip']) if result.get('exit_ip') else ''))
        self.work(lambda:invoke(action,ident),show)
    def catalog(self):
        from akeno.network.vpngate import fetch_catalog,countries
        def show(servers):
            self.countries.clear()
            for code,name in countries(servers):self.countries.addItem(name+' ('+code+')',code)
            self.work_status.setText(t('Catalog loaded. Public VPN connections remain disabled.'))
        self.work(fetch_catalog,show)
