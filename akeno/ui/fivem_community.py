"""Operator policy inputs for the normal FiveM installation workflow."""
from dataclasses import replace
from PySide6.QtWidgets import QWidget,QVBoxLayout,QFormLayout,QLineEdit,QCheckBox,QComboBox,QFileDialog
from akeno.ui.widgets import button,label
from akeno.i18n import t
from akeno.fivem.server_config import inspect_config


class FiveMCommunitySettings(QWidget):
    def __init__(self,ctl,game,parent=None):
        super().__init__(parent);self.ctl=ctl;self.s=ctl.s;self.game=game;self.panel=parent
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        policy=self.s.fivem.policy(game.id);saved=self.s.db.get_setting('fivem_community:'+game.id,{})
        self.server=QLineEdit(policy.server_id);self.reference=QLineEdit(policy.permission_reference)
        form.addRow(t('Test server'),self.server);form.addRow(t('Operator permission reference'),self.reference)
        self.pure=QComboBox()
        for name,value in [('Unknown',None),('Pure 0',0),('Pure 1',1),('Pure 2',2)]:self.pure.addItem(t(name),value)
        self.pure.setCurrentIndex(max(0,self.pure.findData(policy.pure_level)));form.addRow(t('Effective Pure Mode'),self.pure)
        self.allowed=QCheckBox(t('The operator permits this exact graphics stack'));self.allowed.setChecked(policy.graphics_plugins_allowed is True);layout.addWidget(self.allowed)
        self.client=QCheckBox(t('The installed client permits the selected graphics plugin mechanism'));self.client.setChecked(saved.get('client_plugins_permitted') is True);layout.addWidget(self.client)
        self.cfg=QLineEdit(saved.get('server_config',''));form.addRow(t('Server configuration'),self.cfg)
        layout.addWidget(button(t('Read server configuration'),on_click=self.choose_config))
        layout.addWidget(button(t('Save FiveM compatibility'),on_click=self.save))
        self.status=label('',wrap=True);layout.addWidget(self.status)
    def choose_config(self):
        path,_=QFileDialog.getOpenFileName(self,t('Server configuration'),'','Config (*.cfg)')
        if path:
            self.cfg.setText(path);report=inspect_config(path)
            self.pure.setCurrentIndex(max(0,self.pure.findData(report['pure_level'])))
            self.status.setText('\n'.join(report['errors']) or t('Included configurations read. No server values were changed.'))
    def save(self):
        old=self.s.fivem.policy(self.game.id)
        policy=replace(old,server_id=self.server.text().strip(),permission_reference=self.reference.text().strip(),
            graphics_plugins_allowed=self.allowed.isChecked(),pure_level=self.pure.currentData(),
            allowed_backends=frozenset({'dlss5_feeder','renodx_nr'}),experimental_consent=self.panel.consent.isChecked())
        self.s.fivem.save_policy(self.game.id,policy,'dlss5_feeder')
        self.s.db.set_setting('fivem_community:'+self.game.id,{'server_config':self.cfg.text().strip(),'client_plugins_permitted':self.client.isChecked()})
        self.panel.refresh();self.status.setText(t('FiveM compatibility saved. Client restrictions remain enforced.'))
