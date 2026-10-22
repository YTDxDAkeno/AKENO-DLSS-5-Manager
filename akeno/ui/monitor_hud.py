"""Opt-in desktop Qt HUD. Reuses collected evidence; never injects or reads a game process."""
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QDialog,QVBoxLayout
from akeno.i18n import t
from akeno.ui.widgets import label,button


class MonitorHud(QDialog):
    def __init__(self,report_provider,parent=None):
        super().__init__(parent,Qt.WindowType.Tool|Qt.WindowType.WindowStaysOnTopHint)
        self.provider=report_provider;self.setWindowTitle(t('AKENO monitoring HUD'));self.resize(430,280)
        layout=QVBoxLayout(self);self.output=label('',wrap=True);layout.addWidget(self.output)
        layout.addWidget(label(t('Desktop window for windowed/borderless games. FPS and sensors show the last completed capture, not instantaneous telemetry.'),'dim',wrap=True))
        layout.addWidget(button(t('Stop HUD'),on_click=self.close))
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.refresh);self.finished.connect(lambda _:self.timer.stop());self.timer.start();self.refresh()
    def refresh(self):
        report=self.provider() or {};session=report.get('session',{});neural=report.get('neural',{})
        rows=[t('NR status')+': '+t(session.get('state','Not verified')),
              t('Backend')+': '+t('Loaded' if neural.get('stages',{}).get('backend_loaded') else 'Not verified'),
              t('Successful NR API evaluations')+': '+str(neural.get('counters',{}).get('eval_success',t('Not measured')))]
        captures=(report.get('benchmark_groups') or {}).get('current_session') or []
        capture=max(captures,key=lambda c:c.get('id',0)) if captures else {}
        stats=capture.get('stats') or {};measurement=(capture.get('neural_config') or {}).get('measurement') or {}
        fps=stats.get('application_rendered_fps');display=stats.get('displayed_fps');generated=stats.get('generated_frame_share')
        rows.extend([t('Last captured application FPS')+': '+(str(fps) if fps is not None and measurement.get('reliable') else t('Not measured')),
                     t('Displayed FPS')+': '+(str(display) if display is not None else t('Not measured')),
                     t('Generated-frame share')+': '+(f'{generated*100:.1f}%' if generated is not None else t('Not measured')),
                     t('Frame time')+': '+str(stats.get('avg_frametime_ms',t('Not measured')))])
        samples=(capture.get('neural_config') or {}).get('sensor_samples') or []
        sensor=samples[-1] if samples else {}
        rows.extend([t('GPU usage')+': '+str(sensor.get('gpu_usage_percent') if sensor.get('gpu_usage_percent') is not None else t('Not measured')),
                     t('VRAM used (MiB)')+': '+str(sensor.get('vram_used_mib') if sensor.get('vram_used_mib') is not None else t('Not measured')),
                     t('Capture time')+': '+str(capture.get('started_at',t('Not measured')))])
        text='\n'.join(rows)
        if text!=self.output.text():self.output.setText(text)
        if session.get('ended'):self.timer.stop()
