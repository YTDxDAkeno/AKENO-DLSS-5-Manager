"""Automatic country VPN controls. The list contains only currently admitted control-plane exits."""
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout

from akeno.i18n import t
from akeno.licensing.storage import SecureStore
from akeno.network.vpn import VPNClient
from akeno.paths import sub
from akeno.ui.widgets import Card, button, confirm, label


class AutomaticVPN(Card):
    def __init__(self,page):
        super().__init__(t('AKENO VPN — choose a country'),'globe')
        self.page,self.servers=page,[]
        self.body.addWidget(label(t('Choose an available AKENO exit. Keys and tunnel configuration are created '
                                    'automatically for this device. A country appears only while an authorized, '
                                    'healthy and independently verified exit is available.'),'dim',wrap=True))
        self.country=QComboBox()
        self.country.setPlaceholderText(t('Refresh available countries'))
        self.body.addWidget(self.country)
        row=QHBoxLayout()
        self.refresh_button=button(t('Refresh available countries'),'refresh',on_click=self.refresh)
        self.connect_button=button(t('Connect to country'),'play','primary',on_click=self.connect)
        self.stop_button=button(t('Disconnect AKENO VPN'),'stop',on_click=self.disconnect)
        self.recover_button=button(t('Recover AKENO VPN'),'restore','ghost',on_click=self.recover)
        for b in (self.refresh_button,self.connect_button,self.stop_button,self.recover_button):
            row.addWidget(b)
        self.body.addLayout(row)
        self.summary=label(t('No AKENO exits have been loaded. Your own profiles remain available below.'),'muted',wrap=True)
        self.body.addWidget(self.summary)
        self.state=label(t('Disconnected'),'dim',wrap=True)
        self.body.addWidget(self.state)
        self.built=sys.platform=='win32' and bool(getattr(sys,'frozen',False))
        self.connect_button.setEnabled(False)
        self.stop_button.setEnabled(self.built)
        self.recover_button.setEnabled(self.built)
        self.refresh_button.setEnabled(sys.platform=='win32')
        self.timer=QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        if self.built:
            self.timer.start()   # Local DPAPI status only; never connects or contacts an API.

    def client(self):
        return VPNClient(self.page.window.license,SecureStore(sub('network')/'vpn-identity'))

    def refresh(self):
        if not self.page.modules.enabled('vpn'):
            return
        if not getattr(self.page.window,'license',None):
            self.page.say(t('Sign in before using AKENO Network.'),'warn')
            return
        def show(result):
            self.servers=result['servers']
            self.country.clear()
            from akeno.ui.pages.akeno_network import country_name
            for code in result['countries']:
                self.country.addItem(country_name(code),code)
            self.connect_button.setEnabled(self.built and bool(result['countries']))
            self.summary.setText(t('{n} authorized healthy exits.').format(n=len(self.servers)) if self.servers else
                                 t('No authorized healthy country exits are currently available.'))
        self.page.work(lambda:self.client().catalog(),show)

    def connect(self):
        code=self.country.currentData()
        if not self.built or not code or not any(s['country']==code for s in self.servers):
            return
        if not confirm(self,t('Connect VPN'),t('Create a device-bound VPN tunnel for this country? Windows asks '
                'for permission. AKENO verifies the handshake and observed IPv4/IPv6 exit before showing Connected. '
                'LAN Play and VPN cannot run together until their routing has been verified.')):
            return
        from akeno.network.helper import invoke
        self.page.work(lambda:invoke('vpn_session',code),lambda _:self.poll())

    def disconnect(self):
        if not self.built:
            return
        from akeno.network.helper import stop_vpn
        stop_vpn()
        self.state.setText(t('Disconnecting…'))

    def recover(self):
        if not self.built:
            return
        from akeno.network.helper import invoke
        self.page.work(lambda:invoke('vpn_recover'),lambda _:self.poll())

    def poll(self):
        if not self.built:
            return
        from akeno.network.helper import vpn_status
        try:
            result=vpn_status()
        except Exception:
            self.state.setText(t('Needs recovery'))
            return
        names={'starting':t('Starting…'),'provisioning':t('Requesting VPN permission…'),
               'awaiting_exit':t('Waiting for the exit server…'),'awaiting_handshake':t('Waiting for the server'),
               'verifying':t('Verifying exit and routing…'),'connected':t('Connected'),
               'disconnected':t('Disconnected'),'stopped':t('Disconnected'),'failed':t('Failed'),
               'recovery_required':t('Needs recovery'),'verification_required':t('VPN verification is incomplete.')}
        state=result.get('state','disconnected')
        text=names.get(state,t('Starting…'))
        if state=='connected':
            # No extrapolation from country selection or lease allocation; these come from the native session.
            text+=' · '+str(result.get('country',''))+' · '+str(result.get('exit_ipv4',''))
        self.state.setText(text)
        active=state not in ('disconnected','stopped','failed','verification_required')
        self.connect_button.setEnabled(self.built and not active and bool(self.servers))
        if result.get('error'):
            self.state.setToolTip(str(result['error'])[:300])
