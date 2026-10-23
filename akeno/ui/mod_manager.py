"""The normal game-mod workflow. Diagnostics remain in optional advanced tools."""
from pathlib import Path
import json
from PySide6.QtCore import Qt,QObject,Signal,QTimer
from shiboken6 import isValid
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QComboBox,QCheckBox,QLabel,QProgressBar,
    QPlainTextEdit,QTabWidget,QTableWidget,QTableWidgetItem,QDialog,QDialogButtonBox,QFileDialog,QFormLayout,QLineEdit,QGridLayout,QSpinBox,QDoubleSpinBox)
from akeno.i18n import t
from akeno.security.downloads import CancelToken
from akeno.ui.widgets import label,button,confirm,info,KeyValueGrid


class _Progress(QObject):
    changed=Signal(int,object)


class ModPanel(QWidget):
    def __init__(self,ctl,game,parent=None):
        super().__init__(parent);self.ctl=ctl;self.s=ctl.s;self.game=game;self.manager=self.s.mods
        self.progress_events=_Progress(self);self.progress_events.changed.connect(self.download_progress)
        self.busy=False;self.token=None;self.epoch=0;self.closed=False;self.decisions=[];self.prepared=None
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0)
        outer.addWidget(label(game.name,'h2'))
        gpu=self.s.selected_gpu();outer.addWidget(label((gpu.name if gpu else t('GPU not detected'))+' · '+t(game.api.label),'dim'))
        self.tabs=QTabWidget();outer.addWidget(self.tabs)
        pages=[]
        for title in ('Community DLSS 5','Versions','Advanced / Diagnostics'):
            page=QWidget();layout=QVBoxLayout(page);layout.setAlignment(Qt.AlignmentFlag.AlignTop);self.tabs.addTab(page,t(title));pages.append(layout)
        main,versions,advanced=pages
        self.summary=label('',wrap=True);main.addWidget(self.summary)
        self.facts=KeyValueGrid(150);main.addWidget(self.facts)
        self.methods=QComboBox();self.methods.setAccessibleName(t('Community method'));main.addWidget(self.methods)
        form=QFormLayout();self.runtime=QComboBox();self.runtime.setAccessibleName(t('Neural runtime'))
        self.preset=QComboBox()
        for value in ('performance','balanced','quality','ultra','disabled'):self.preset.addItem(t(value.title()),value)
        saved=self.s.db.game_overrides(game.id)
        default='performance' if game.launcher=='fivem' and gpu and gpu.generation=='RTX 40' else 'balanced' if gpu and gpu.generation=='RTX 40' else 'quality'
        self.preset.setCurrentIndex(max(0,self.preset.findData(saved.get('mod_preset',default))))
        form.addRow(t('Neural runtime'),self.runtime);form.addRow(t('Quality preset'),self.preset);main.addLayout(form)
        self.offline=QCheckBox(t('This is an eligible offline / singleplayer session'))
        self.offline.setVisible(game.launcher!='fivem');main.addWidget(self.offline)
        self.consent=QCheckBox(t('I accept experimental community mods and permitted local component reuse'))
        outer.insertWidget(2,self.consent)
        self.requirements=QPlainTextEdit();self.requirements.setReadOnly(True);self.requirements.setMaximumHeight(160);main.addWidget(self.requirements)
        row=QHBoxLayout();self.install_button=button(t('Install DLSS 5'),'download','primary',on_click=self.install)
        row.addWidget(self.install_button);row.addWidget(button(t('Launch Game'),'play',on_click=self.launch));outer.insertLayout(3,row)
        row=QGridLayout()
        for i,(title,fn) in enumerate([('Choose Preset',self.configure),('Restore previous version',self.previous),('Remove mod completely',self.restore)]):row.addWidget(button(t(title),on_click=fn),i//2,i%2)
        main.addLayout(row)
        row=QGridLayout()
        for i,(title,fn) in enumerate([('Find local runtimes',self.find_runtime),('Import community runtime',self.import_runtime),('Resolve Dependencies',self.resolve),('Refresh',self.refresh),('Import licensed NR consumer',self.import_consumer)]):row.addWidget(button(t(title),on_click=fn),i//2,i%2)
        main.addLayout(row)
        self.version_filter=QComboBox();self.version_filter.addItem(t('Stable releases'),False);self.version_filter.addItem(t('Include pre-releases'),True)
        versions.addWidget(self.version_filter)
        self.auto_updates=QCheckBox(t('Check community updates automatically once a day'));self.auto_updates.setChecked(self.s.db.get_setting('community_auto_updates',False))
        self.auto_updates.toggled.connect(lambda enabled:self.s.db.set_setting('community_auto_updates',enabled));versions.addWidget(self.auto_updates)
        self.version_table=QTableWidget(0,5);self.version_table.setHorizontalHeaderLabels([t('Project'),t('Version'),t('Status'),t('Architecture'),t('Source')]);self.version_table.setWordWrap(True)
        self.version_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);self.version_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);versions.addWidget(self.version_table)
        self.version_info=QPlainTextEdit();self.version_info.setReadOnly(True);self.version_info.setMaximumHeight(190);versions.addWidget(self.version_info)
        self.version_table.currentCellChanged.connect(self.version_details)
        versions.addWidget(button(t('Use selected version'),on_click=self.use_version))
        versions.addWidget(button(t('Check community updates'),'refresh',on_click=self.sync))
        self.update_status=label(t('Pinned versions remain available offline. Updates require explicit selection.'),'dim',wrap=True);versions.addWidget(self.update_status)
        advanced.addWidget(label(t('Installation and Neural Rendering execution are separate results. Advanced verification is optional.'),'dim',wrap=True))
        for title,fn in [('Rendering pipelines',self.open_pipeline),('Community runtime diagnostics',self.open_runtime),('Official DLL management',self.open_official),('View installation logs',self.logs),('Execution status',self.execution_status),('Import private component manifest',self.import_manifest)]:advanced.addWidget(button(t(title),on_click=fn))
        if game.launcher=='fivem':
            from akeno.ui.fivem_community import FiveMCommunitySettings
            self.fivem_settings=FiveMCommunitySettings(ctl,game,self);main.insertWidget(2,self.fivem_settings)
        self.progress=QProgressBar();self.progress.hide();outer.addWidget(self.progress)
        self.status=label('',wrap=True);outer.addWidget(self.status)
        self.cancel_button=button(t('Cancel'),'stop',on_click=self.cancel);self.cancel_button.hide();outer.addWidget(self.cancel_button)
        self.methods.currentIndexChanged.connect(self.method_changed)
        self.version_filter.currentIndexChanged.connect(self.render_versions)
        self.offline.toggled.connect(self.refresh);self.runtime.currentIndexChanged.connect(self.refresh);self.preset.currentIndexChanged.connect(self.invalidate)
        self.refresh()
        self.update_timer=QTimer(self);self.update_timer.setSingleShot(True);self.update_timer.timeout.connect(self.auto_sync);self.update_timer.start(2000)

    def closeEvent(self,event):
        self.closed=True;self.epoch+=1;self.cancel();super().closeEvent(event)
    def cancel(self):
        if self.token:self.token.cancel()
    def invalidate(self,*_):self.prepared=None;self.epoch+=1
    def run(self,key,work,done):
        if self.busy:return
        self.busy=True;epoch=self.epoch;self.token=CancelToken();token=self.token
        self.progress.setRange(0,0);self.progress.show();self.cancel_button.show();self.install_button.setEnabled(False)
        self.status.setText(t('Preparing the selected community stack…'))
        def finish(value):
            self.busy=False
            if self.closed or not isValid(self):return
            self.progress.hide();self.cancel_button.hide()
            if epoch!=self.epoch:self.method_changed();self.status.setText(t('Selection changed. Review the current selection again.'));return
            done(value)
        def error(exc):
            self.busy=False
            if self.closed or not isValid(self):return
            self.progress.hide();self.cancel_button.hide();self.status.setText(t(str(exc)));self.method_changed()
        started=self.ctl.run('mods_'+self.game.id+'_'+key,lambda:work(token),finish,error)
        if not started:self.busy=False;self.progress.hide();self.cancel_button.hide();self.method_changed()

    def download_progress(self,current,total):
        if self.closed or not isValid(self):return
        if total:
            self.progress.setRange(0,100);self.progress.setValue(min(100,int(current*100/total)))
            self.status.setText(t('Download {current} / {total} MiB',current=round(current/1048576,1),total=round(total/1048576,1)))

    def refresh(self,*_):
        selected=self.methods.currentData();runtime=self.runtime.currentData()
        self.methods.blockSignals(True);self.methods.clear()
        try:self.decisions=self.manager.assessments(self.game.id,self.offline.isChecked(),runtime)
        except (ValueError,OSError) as exc:self.status.setText(str(exc));self.decisions=[]
        for row in self.decisions:self.methods.addItem(t(row['name']),row['id'])
        chosen=selected or self.s.db.game_overrides(self.game.id).get('mod_recipe')
        index=self.methods.findData(chosen)
        if index>=0:self.methods.setCurrentIndex(index)
        self.methods.blockSignals(False)
        self.runtime.blockSignals(True);self.runtime.clear();self.runtime.addItem(t('Find a permitted local runtime automatically'),None)
        for pin,row in self.manager.runtime_choices().items():
            trust=t('NVIDIA signature verified') if row.get('category')=='official' else t('Experimental community runtime')
            self.runtime.addItem(str(row.get('version') or t('Unknown'))+' · '+pin[:12]+' · '+trust,pin)
        pin=runtime;idx=self.runtime.findData(pin)
        if idx>=0:self.runtime.setCurrentIndex(idx)
        self.runtime.blockSignals(False);self.method_changed();self.render_versions()

    def method_changed(self,*_):
        self.invalidate()
        row=next((r for r in self.decisions if r['id']==self.methods.currentData()),None)
        if not row:self.summary.setText(t('No reviewed community method is available for this target.'));self.install_button.setEnabled(False);return
        self.summary.setText(t('Experimental community installation — execution must be tested in the selected game.'))
        old_preset=self.preset.currentData() or self.s.db.game_overrides(self.game.id).get('mod_preset','quality')
        self.preset.blockSignals(True);self.preset.clear()
        from dataclasses import replace
        from akeno.core.models import GraphicsApi
        config_game=replace(self.game,api=GraphicsApi.D3D12) if row['family'] in ('feeder','fivem','renodx_feeder','renodx_native') else self.game
        supported=[p.id for p in self.s.registry.get('optiscaler_nr').profiles(config_game)]
        for value in ('performance','balanced','quality','ultra','disabled'):
            if value in supported:self.preset.addItem(t(value.title()),value)
        self.preset.addItem(t('Custom'),'custom')
        self.preset.setCurrentIndex(max(0,self.preset.findData(old_preset)));self.preset.blockSignals(False)
        conditions=row['conditions'];self.requirements.setPlainText('\n\n'.join(t(c['message'])+' '+t(c['remedy']) for c in conditions) or t('The reviewed package set is ready.'))
        active=self.s.installer.active_install(self.game.id)
        self.facts.set_rows([(t('Executable'),self.game.exe_path or t('Unknown')),(t('DLSS (game)'),self.game.dlss_version or t('Not found')),
            (t('Installed version'),(' / '.join(filter(None,[active.get('package_version'),active['params'].get('_mod_consumer_version')])) or t('Unknown')) if active else t('Not installed')),
            (t('Installation status'),t('Installed') if active else t('Not installed')),
            (t('Recommended method'),t(row['name']) if row['eligible'] else t('Resolve the listed prerequisites'))])
        # Missing local runtimes can be found/imported with the explicit reuse consent.
        retryable={'runtime','official_sr'}
        hard=[c for c in conditions if c['kind']!='resolvable_dependency' and c['code'] not in retryable]
        self.install_button.setEnabled(not hard and not self.busy)
        self.install_button.setText(t('Change Version / Reinstall') if active else t('Install DLSS 5'))

    def install(self):
        if not self.consent.isChecked():self.status.setText(t('Confirm experimental community mods and local reuse first.'));return
        recipe=self.methods.currentData();runtime=self.runtime.currentData();preset=self.preset.currentData();offline=self.offline.isChecked();gid=self.game.id
        if not recipe:return
        custom=self.custom_settings() if preset=='custom' else None
        if preset=='custom' and custom is None:return
        def done(prepared):
            dialog=QDialog(self);dialog.setWindowTitle(t('Review complete installation'));dialog.resize(850,600)
            layout=QVBoxLayout(dialog);view=QPlainTextEdit();view.setReadOnly(True)
            details=[t(s) for s in prepared.plan.summary]+['','\n'.join(op.rel_path for op in prepared.plan.ops)]
            view.setPlainText('\n'.join(details));layout.addWidget(view)
            buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
            if dialog.exec()!=QDialog.DialogCode.Accepted:self.status.setText(t('Installation cancelled. Game files were not changed.'));return
            self.run('install',lambda token:self.manager.install(prepared),self.installed)
        self.run('prepare',lambda token:self.manager.prepare(gid,recipe,runtime,preset,offline,True,token,self.progress_events.changed.emit,custom),done)

    def installed(self,result):
        self.status.setText('\n'.join(t(x) for x in result.messages));self.refresh();self.ctl.installs_changed.emit()
    def configure(self):
        gid=self.game.id;preset=self.preset.currentData()
        custom=self.custom_settings() if preset=='custom' else None
        if preset=='custom' and custom is None:return
        self.run('preset',lambda token:self.manager.configure(gid,preset,custom),self.installed)
    def custom_settings(self):
        from dataclasses import replace
        from akeno.core.models import GraphicsApi
        dialog=QDialog(self);dialog.setWindowTitle(t('Custom'));layout=QVBoxLayout(dialog);form=QFormLayout();layout.addLayout(form);editors={}
        row=next(r for r in self.decisions if r['id']==self.methods.currentData())
        if row['family'] in ('fivem','renodx_native','renodx_feeder'):
            # Exact upstream ConfigVersion 7 keys; ordinary presets remain the default.
            for key,minimum,maximum,value in [('NRResolutionScale',.25,1.,1.),('NRPasses',1,4,1),('NRStyle',0,2,0),('NeuralUplift',0,1,1)]:
                widget=QDoubleSpinBox() if key=='NRResolutionScale' else QSpinBox();widget.setRange(minimum,maximum);widget.setValue(value);form.addRow(key,widget);editors[key]=widget.value
        else:
            game=replace(self.game,api=GraphicsApi.D3D12) if row['family']=='feeder' else self.game
            for spec in self.s.registry.get('optiscaler_nr').param_specs(game):
                if spec.kind=='bool':
                    widget=QCheckBox();widget.setChecked(spec.default);getter=widget.isChecked
                elif spec.kind=='choice':
                    widget=QComboBox()
                    for value,title in spec.choices:widget.addItem(t(title),value)
                    widget.setCurrentIndex(max(0,widget.findData(spec.default)));getter=widget.currentData
                else:
                    widget=QSpinBox() if spec.kind=='int' else QDoubleSpinBox();widget.setRange(spec.minimum if spec.minimum is not None else -10000,spec.maximum if spec.maximum is not None else 10000);widget.setValue(spec.default);getter=widget.value
                widget.setToolTip(t(spec.help));form.addRow(t(spec.label),widget);editors[spec.key]=getter
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        return {key:get() for key,get in editors.items()} if dialog.exec()==QDialog.DialogCode.Accepted else None

    def restore(self):
        if confirm(self,t('Restore Game'),t('Restore original files from the verified backup chain and remove the managed mod?')):
            self.run('restore',lambda token:self.manager.restore(self.game.id),self.installed)
    def previous(self):
        if confirm(self,t('Restore previous version'),t('Undo the newest managed installation while preserving its previous managed version?')):
            self.run('previous',lambda token:self.manager.previous(self.game.id),self.installed)
    def launch(self):
        if not self.consent.isChecked():self.status.setText(t('Confirm experimental community mods and local reuse first.'));return
        self.run('launch',lambda token:self.manager.launch(self.game.id,self.offline.isChecked(),True),lambda _:self.status.setText(t('Game launch requested. Neural Rendering execution is a separate result.')))
    def find_runtime(self):
        if not self.consent.isChecked():self.status.setText(t('Confirm experimental community mods and local reuse first.'));return
        self.run('runtimes',lambda token:self.manager.discover_runtimes(True,token),lambda _:self.refresh())
    def resolve(self):
        if not self.consent.isChecked():return
        self.run('dependencies',lambda token:self.manager.resolve_dependencies(token),lambda _:self.refresh())
    def auto_sync(self):
        if self.closed or self.busy or not self.auto_updates.isChecked():return
        from datetime import datetime,timezone,timedelta
        value=self.s.db.get_setting('community_last_check')
        try:due=not value or datetime.now(timezone.utc)-datetime.fromisoformat(value)>timedelta(days=1)
        except ValueError:due=True
        if due:self.sync()

    def sync(self):
        def done(rows):
            from datetime import datetime,timezone
            self.s.db.set_setting('community_last_check',datetime.now(timezone.utc).isoformat())
            count=sum(r['ok'] for r in rows);self.update_status.setText(t('Updated {count} original repositories. Existing pinned versions are preserved.',count=count));self.render_versions()
            failed=[r for r in rows if not r['ok']]
            if failed:self.version_info.setPlainText('\n'.join(r['project']+': '+r['error'] for r in failed))
        self.run('releases',lambda token:self.manager.refresh_releases(token),done)
    def render_versions(self,*_):
        rows=[];self.local_version_rows={}
        for project in self.manager.catalog.projects.values():
            pinned=self.manager.catalog.versions(project['id'],self.version_filter.currentData())
            known={p['release_tag'] for p in pinned}
            rows.extend((project['name'],p['version'],t('Pinned installation') if p['installable'] else t('Dependency review required'),','.join(p['architectures']),project['repository']) for p in pinned)
            rows.extend((project['name'],r['tag'],t('New release — review required'),'—',project['repository']) for r in self.manager.releases.available(project['repository'],self.version_filter.currentData()) if r['tag'] not in known)
        library=self.s.db.get_setting('pipeline_component_library',{})
        for pin,item in library.items():
            if item.get('role')!='neural_consumer':continue
            self.local_version_rows[len(rows)]=item
            rows.append((t('Local licensed NR consumer'),item['version'],t('Private imported version'),'x64',item['source']))
        self.version_table.setRowCount(len(rows))
        for y,row in enumerate(rows):
            for x,value in enumerate(row):self.version_table.setItem(y,x,QTableWidgetItem(value))
        self.version_table.resizeColumnsToContents()
        if rows:self.version_table.selectRow(0)
        selected=self.methods.currentData();choice=self.manager.catalog.recipes.get(selected,{})
        current=choice.get('packages',[])
        updates=[]
        for pid in current:
            p=self.manager.catalog.packages[pid];project=self.manager.catalog.projects[p['project']]
            available=self.manager.releases.available(project['repository'])
            if available and available[0]['tag']!=p['release_tag']:
                from akeno.mods.releases import version_key
                if version_key(available[0]['tag'])>version_key(p['version']):updates.append(project['name']+' '+available[0]['tag'])
        if updates:self.update_status.setText(t('Updates available: {versions}. Select a reviewed matching recipe explicitly.',versions=', '.join(updates)))
    def version_details(self,*_):
        row=self.version_table.currentRow()
        if row<0:return
        if row in self.local_version_rows:
            item=self.local_version_rows[row];self.version_info.setPlainText(json.dumps(item,ensure_ascii=False,indent=2));return
        name=self.version_table.item(row,0);version=self.version_table.item(row,1)
        if not name or not version:return
        p=next((p for p in self.manager.catalog.packages.values() if p['name']==name.text() and p['version']==version.text()),None)
        if not p:
            project=next((p for p in self.manager.catalog.projects.values() if p['name']==name.text()),None)
            release=next((r for r in self.manager.releases.cached(project['repository']) if r['tag']==version.text()),{}) if project else {}
            self.version_info.setPlainText(json.dumps(release,ensure_ascii=False,indent=2));return
        details=[t('Source')+': '+p['url'],t('Release date')+': '+str(p.get('published_at') or t('Unknown')),
            t('License')+': '+p['license'],t('Graphics API')+': '+', '.join(p['graphics_apis']),t('GPU')+': '+', '.join(p.get('gpu_families',[])),
            t('Dependencies')+': '+', '.join(p.get('dependencies',[])),t('SHA-256')+': '+p['sha256'],
            t('Known issues')+': '+'; '.join(p.get('issues',[])),t('Local execution')+': '+t('Not tested')]
        self.version_info.setPlainText('\n'.join(details))

    def use_version(self):
        row=self.version_table.currentRow()
        if row<0:return
        if row in self.local_version_rows:
            item=self.local_version_rows[row];self.s.pipelines.components.select(self.game.id,'neural_consumer',item['sha256']);self.refresh();self.tabs.setCurrentIndex(0);return
        project=self.version_table.item(row,0).text();version=self.version_table.item(row,1).text()
        package=next((p for p in self.manager.catalog.packages.values() if p['name']==project and p['version']==version),None)
        recipe=next((r for r in self.decisions if package and package['id'] in [p['id'] for p in r['packages']]),None)
        if not recipe:self.status.setText(t('This version needs a complete reviewed dependency recipe before installation.'));return
        self.methods.setCurrentIndex(self.methods.findData(recipe['id']));self.tabs.setCurrentIndex(0)

    def open_pipeline(self):
        from akeno.ui.pipelines import PipelineDialog
        PipelineDialog(self.ctl,self).exec();self.refresh()
    def open_official(self):
        from akeno.ui.pipelines import PipelineDialog
        dialog=PipelineDialog(self.ctl,self);dialog.tabs.setCurrentIndex(2);dialog.exec();self.refresh()

    def open_runtime(self):
        from akeno.ui.community_runtime import CommunityRuntimeDialog
        CommunityRuntimeDialog(self.ctl,self).exec();self.refresh()
    def import_runtime(self):self.open_runtime()
    def import_consumer(self):
        if not self.consent.isChecked():self.status.setText(t('Confirm experimental community mods and local reuse first.'));return
        path,_=QFileDialog.getOpenFileName(self,t('Import licensed NR consumer'),'','NR add-on (*.addon64)')
        if not path:return
        dialog=QDialog(self);dialog.setWindowTitle(t('Exact NR consumer contract'));layout=QVBoxLayout(dialog)
        layout.addWidget(label(t('Only a licensed RenoDX DLSS5 consumer matching ConfigVersion 7 is accepted. Generic HDR add-ons are different.'),wrap=True))
        form=QFormLayout();version=QLineEdit();source=QLineEdit();license=QLineEdit()
        form.addRow(t('Exact component version'),version);form.addRow(t('Upstream HTTPS source'),source);form.addRow(t('Component license'),license);layout.addLayout(form)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            values=(path,version.text(),source.text(),license.text(),True)
            self.run('consumer',lambda token:self.manager.import_consumer(*values),lambda _:self.refresh())

    def import_manifest(self):
        path,_=QFileDialog.getOpenFileName(self,t('Import private component manifest'),'','JSON (*.json)')
        if path and self.consent.isChecked():self.run('import',lambda token:self.s.pipelines.components.import_manifest(path,True),lambda _:self.refresh())
    def execution_status(self):
        def show(report):
            dialog=QDialog(self);dialog.setWindowTitle(t('Execution status'));dialog.resize(850,650);layout=QVBoxLayout(dialog)
            view=QPlainTextEdit();view.setReadOnly(True);view.setPlainText(json.dumps(report,ensure_ascii=False,indent=2,default=str));layout.addWidget(view);dialog.exec()
        self.run('execution_status',lambda token:self.manager.rendering_status(self.game.id),show)

    def logs(self):
        tx=self.s.installer.active_install(self.game.id)
        dialog=QDialog(self);dialog.setWindowTitle(t('Installation logs'));dialog.resize(800,500);layout=QVBoxLayout(dialog)
        view=QPlainTextEdit();view.setReadOnly(True);view.setPlainText('\n'.join(str(s) for s in tx.get('log',[])) if tx else t('No installation logs.'));layout.addWidget(view);dialog.exec()
