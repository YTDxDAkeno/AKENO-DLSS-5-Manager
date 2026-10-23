"""License/source pins and optional Windows-built dependency validation."""
import json
import os
from pathlib import Path
import pytest
from akeno.paths import resource_dir
from akeno.security.downloads import sha256_file
from akeno.pipeline.manifests import ComponentStore

ROOT=Path(__file__).resolve().parents[1]


def test_vendored_motion_provider_has_corresponding_source_and_license():
    pins=json.loads((ROOT/'community/vendor/pins.json').read_text())
    for name,pin in pins['files'].items():assert sha256_file(ROOT/name)==pin
    assert 'GNU GENERAL PUBLIC LICENSE' in (ROOT/'community/vendor/dh-motion/LICENSE').read_text()
    shader=(ROOT/'community/vendor/dh-motion/dh_uber_motion.fx').read_text()
    assert 'texMotionVectors' in shader and 'PS_MotionPass' in shader and 'DH_UBER_MOTION_020' in shader
    assert (ROOT/'community/licenses/ReShade-BSD-3-Clause.txt').is_file()
    assert 'CC0-1.0' in (ROOT/'community/vendor/reshade-framework/ReShade.fxh').read_text()


@pytest.mark.skipif(os.environ.get('AKENO_TEST_REAL_BUNDLE')!='1',reason='Requires the actual Windows CI full add-on build')
def test_real_distributable_contains_full_addon_and_source_without_nvidia(db):
    from tools.check_public_payload import check
    root=resource_dir()/'community/dependencies';data=json.loads((root/'manifest.json').read_text())
    result=ComponentStore(db).import_manifest(root/'manifest.json',True)
    assert result['reshade']['version']=='6.8.0'
    assert result['motion_provider']['provider_id']==0 and result['motion_provider']['assets']
    source=data['corresponding_source'];assert sha256_file(root/source['path'])==source['sha256']
    assert check(root)>8
