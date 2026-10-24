"""Graphical rendering route planning, standalone validation and exact DLL swaps."""
from dataclasses import asdict
from pathlib import Path
import json
import tempfile
import shutil
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QTabWidget,QWidget,QComboBox,
    QCheckBox,QPlainTextEdit,QFileDialog,QLineEdit,QFormLayout,QDialogButtonBox,QScrollArea,QListWidget)
from akeno.i18n import t
from akeno.ui.widgets import fit_to_screen, button,label,confirm
from akeno.security.downloads import CancelToken,sha256_file
from akeno.diagnostics.privacy import redact


class PipelineDialog(QDialog):
    def __init__(self,ctl,parent=None):
        super().__init__(parent)
        self.ctl=ctl;self.s=ctl.s;self.game=self.s.selected_game();self.prepared=None;self.swap=None;self.analysis=None;self.result=None
        self._closed=False;self.token=None;self.busy=False;self.revision=0
        self.setWindowTitle(t('Rendering pipelines'));fit_to_screen(self, 1060, 780)
        outer=QVBoxLayout(self);outer.addWidget(label(t('Rendering pipelines'),'h1'))
        outer.addWidget(label(self.game.name if self.game else t('No game selected'),'dim'))
        outer.addWidget(label(t('Choose an eligible rendering path, review its files, test the runtime separately, then verify the game. Installation alone does not prove Neural Rendering.'),'dim',wrap=True))
        runtime_row=QHBoxLayout();runtime_row.addWidget(label(t('Imported neural runtime'),'dim'))
        self.runtime=QComboBox();self.runtime.setMinimumContentsLength(24);runtime_row.addWidget(self.runtime,1)
        runtime_row.addWidget(button(t('Import local runtime'),'folder',on_click=self.open_runtime));outer.addLayout(runtime_row)
        self.offline=QCheckBox(t('I confirm this is an eligible offline / single-player test'))
        self.consent=QCheckBox(t('I accept the experimental runtime and local component limitations'))
        outer.addWidget(self.offline);outer.addWidget(self.consent)
        self.tabs=QTabWidget();outer.addWidget(self.tabs,1)
        layouts=[]
        for title in ('Choose pipeline','Runtime self-test','Official DLL management','Visual A/B comparison'):
            page=QWidget();lay=QVBoxLayout(page);scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(page)
            self.tabs.addTab(scroll,t(title));layouts.append(lay)
        pipeline,selftest,dlls,ab=layouts
        self.routes=QComboBox();self.routes.setAccessibleName(t('Rendering route'));pipeline.addWidget(self.routes)
        actions=QHBoxLayout()
        self.preview_button=None;self.install_button=None
        for text,fn in [('Analyze pipelines',self.analyze),('Preview installation',self.prepare),('Install with backup',self.install),('Open game verification',self.open_runtime)]:
            item=button(t(text),on_click=fn);actions.addWidget(item)
            if fn==self.prepare:self.preview_button=item
            if fn==self.install:self.install_button=item;item.setEnabled(False)
        pipeline.addLayout(actions)
        deps=QHBoxLayout()
        for text,fn in [('Import dependency',self.import_component),('Import private component manifest',self.import_manifest),('Import diagnostic source build',self.import_diagnostic),('Recover interrupted transactions',self.recover)]:deps.addWidget(button(t(text),on_click=fn))
        pipeline.addLayout(deps)
        self.summary=label('',wrap=True);pipeline.addWidget(self.summary)
        self.requirements=QListWidget();self.requirements.setWordWrap(True);pipeline.addWidget(self.requirements,1)
        pipeline.addWidget(button(t('Show technical details / file preview'),on_click=lambda:self.plan_view.setVisible(not self.plan_view.isVisible())))
        self.plan_view=QPlainTextEdit();self.plan_view.setReadOnly(True);self.plan_view.hide();pipeline.addWidget(self.plan_view,1)
        selftest.addWidget(label(t('This test uses a pinned 64-bit host in an isolated AKENO folder. It changes no game files. A successful standalone test does not prove that a game uses Neural Rendering.'),'dim',wrap=True))
        selftest.addWidget(button(t('Build optional diagnostic backend locally'),on_click=self.build_diagnostic))
        selftest.addWidget(label(t('Test consumer: OptiScaler NR 1.3.0. Host: Feeder 1.19.0. An imported diagnostic build replaces only the identified consumer.'),'dim',wrap=True))
        test_actions=QHBoxLayout();test_actions.addWidget(button(t('Test Neural Rendering Runtime'),'play',on_click=self.start_selftest));test_actions.addWidget(button(t('Stop'),'stop',on_click=self.cancel));test_actions.addWidget(button(t('Export test result'),'file',on_click=self.export_result));selftest.addLayout(test_actions)
        self.test_status=label(t('Not tested'),'dim',wrap=True);selftest.addWidget(self.test_status)
        self.test_output=QPlainTextEdit();self.test_output.setReadOnly(True);selftest.addWidget(self.test_output,1)
        dlls.addWidget(label(t('Import official NVIDIA-authenticated components. Observe the active DLL paths while the game runs, then close it before previewing a swap. Only selected observed paths are changed.'),'dim',wrap=True))
        dr=QHBoxLayout()
        dr.addWidget(button(t('Import official DLL'),'folder',on_click=self.import_official));dr.addWidget(button(t('Observe active DLL paths'),'search',on_click=self.observe_dlls));dlls.addLayout(dr)
        self.dll_path=QComboBox();self.dll_version=QComboBox();self.dll_path.currentIndexChanged.connect(self.invalidate);self.dll_version.currentIndexChanged.connect(self.invalidate);dlls.addWidget(self.dll_path);dlls.addWidget(self.dll_version)
        dr2=QHBoxLayout();dr2.addWidget(button(t('Preview DLL swap'),on_click=self.prepare_swap));dr2.addWidget(button(t('Preview bulk game update'),on_click=self.prepare_bulk));dr2.addWidget(button(t('Apply DLL swap with backup'),on_click=self.install_swap));dr2.addWidget(button(t('Restore latest transaction'),on_click=self.restore));dlls.addLayout(dr2)
        self.dll_output=QPlainTextEdit();self.dll_output.setReadOnly(True);dlls.addWidget(self.dll_output,1)
        ab.addWidget(label(t('Use the same camera, scene, resolution and settings for NR off/on. Import real screenshots captured by the game or a permitted backend; AKENO does not inject a screenshot hook.'),'dim',wrap=True))
        self.ab_conditions={};form=QFormLayout()
        for key,title in [('viewpoint','Camera / scene'),('resolution','Game resolution'),('settings','Graphics settings'),('renderer','Renderer')]:
            edit=QLineEdit();form.addRow(t(title),edit);self.ab_conditions[key]=edit
        ab.addLayout(form)
        self.ab_paths={};abr=QHBoxLayout()
        for side,title in [('off','Choose NR-off screenshot'),('on','Choose NR-on screenshot')]:abr.addWidget(button(t(title),on_click=lambda _=False,s=side:self.choose_ab(s)))
        abr.addWidget(button(t('Save visual comparison'),on_click=self.save_ab));ab.addLayout(abr)
        self.ab_notes=QLineEdit();self.ab_notes.setPlaceholderText(t('Observed sharpness, flicker, ghosting or HUD effects; temporal findings need video'))
        ab.addWidget(self.ab_notes)
        self.ab_output=QPlainTextEdit();self.ab_output.setReadOnly(True);ab.addWidget(self.ab_output,1)
        self.status=label('',wrap=True);outer.addWidget(self.status)
        self.finished.connect(self.closed);self.routes.currentIndexChanged.connect(self.show_route)
        self.runtime.currentIndexChanged.connect(self.invalidate);self.offline.toggled.connect(self.invalidate);self.consent.toggled.connect(self.invalidate)
        self.reload_runtimes();self.reload_dlls()
        if self.game:self.analyze()

    def close(self):
        self.closed()
        return super().close()
    def closeEvent(self,event):
        self.closed()
        super().closeEvent(event)
    def closed(self,*_):self._closed=True;self.cancel()
    def cancel(self):
        if self.token:self.token.cancel()
    def invalidate(self,*_):
        self.prepared=None;self.swap=None;self.revision+=1
        self.install_button.setEnabled(False)
    def run(self,key,work,done):
        if self.busy or self._closed:return
        self.busy=True;revision=self.revision;self.status.setText(t('Working…'))
        def completed(value):
            self.busy=False
            if not self._closed and revision==self.revision:done(value)
            elif not self._closed:self.status.setText(t('Inputs changed. Analyze or prepare again.'))
        def failed(error):
            self.busy=False
            if not self._closed:self.status.setText(t(str(error)))
        if not self.ctl.run('pipeline_'+key,work,completed,failed):self.busy=False
    def reload_runtimes(self):
        selected=self.runtime.currentData();self.runtime.blockSignals(True);self.runtime.clear()
        self.runtime.addItem(t('Select an imported runtime'),None)
        for key,value in self.s.db.all_settings().items():
            if key.startswith('community-runtime:'):
                pin=key.split(':',1)[1]
                if not isinstance(value,dict):continue
                trust=t('NVIDIA signature verified') if value.get('category')=='official' else t('Experimental')
                self.runtime.addItem(f"{value.get('version') or t('Unknown')} · {pin[:16]} · {trust}",pin)
        official={pin:item for pin,item in self.s.db.get_setting('official_dll_inventory',{}).items()
            if item.get('name')=='nvngx_dlssnr.dll' and item.get('family')=='neural_rendering' and item.get('trust')=='nvidia_authenticated'}
        official.update({row['sha256']:row for row in self.s.db.runtimes('nvngx_dlssnr.dll') if row.get('trust')=='verified' and row.get('stored_path')})
        for pin,item in official.items():
            if self.runtime.findData(pin)<0:self.runtime.addItem(f"{item.get('version') or t('Unknown')} · {pin[:16]} · {t('NVIDIA signature verified')}",pin)
        index=self.runtime.findData(selected)
        if index>=0:self.runtime.setCurrentIndex(index)
        self.runtime.blockSignals(False)
    def open_runtime(self):
        from akeno.ui.community_runtime import CommunityRuntimeDialog
        CommunityRuntimeDialog(self.ctl,self).exec();self.reload_runtimes();self.invalidate()
    def analyze(self):
        if not self.game:return
        game_id=self.game.id;offline=self.offline.isChecked();pin=self.runtime.currentData()
        def done(result):
            self.analysis=result;self.routes.blockSignals(True);self.routes.clear()
            for route in result['routes']:
                text=t(route.name)+' · '+t(route.state.value)
                if route.execution_state=='execution_verified':text+=' · '+t('execution_verified')
                if route.recommended:text=t('Recommended')+' · '+text
                self.routes.addItem(text,route.route_id)
            best=next((i for i,r in enumerate(result['routes']) if r.recommended),0);self.routes.setCurrentIndex(best);self.routes.blockSignals(False)
            self.show_route();self.status.setText(t('Analysis complete. Select a route to inspect its requirements.'))
        self.run('analyze',lambda:self.s.pipelines.analyze(game_id,offline,pin),done)
    def show_route(self,*_):
        self.prepared=None
        self.install_button.setEnabled(False)
        if not self.analysis:return
        route=next((r for r in self.analysis['routes'] if r.route_id==self.routes.currentData()),None)
        if not route:return
        self.preview_button.setEnabled(route.eligible)
        self.summary.setText(t('Ready to preview') if route.eligible else t('Prerequisites are missing or this route is not permitted. Details are listed below.'))
        titles={'permission':t('Confirm an eligible offline test'),'closed':t('Close the game before installing'),
                'architecture':t('Game architecture matches this route'),'api':t('Selected renderer matches this route'),
                'native_calls':t('Native engine DLSS integration is established'),'rtx':t('Detected NVIDIA RTX hardware'),
                'driver':t('Exact NVIDIA driver is known'),'runtime_pin':t('Imported exact neural runtime selected'),
                'exclusive_consumer':t('Only one neural consumer'),'conflicts':t('No conflicting unmanaged graphics hooks'),
                'proxy':t('A free supported loader path is evidenced'),'consumer':t('A compatible neural consumer is selected'),
                'feeder_api':t('Reviewed Feeder transport for this renderer'),'observed_component':t('Active component paths were observed'),'bridge_contract':t('A complete reviewed legacy bridge is available')}
        if route.route_id=='configuration':
            self.summary.setText(t('Use the game graphics menu or the NVIDIA app program profile for DLAA, quality and preset overrides. Keep those settings separate from NR activation. AKENO does not change global signature policies or unsupported driver flags.'))
        self.requirements.clear()
        for check in route.requirements:
            text=titles.get(check.code)
            if text is None:
                role=check.code.split(':',1)[-1]
                text=t('Compatible pinned component: {component}',component=role)
            self.requirements.addItem(('✓ ' if check.satisfied else '○ ')+text)
        self.plan_view.setPlainText(json.dumps(redact(route.to_dict()),indent=2,ensure_ascii=False))
    def prepare(self):
        if not self.game:return
        route=self.routes.currentData()
        if route=='configuration':
            self.show_route();self.status.setText(t('Configuration guidance only. No game or driver files changed.'));return
        if route=='official_swap':
            self.tabs.setCurrentIndex(2);self.status.setText(t('Choose an observed active path and an imported official version.'));return
        gid,route,pin=self.game.id,route,self.runtime.currentData();offline,consent=self.offline.isChecked(),self.consent.isChecked()
        def done(prepared):
            self.prepared=prepared;self.install_button.setEnabled(True);self.plan_view.show();self.plan_view.setPlainText('\n'.join(t(x) for x in prepared.plan.summary)+'\n\n'+json.dumps(self.s.pipelines.diff(prepared),indent=2));self.status.setText(t('Preview ready. No game files changed.'))
        self.run('prepare',lambda:self.s.pipelines.prepare(gid,route,pin,offline,consent),done)
    def install(self):
        if not self.offline.isChecked() or not self.consent.isChecked():self.invalidate();return
        if not self.prepared:self.status.setText(t('Prepare a current installation preview first.'));return
        if not confirm(self,t('Install with backup'),t('Apply the displayed experimental pipeline with verified backups? This does not prove execution.'),ok_text=t('Install')):return
        prepared=self.prepared;self.prepared=None;self.install_button.setEnabled(False)
        self.run('install',lambda:self.s.pipelines.install(prepared),self.install_done)
    def install_done(self,result):self.status.setText('\n'.join(t(x) for x in result.messages));self.ctl.installs_changed.emit();self.invalidate()
    def import_manifest(self):
        bundled=self.s.offline_bundle.pipeline_manifest()
        path,_=QFileDialog.getOpenFileName(self,t('Import private component manifest'),str(bundled) if bundled else '','JSON (*.json)')
        permitted=self.consent.isChecked()
        if path:self.run('import',lambda:self.s.pipelines.components.import_manifest(path,permitted),lambda _:self.status.setText(t('Local components imported. Analyze again.')))
    def import_diagnostic(self):
        path,_=QFileDialog.getOpenFileName(self,t('Import diagnostic source build'),'','JSON (*.json)')
        permitted=self.consent.isChecked()
        if path:self.run('diagnostic',lambda:self.s.pipelines.components.import_diagnostic(path,permitted),lambda _:self.status.setText(t('Local diagnostic build selected. Its source declaration is not a security certification.')))
    def import_component(self):
        dialog=QDialog(self);dialog.setWindowTitle(t('Import dependency'));lay=QVBoxLayout(dialog);form=QFormLayout();lay.addLayout(form)
        role=QComboBox();role.addItem(t('ReShade with full add-on support'),'reshade');role.addItem(t('Motion-vector shader provider'),'motion_provider');form.addRow(t('Component'),role)
        fields={}
        for key,title in [('version','Exact component version'),('source','Upstream HTTPS source'),('license','Component license')]:
            edit=QLineEdit();form.addRow(t(title),edit);fields[key]=edit
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);lay.addWidget(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        path,_=QFileDialog.getOpenFileName(self,t('Choose local component'))
        if not path:return
        permitted=self.consent.isChecked();values={k:v.text().strip() for k,v in fields.items()};selected=role.currentData()
        def work():
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);src=Path(path);shutil.copyfile(src,root/src.name)
                assets=[]
                if selected=='motion_provider':
                    from akeno.pipeline.manifests import shader_dependencies
                    for index,dependency in enumerate(shader_dependencies(src)):
                        asset=Path(dependency['source']);name=f'asset-{index}'+asset.suffix;shutil.copyfile(asset,root/name)
                        assets.append({'path':name,'destination':dependency['destination'],'sha256':sha256_file(root/name)})
                item=dict(values,assets=assets,role=selected,path=src.name,sha256=sha256_file(root/src.name),architecture='x64' if selected=='reshade' else None,apis=['d3d12'],local_use_permitted=permitted)
                (root/'manifest.json').write_text(json.dumps({'schema':1,'distribution':'private-local-use','components':[item]}))
                return self.s.pipelines.components.import_manifest(root/'manifest.json',permitted)
        self.run('component',work,lambda _:self.status.setText(t('Local components imported. Analyze again.')))
    def build_diagnostic(self):
        from akeno.diagnostics.localbuild import prerequisites,build
        req=prerequisites()
        if not req['ready']:
            self.test_status.setText(t('Missing local build tools: {tools}',tools=', '.join(req['missing'])));return
        if not confirm(self,t('Build optional diagnostic backend locally'),t('Download the pinned open-source OptiScaler 1.3.0 sources and compile with your installed Visual Studio tools? No NVIDIA runtime is downloaded and no game files are changed.'),ok_text=t('Build locally')):return
        self.token=CancelToken();token=self.token
        def done(result):
            self.test_output.setPlainText(json.dumps(result,indent=2,ensure_ascii=False))
            self.test_status.setText(t('Local diagnostic build complete. Import diagnostic-manifest.json to select it.') if result['status']=='built' else t('Local diagnostic build failed or was cancelled. Inspect the recorded build log.'))
        self.run('localbuild',lambda:build(True,token),done)

    def start_selftest(self):
        pin=self.runtime.currentData()
        if not pin or not self.consent.isChecked():self.status.setText(t('Select an imported runtime and confirm experimental consent.'));return
        if not confirm(self,t('Test Neural Rendering Runtime'),t('Run the selected runtime and consumer in an isolated local host now? No game files will be changed.'),ok_text=t('Start test')):return
        self.token=CancelToken();token=self.token
        def done(result):
            self.result=result;self.test_output.setPlainText(json.dumps(result,indent=2,ensure_ascii=False))
            sr=result.get('dlss_evaluations',{}).get('successful',t('Not measured'))
            nr=result.get('neural_evaluations',{}).get('successful',t('Not measured'))
            self.test_status.setText(t('SR successful calls: {sr}. NR successful API returns: {nr}. GPU completion and display contribution: unconfirmed.',sr=sr,nr=nr)+'\n'+t('Runtime self-test: {status}',status=t(result['status'])))
            self.status.setText(t(result.get('next_action') or 'Standalone result saved locally; game integration remains separate.'))
        self.run('selftest',lambda:self.s.neural_selftest.run(pin,True,token),done)
    def export_result(self):
        if not self.result:return
        path,_=QFileDialog.getSaveFileName(self,t('Export test result'),'akeno-runtime-selftest.json','JSON (*.json)')
        if path:Path(path).write_text(json.dumps(redact(self.result),indent=2),encoding='utf-8')
    def recover(self):
        interrupted=self.s.installer.interrupted()
        if not interrupted:self.status.setText(t('No interrupted transactions found.'));return
        if not confirm(self,t('Recover interrupted transactions'),t('Verify ownership and backups, then roll back interrupted changes? Unknown or modified files will be preserved.'),ok_text=t('Recover')):return
        self.run('recover',lambda:[self.s.installer.recover(tx['id']) for tx in interrupted],lambda results:self.status.setText('\n'.join(t(m) for r in results for m in r.messages)))
    def reload_dlls(self):
        self.dll_version.clear()
        for pin,item in self.s.db.get_setting('official_dll_inventory',{}).items():self.dll_version.addItem(item['name']+' · '+item['version']+' · '+pin[:12],pin)
        self.dll_path.clear()
        if self.game:
            for path in self.s.db.get_setting('dll_load_evidence:'+self.game.id,{}).get('paths',{}):self.dll_path.addItem(path,path)
    def import_official(self):
        path,_=QFileDialog.getOpenFileName(self,t('Import official DLL'),'','DLL (*.dll)')
        permitted=self.consent.isChecked()
        if path:self.run('official',lambda:self.s.dlls.import_official(path,permitted),lambda _:self.reload_dlls())
    def observe_dlls(self):
        if not self.game:return
        def done(result):self.reload_dlls();self.dll_output.setPlainText(json.dumps(redact(result),indent=2));self.status.setText(t('Active paths recorded. Close the game before previewing a swap.'))
        self.run('modules',lambda:self.s.dlls.observe_active_paths(self.game.id),done)
    def prepare_swap(self):
        if not self.game or not self.dll_path.currentData() or not self.dll_version.currentData():return
        replacements={self.dll_path.currentData():self.dll_version.currentData()};offline=self.offline.isChecked()
        selected=self.s.db.get_setting('official_dll_inventory',{}).get(self.dll_version.currentData(),{})
        if selected.get('family')=='streamline':
            folder=Path(self.dll_path.currentData()).parent;replacements={}
            for path in folder.glob('sl.*.dll'):
                matches=[(pin,item) for pin,item in self.s.db.get_setting('official_dll_inventory',{}).items() if item['name']==path.name.casefold() and item['version']==selected['version']]
                if len(matches)!=1:self.status.setText(t('Import one unambiguous complete Streamline set at the selected version.'));return
                replacements[str(path)]=matches[0][0]
        def done(result):self.swap=result;self.dll_output.setPlainText(json.dumps(self.s.pipelines.diff(result),indent=2));self.status.setText(t('Preview ready. No game files changed.'))
        self.run('swap_preview',lambda:self.s.dlls.prepare(self.game.id,replacements,offline),done)
    def prepare_bulk(self):
        from PySide6.QtWidgets import QListWidgetItem
        pin=self.dll_version.currentData();item=self.s.db.get_setting('official_dll_inventory',{}).get(pin)
        if not item:return
        dialog=QDialog(self);dialog.setWindowTitle(t('Select games for the displayed DLL version'));layout=QVBoxLayout(dialog)
        choices=QListWidget();layout.addWidget(choices)
        for game in self.s.library.games():
            evidence=self.s.db.get_setting('dll_load_evidence:'+game.id,{})
            paths=[name for name in evidence.get('paths',{}) if Path(name).name.casefold()==item['name']]
            if paths:
                row=QListWidgetItem(game.name);row.setData(Qt.ItemDataRole.UserRole,(game.id,paths));row.setFlags(row.flags()|Qt.ItemFlag.ItemIsUserCheckable);row.setCheckState(Qt.CheckState.Unchecked);choices.addItem(row)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        selected=[choices.item(i).data(Qt.ItemDataRole.UserRole) for i in range(choices.count()) if choices.item(i).checkState()==Qt.CheckState.Checked]
        if not selected:return
        offline=self.offline.isChecked()
        def work():return [self.s.dlls.prepare(gid,{path:pin for path in paths},offline) for gid,paths in selected]
        def done(prepared):
            self.swap=prepared
            self.dll_output.setPlainText(json.dumps([{'game_id':p.game_id,'files':self.s.pipelines.diff(p)} for p in prepared],indent=2));self.status.setText(t('Review every game before applying. Each game has a separate reversible transaction.'))
        self.run('bulk_preview',work,done)

    def install_swap(self):
        if not self.offline.isChecked() or not self.consent.isChecked():self.invalidate();return
        if not self.swap:return
        if confirm(self,t('Apply DLL swap with backup'),t('Replace only the displayed observed DLL paths and keep verified originals?'),ok_text=t('Apply')):
            prepared=self.swap;self.swap=None
            if isinstance(prepared,list):
                def done(results):
                    self.status.setText('\n'.join(gid+': '+'; '.join(t(x) for x in result.messages) for gid,result in results));self.ctl.installs_changed.emit();self.invalidate()
                self.run('bulk_swap',lambda:self.s.dlls.bulk_install(prepared),done)
            else:self.run('swap',lambda:self.s.dlls.install(prepared),self.install_done)
    def restore(self):
        tx=self.s.installer.active_install(self.game.id) if self.game else None
        if tx and confirm(self,t('Restore latest transaction'),t('Restore the recorded original files after checking for later modifications?'),ok_text=t('Restore')):
            self.run('restore',lambda:self.s.installer.restore(tx['id']),self.install_done)
    def choose_ab(self,side):
        path,_=QFileDialog.getOpenFileName(self,t('Choose real captured screenshot'),'','Images (*.png *.jpg *.jpeg *.bmp)')
        if path:self.ab_paths[side]=path;self.ab_output.setPlainText(json.dumps(self.ab_paths,indent=2))
    def save_ab(self):
        if not self.game:return
        conditions={k:v.text().strip() for k,v in self.ab_conditions.items()}
        paths=dict(self.ab_paths);notes=self.ab_notes.text();game_id=self.game.id
        self.run('ab',lambda:self.s.visual_comparisons.create(game_id,paths,conditions,notes),lambda result:self.ab_output.setPlainText(json.dumps(result,indent=2,ensure_ascii=False)))
