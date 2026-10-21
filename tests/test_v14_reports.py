"""Measured fixtures validate reports and isolation, never real hardware or FiveM compatibility."""
import json
from dataclasses import replace
from pathlib import Path
import pytest
from akeno.benchmark.service import BenchmarkService
from akeno.diagnostics.privacy import redact
from akeno.fivem.development import validation_plan, write_kit
from akeno.fivem.discovery import inspect_installation
from akeno.fivem.models import ServerPolicy
from test_fivem_discovery import installation
from helpers import rtx


def test_measurement_reports_redact_paths_escape_html_and_flag_unknown_conditions(db, tmp_path):
    svc = BenchmarkService(db, None)
    svc.record('game', '<script>alert(1)</script>', [10.0]*100,
        {'gpu': 'RTX 5090', 'driver': '617.42', 'neural_config': {'source_path': r'C:\Users\private\file.dll'}},
        'fixture', '/home/private/capture.csv')
    svc.record('game', 'experimental', [20.0]*100, {'gpu': 'RTX 5090', 'driver': '617.42'}, 'fixture', None)
    sessions = svc.sessions('game')
    assert any('unknown' in w for w in svc.comparable(*sessions))
    svc.export_json(sessions, str(tmp_path/'report.json'))
    svc.export_html(sessions, str(tmp_path/'report.html'))
    assert 'private' not in (tmp_path/'report.json').read_text()
    assert '<script>' not in (tmp_path/'report.html').read_text()
    assert '&lt;script&gt;' in (tmp_path/'report.html').read_text()
    assert 'private' not in (tmp_path/'report.html').read_text()


def test_comparison_requires_same_capture_conditions(db):
    svc = BenchmarkService(db, None)
    meta = {'gpu': 'RTX 5090', 'driver': '617.42', 'resolution': '3840x2160',
        'neural_config': {'capture_conditions': {'game_version':'1', 'workload':'scene A', 'settings':'High'}}}
    svc.record('game','baseline',[10.0]*100,meta,'fixture',None)
    svc.record('game','experimental',[11.0]*100,meta,'fixture',None)
    sessions = svc.sessions('game')
    assert not svc.comparable(*sessions)
    sessions[0]['neural_config']['capture_conditions']['workload'] = 'scene B'
    assert any('workload' in w for w in svc.comparable(*sessions))


def test_fivem_first_test_does_not_require_prior_nr_success(installation, tmp_path):
    root, app, gta = installation
    i = inspect_installation(root)
    p = ServerPolicy('private-server', True, 'operator approval for exact plugin', True, 0, False,
        frozenset({'dlss5_feeder'}), experimental_consent=True)
    report = validation_plan(i, p, rtx(), 'dlss5_feeder', 'https://example.org/documented-fixture-interface', True, True)
    assert report['first_test_eligible'] and not report['production_approved']
    assert not report['neural_execution_verified']
    before = {str(f): f.read_bytes() for f in root.rglob('*') if f.is_file()}
    kit = write_kit(tmp_path/'isolated-kit', report)
    assert (kit/'validation-plan.json').is_file()
    assert before == {str(f): f.read_bytes() for f in root.rglob('*') if f.is_file()}
    assert not validation_plan(i, replace(p, pure_level=2), rtx(), 'dlss5_feeder', 'https://example.org/contract', True, True)['first_test_eligible']
    assert not validation_plan(i, p, rtx(), 'dlss5_feeder')['first_test_eligible']


def test_sensor_absence_is_not_fabricated(monkeypatch):
    from akeno.hardware import sensors
    monkeypatch.setattr(sensors, 'is_windows', lambda: False)
    assert all(v is None for v in sensors.sample(rtx()).values())
