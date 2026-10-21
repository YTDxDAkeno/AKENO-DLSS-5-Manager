"""Dependency resolver: discovery-driven plans, lawful acquisition, failure modes and honest states."""

import pytest

from akeno.backends import runtimes
from akeno.backends.runtimes import DLSS_RUNTIME, NR_RUNTIME, SearchRoot
from akeno.compat.engine import CompatDatabase
from akeno.deps import resolver as R
from akeno.install.transaction import Installer
from akeno.security.catalog import Catalog
from akeno.security.downloads import CancelToken, Cancelled, DownloadError
from akeno.services import AppServices
from helpers import FakeDownloads, make_opti_package, make_pe, rtx, system_with
from test_runtimes import fake_verifier, nv_dll


class ScriptedDownloads(FakeDownloads):
    """FakeDownloads with an offline / corrupt / cancel switch."""

    mode = "ok"

    def fetch(self, url, sha256, name, expected_size=None, progress=None, token=None):
        if self.mode == "offline":
            raise DownloadError("Download failed after 3 attempts: <urlopen error [Errno 11001] getaddrinfo failed>")
        if self.mode == "cancel":
            raise Cancelled("Download cancelled.")
        if self.mode == "corrupt":
            self.files[url] = self.files[url].with_name("corrupt.zip")
            self.files[url].write_bytes(b"PK\x03\x04 not the reviewed release")
        if progress:
            progress(10, 20)
            progress(20, 20)
        return super().fetch(url, sha256, name, expected_size, progress, token)


@pytest.fixture
def env(tmp_path, db, monkeypatch):
    zpath, entry = make_opti_package(tmp_path)
    catalog = Catalog({"schema": 1, "allowed_hosts": ["github.com"], "packages": [entry], "tools": []})
    downloads = ScriptedDownloads({entry["url"]: zpath}, tmp_path / "dlcache")
    installer = Installer(db, tmp_path / "backups", running_check=lambda d: [])
    svc = AppServices(db=db, catalog=catalog, downloads=downloads, installer=installer,
                      compat_db=CompatDatabase(CompatDatabase.load().data, "test"))
    svc.system = system_with(rtx("NVIDIA GeForce RTX 5090", device=0x2B85, vram_gb=32))
    svc.deps.verifier = fake_verifier
    game_dir = tmp_path / "games" / "Night City"
    make_pe(game_dir / "bin" / "x64" / "NightCity.exe", ["d3d12.dll", "kernel32.dll"], pad=50000)
    make_pe(game_dir / "bin" / "x64" / "nvngx_dlss.dll", [], dll=True, version=(310, 2, 1, 0))
    game = svc.library.add_manual(str(game_dir), "Night City")
    driver = tmp_path / "DriverStore" / "nv_dispi.inf_amd64_1"
    roots = {"driver": driver}

    def fake_roots(file_name, game_dirs=(), user_dirs=True):
        return [SearchRoot(str(roots["driver"]), "driver_store", 3, (file_name.lower(),))] + \
               [SearchRoot(d, "game", 0, (file_name.lower(),)) for d in game_dirs]
    monkeypatch.setattr(runtimes, "search_roots", fake_roots)
    return svc, game, game_dir, driver, downloads


def test_plan_before_search_is_unknown_and_never_passed(env):
    svc, game, *_ = env
    plan = svc.dependency_plan(game)
    rt = plan.get("nr_runtime")
    assert rt.state == R.UNKNOWN and rt.badge == "pending"
    assert plan.get("package").state == R.DOWNLOAD and plan.get("package").badge == "info"
    assert not plan.ready and not plan.ready_after_resolve
    assert plan.get("dlss_runtime").state == R.OK and not plan.get("dlss_runtime").required, "game has its own DLSS"


def test_missing_runtime_is_blocked_with_driver_guidance(env):
    svc, game, *_ = env
    svc.discover_dependencies()
    rt = svc.dependency_plan(game).get("nr_runtime")
    assert rt.state == R.BLOCKED and "no separate download" in rt.detail
    assert any(g.url == R.NVIDIA_DRIVERS_URL for g in rt.guide)
    assert any(g.action == "import_nr_runtime" for g in rt.guide)
    rd = svc.readiness(game)
    assert not rd.can_install and not rd.can_resolve


def test_resolve_imports_driver_runtime_downloads_package_and_install_succeeds(env):
    svc, game, game_dir, driver, downloads = env
    nv_dll(driver / NR_RUNTIME, version=(310, 8, 0, 0))
    svc.discover_dependencies()
    plan = svc.dependency_plan(game)
    assert plan.get("nr_runtime").state == R.LOCAL and plan.ready_after_resolve and not plan.ready
    rd = svc.readiness(game)
    assert rd.can_resolve and not rd.can_install
    runtime_item = next(i for i in rd.items if i.key == "runtime")
    assert runtime_item.state == "info", "found but not imported must not be green"
    steps = []
    res = svc.resolve_dependencies(game, progress=lambda k, n, t: steps.append(k))
    assert res.ok, res.messages
    assert res.plan.ready and "package" in steps
    assert svc.runtime_lookup(NR_RUNTIME)["version"] == "310.8.0.0"
    assert any("NVIDIA signature valid" in m for m in res.messages)
    assert svc.readiness(game).can_install
    # the resolved dependencies are enough for the real transactional install
    adapter = svc.backend_for(game)
    pkg = svc.package_for(adapter)
    plan_i = svc.plan_install(game, adapter, pkg, dict(adapter.default_params(game), _profile="quality"),
                              svc.stage(game, adapter, pkg))
    assert svc.install(game, plan_i).ok
    assert (game_dir / "bin" / "x64" / NR_RUNTIME).read_bytes() == (driver / NR_RUNTIME).read_bytes()


