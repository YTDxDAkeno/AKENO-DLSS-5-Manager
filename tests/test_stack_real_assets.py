"""Contract tests against the original upstream release archives (pinned in akeno/data/stack/components.json).

Set AKENO_REAL_ASSETS_DIR to a folder holding the original assets (``python tools/validate-stack-catalog.py
--download DIR`` fetches them; CI caches them). Without it these tests are skipped, never faked.
The real archives are installed into temporary fixture game folders; no game and no GPU is involved.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = os.environ.get("AKENO_REAL_ASSETS_DIR")
pytestmark = pytest.mark.skipif(not ASSETS, reason="AKENO_REAL_ASSETS_DIR not set (original release assets)")


def test_every_pinned_asset_matches_its_contract():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "validate-stack-catalog.py"), "--archive-dir", ASSETS],
                       capture_output=True, text=True, timeout=900)
    report = json.loads(r.stdout)
    assert report["problems"] == [] and r.returncode == 0
    assert len(report["checked"]) == report["versions"]


@pytest.fixture
def real(tmp_path, db, monkeypatch):
    """Services whose downloads come from the local copies of the original assets (same bytes, same URLs)."""
    from akeno.stack.catalog import StackCatalog
    from akeno.stack.service import StackManager
    from akeno.stack.sources import DOWNLOAD_HOSTS, ComponentStore
    from akeno.security.downloads import DownloadManager
    from stack_fixtures import Server, services
    svc, fx = services(tmp_path, db, monkeypatch)
    server = Server()
    cat = StackCatalog.load()
    for c in cat.components.values():
        for v in c.versions:
            if v.source == "download":
                server.files[v.url] = (Path(ASSETS) / v.asset).read_bytes()
    store = ComponentStore(db, cat, DownloadManager(DOWNLOAD_HOSTS, cache=tmp_path / "real-dl", opener=server),
                           root=tmp_path / "real-components")
    svc.stack = StackManager(svc, catalog=cat, store=store)
    return svc


def test_real_skynizz_and_rtx40_model_install_and_restore(real, tmp_path):
    from stack_fixtures import dlss_game, snapshot
    svc = real
    game, exe_dir = dlss_game(svc, tmp_path)
    before = snapshot(exe_dir)
    res = svc.stack.install(svc.stack.prepare(game.id, "optiscaler", preset="quality"))
    assert res.ok, res.messages
    ini = (exe_dir / "OptiScaler.ini").read_text(encoding="utf-8")
    # the shipped 78 kB template keeps its comments; the Quality preset is the upstream installer default
    assert "[DlssNr]" in ini and "PreSr=true" in ini and "WorkingScale=0.67" in ini and "Dx12Upscaler=dlss" in ini
    assert (exe_dir / "winmm.dll").stat().st_size == 26570240           # OptiScaler.dll 1.3.0 as the proxy
    assert (exe_dir / "nvngx_dlssnr.dll").stat().st_size == 165830144   # 310.8.2 SF-v2 (RTX 20-50)
    assert svc.stack.uninstall(game.id).ok
    assert snapshot(exe_dir) == before


def test_real_feeder_renodx_stack_layout(real, tmp_path):
    from stack_fixtures import plain_game
    svc = real
    game, root = plain_game(svc, tmp_path)
    res = svc.stack.install(svc.stack.prepare(game.id, "feeder", preset="balanced"))
    assert res.ok, res.messages
    assert (root / "dlss5-feed.addon64").stat().st_size == 343552           # Feeder 1.19.0, in-process add-on
    assert (root / "reshade-shaders" / "Shaders" / "DLSS5_Feed.fx").is_file()
    assert (root / "renodx-dlss5.addon64").stat().st_size == 1921024         # RenoDX DLSS 5 7.0.0-rc8
    assert (root / "nvngx_dlss.dll").stat().st_size == 74187376              # DLSS SR 310.9.16
    assert not (root / "dlss5-feed-helper.addon64").exists()
    assert "NRResolutionScale=0.67" in (root / "ReShade.ini").read_text()
    assert svc.stack.uninstall(game.id).ok
    assert sorted(p.name for p in root.iterdir()) == ["Racer.exe"]


def test_real_fivem_stack(real, tmp_path, monkeypatch):
    from stack_fixtures import fivem_install
    monkeypatch.setenv("COMPUTERNAME", "AKENO-PC")
    svc = real
    game, root, app, gta = fivem_install(svc, tmp_path)
    res = svc.stack.install(svc.stack.prepare(game.id, "fivem", preset="performance"))
    assert res.ok, res.messages
    plugins = app / "plugins"
    assert (plugins / "dlss5-feed.addon64").stat().st_size == 343552
    assert (plugins / "renodx-dlss5.addon64").is_file() and (plugins / "nvngx_dlssnr.dll").is_file()
    assert "ReShade5=ID:" in (app / "CitizenFX.ini").read_text()
    assert svc.stack.uninstall(game.id).ok
    assert not any(plugins.iterdir()) if plugins.exists() else True
    shutil.rmtree(tmp_path / "real-components", ignore_errors=True)