def test_modified_runtime_is_refused_and_reported(env):
    svc, game, _, driver, _ = env
    nv_dll(driver / NR_RUNTIME, marker=b"MODIFIED")
    svc.discover_dependencies()
    rt = svc.dependency_plan(game).get("nr_runtime")
    assert rt.state == R.BLOCKED and "never installed" in rt.detail
    res = svc.resolve_dependencies(game)
    assert not res.ok and svc.runtime_lookup(NR_RUNTIME) is None


def test_offline_download_keeps_state_and_explains(env):
    svc, game, _, driver, downloads = env
    nv_dll(driver / NR_RUNTIME)
    downloads.mode = "offline"
    res = svc.resolve_dependencies(game)
    assert not res.ok
    assert any("download failed" in m for m in res.messages)
    pkg = res.plan.get("package")
    assert pkg.state == R.DOWNLOAD and "Last attempt failed" in pkg.detail
    assert res.plan.get("nr_runtime").state == R.OK, "local import works offline"
    downloads.mode = "ok"
    assert svc.resolve_dependencies(game).ok


def test_corrupt_download_is_discarded(env):
    svc, game, _, driver, downloads = env
    nv_dll(driver / NR_RUNTIME)
    downloads.mode = "corrupt"
    res = svc.resolve_dependencies(game)
    assert not res.ok and any("corrupt" in m for m in res.messages)
    assert res.plan.get("package").state == R.DOWNLOAD
    assert not any(p.is_file() and p.stat().st_size < 64 for p in (svc.downloads.cache).glob("*"))


def test_cancel_stops_without_changes(env):
    svc, game, game_dir, driver, downloads = env
    before = sorted(p.name for p in game_dir.rglob("*"))
    downloads.mode = "cancel"
    res = svc.resolve_dependencies(game)
    assert res.cancelled and not res.ok
    tok = CancelToken()
    tok.cancel()
    assert svc.resolve_dependencies(game, token=tok).cancelled
    assert sorted(p.name for p in game_dir.rglob("*")) == before


def test_unavailable_package_is_blocked(env):
    svc, game, *_ = env
    pkg = svc.package_for(svc.backend_for(game))
    pkg.url = None
    dep = svc.dependency_plan(game).get("package")
    assert dep.state == R.BLOCKED and "Not a reviewed" in dep.detail


def test_runtime_from_another_game_and_dlss_requirement_without_own_dlss(env, tmp_path):
    svc, game, game_dir, driver, _ = env
    # second game ships NVIDIA's nvngx_dlss.dll; target game has none -> required dlss_runtime, found locally
    other = tmp_path / "games" / "Other"
    make_pe(other / "Other.exe", ["d3d12.dll"], pad=1000)
    nv_dll(other / DLSS_RUNTIME, original=DLSS_RUNTIME, version=(310, 4, 0, 0))
    svc.library.add_manual(str(other), "Other")
    (game_dir / "bin" / "x64" / "nvngx_dlss.dll").unlink()
    make_pe(game_dir / "bin" / "x64" / "libxess.dll", [], dll=True)
    game = svc.library.reanalyze(game.id)
    svc.discover_dependencies()
    assert "dlss" not in game.upscaler_inputs
    dep = svc.dependency_plan(game).get("dlss_runtime")
    assert dep.required and dep.state == R.LOCAL and "installed games" in dep.detail


def test_feeder_dependencies_are_guided_not_faked(env):
    svc, game, *_ = env
    svc.set_backend(game, "dlss5_feeder")
    svc.discover_dependencies()
    plan = svc.dependency_plan(game)
    assert plan.get("reshade").state == R.MANUAL and plan.get("reshade").guide[0].url == R.RESHADE_URL
    assert plan.get("consumer").state == R.MANUAL
    assert not plan.ready_after_resolve


def test_runtime_note_reports_documented_failure_on_older_gpu(env):
    svc, game, _, driver, _ = env
    svc.system = system_with(rtx())        # RTX 3080
    nv_dll(driver / NR_RUNTIME, version=(310, 8, 0, 0))
    svc.discover_dependencies()
    dep = svc.dependency_plan(game).get("nr_runtime")
    assert "refuse" in dep.note and "RTX 30" in dep.note


def test_messages_translate_completely(env):
    from akeno import i18n
    from akeno.i18n import t
    svc, game, *_ = env
    svc.discover_dependencies()
    dep = svc.dependency_plan(game).get("nr_runtime")
    i18n.set_language("de")
    try:
        text = t(dep.detail)
        assert "Auf diesem PC nicht gefunden" in text and "nvngx_dlssnr.dll" in text
    finally:
        i18n.set_language("en")
