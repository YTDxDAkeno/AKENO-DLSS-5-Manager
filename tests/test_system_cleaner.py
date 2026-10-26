"""Smart Cleaner: only what was scanned, confirmed and is still unchanged leaves the disk.

The Windows environment is simulated with folders below tmp_path (LOCALAPPDATA, TEMP, SystemRoot...). The real
Recycle Bin and file locks are Windows behaviour these tests stand in for (``fs.RecycleBin`` falls back to AKENO's
trash folder elsewhere)."""

import os
import time

import pytest

from akeno.system import fs
from akeno.system.cleaner import diskspace, engine, rules
from akeno.system.cleaner.service import CleanerService, steam_libraries
from akeno.system.store import SystemStore
from akeno.system.tasks import Cancelled, TaskContext

OLD = time.time() - 30 * 86400


def _file(path, data=b"x" * 100, age=OLD):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (age, age))
    return path


class Bin:
    """Records removals instead of using the Recycle Bin."""

    undoable = True

    def __init__(self, supports=True, fail=None):
        self.removed = []
        self._supports = supports
        self.fail = fail or {}

    def supports(self, path):
        return self._supports

    def remove(self, path):
        if path in self.fail:
            raise self.fail[path]
        os.unlink(path)
        self.removed.append(path)


@pytest.fixture
def pc(tmp_path):
    env = {"LOCALAPPDATA": str(tmp_path / "Local"), "APPDATA": str(tmp_path / "Roaming"),
           "TEMP": str(tmp_path / "Local" / "Temp"), "SystemRoot": str(tmp_path / "Windows"),
           "ProgramFiles": str(tmp_path / "Program Files"), "USERPROFILE": str(tmp_path / "User")}
    home = tmp_path / "AKENO"
    _file(tmp_path / "Local" / "Temp" / "old.tmp")
    _file(tmp_path / "Local" / "Temp" / "sub" / "old2.tmp")
    _file(tmp_path / "Local" / "Temp" / "fresh.tmp", age=time.time())
    _file(tmp_path / "Windows" / "Temp" / "win.tmp")
    _file(tmp_path / "Windows" / "System32" / "kernel32.dll")
    _file(tmp_path / "Local" / "NVIDIA" / "DXCache" / "shader.bin")
    chrome = tmp_path / "Local" / "Google" / "Chrome" / "User Data" / "Default"
    _file(chrome / "Cache" / "Cache_Data" / "f_000001")
    _file(chrome / "Cookies", b"secret")
    _file(chrome / "Login Data", b"secret")
    _file(chrome / "Local Storage" / "leveldb" / "000003.log", b"secret")
    _file(home / "cache" / "staging" / "pkg" / "OptiScaler.dll")
    _file(home / "cache" / "0123456789abcdef-pkg.zip")
    _file(home / "cache" / "components" / "x.partial")
    _file(home / "logs" / "akeno.log.1")
    _file(home / "logs" / "akeno.log", age=time.time())
    _file(tmp_path / "User" / "Downloads" / "big.iso", b"\0" * 2048)
    cats = rules.categories(env, [], str(home), large_roots=[str(tmp_path / "User" / "Downloads")])
    # large files: 1 KB is "large" for the test
    cats = [rules.Category(**{**c.__dict__, "min_size": 1024}) if c.id == "large_files" else c for c in cats]
    return env, home, cats, tmp_path


def _scan(cats, env, running=()):
    return engine.scan(cats, env=env, running=set(running))


def test_scan_lists_only_allowed_old_files(pc):
    env, home, cats, root = pc
    res = _scan(cats, env)
    temp = res.get("user_temp")
    names = sorted(os.path.basename(f.path) for f in temp.files)
    assert names == ["old.tmp", "old2.tmp"] and temp.young == 1
    chrome = res.get("browser_google_chrome")
    assert [os.path.basename(f.path) for f in chrome.files] == ["f_000001"], "cookies and logins are never listed"
    all_paths = {f.path for c in res.categories for f in c.files}
    assert not any("System32" in p or "Cookies" in p or "Login Data" in p for p in all_paths)
    assert {os.path.basename(f.path) for f in res.get("akeno_temp").files} == {"OptiScaler.dll"}
    assert [os.path.basename(f.path) for f in res.get("akeno_package_cache").files] == ["0123456789abcdef-pkg.zip"]
    assert res.get("akeno_partial").files == [], "folders in keep_dirs are not entered"
    assert [os.path.basename(f.path) for f in res.get("akeno_logs").files] == ["akeno.log.1"]
    assert res.get("large_files").category.review_only and res.get("large_files").count == 1


def test_defaults_are_safe(pc):
    env, home, cats, root = pc
    by = {c.id: c for c in cats}
    assert by["user_temp"].preselect and not by["shader_nvidia"].preselect and by["shader_nvidia"].shader
    assert not by["akeno_downloads"].preselect and not by["crash_dumps"].preselect
    assert by["large_files"].review_only and not by["large_files"].preselect
    assert all(not c.preselect for c in cats if c.risk != rules.RISK_LOW)


def test_path_gate_blocks_windows_program_files_and_traversal(pc):
    env, home, cats, root = pc
    temp = (str(root / "Local" / "Temp"),)
    assert rules.path_allowed(str(root / "Local" / "Temp" / "a.tmp"), temp, env)
    assert not rules.path_allowed(str(root / "Local" / "Temp"), temp, env), "never a root itself"
    assert not rules.path_allowed(str(root / "Local" / "Temp" / ".." / "x.dat"), temp, env)
    win = (str(root / "Windows"),)
    assert not rules.path_allowed(str(root / "Windows" / "System32" / "kernel32.dll"), win, env)
    assert rules.path_allowed(str(root / "Windows" / "Temp" / "win.tmp"), (str(root / "Windows" / "Temp"),), env)
    pf = (str(root / "Program Files"),)
    assert not rules.path_allowed(str(root / "Program Files" / "a.tmp"), pf, env)
    assert not rules.path_allowed(str(root / "Local" / "Temp" / "desktop.ini"), temp, env)
    assert not rules.path_allowed(str(root / "Local" / "Temp" / "Cookies" / "c"), temp, env)


def test_symlinks_and_junction_like_links_are_not_followed_or_removed(pc):
    env, home, cats, root = pc
    outside = _file(root / "Precious" / "photo.jpg")
    try:
        os.symlink(root / "Precious", root / "Local" / "Temp" / "linkdir", target_is_directory=True)
        os.symlink(outside, root / "Local" / "Temp" / "link.jpg")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted")
    res = _scan(cats, env)
    paths = [f.path for f in res.get("user_temp").files]
    assert not any("link" in os.path.basename(p) or "photo" in p for p in paths)
    rep = engine.clean(res, ["user_temp"], remover=Bin(), env=env, running=set())
    assert outside.exists() and (root / "Local" / "Temp" / "link.jpg").is_symlink()
    assert rep.removed == 2


def test_clean_removes_only_confirmed_categories(pc, tmp_path):
    env, home, cats, root = pc
    res = _scan(cats, env)
    store = SystemStore(tmp_path / "s.sqlite3")
    b = Bin()
    rep = engine.clean(res, ["user_temp"], store=store, remover=b, env=env, running=set())
    assert rep.removed == 2 and len(b.removed) == 2
    assert all("Temp" in p for p in b.removed)
    assert (root / "Windows" / "Temp" / "win.tmp").exists() and (root / "Local" / "NVIDIA" / "DXCache" /
                                                                 "shader.bin").exists()
    assert (root / "Local" / "Temp" / "fresh.tmp").exists()
    journal = store.actions("cleaner")
    assert journal[0]["kind"] == "clean.user_temp" and journal[0]["state"] == "done"
    store.close()


def test_file_changed_between_scan_and_clean_is_skipped(pc):
    env, home, cats, root = pc
    res = _scan(cats, env)
    target = root / "Local" / "Temp" / "old.tmp"
    target.write_bytes(b"new content written after the scan")
    rep = engine.clean(res, ["user_temp"], remover=Bin(), env=env, running=set())
    assert target.exists() and rep.skipped.get("changed") == 1 and rep.removed == 1


def test_files_in_use_and_denied_are_skipped(pc, monkeypatch):
    env, home, cats, root = pc
    res = _scan(cats, env)
    locked = str(root / "Local" / "Temp" / "old.tmp")
    monkeypatch.setattr(fs, "is_locked", lambda p: str(p) == locked)
    denied = str(root / "Local" / "Temp" / "sub" / "old2.tmp")
    rep = engine.clean(res, ["user_temp"], remover=Bin(fail={denied: PermissionError("Access is denied")}), env=env,
                       running=set())
    assert rep.removed == 0 and rep.skipped == {"in_use": 1, "denied": 1}
    assert os.path.exists(locked) and os.path.exists(denied)


def test_shader_cache_needs_extra_confirmation(pc):
    env, home, cats, root = pc
    res = _scan(cats, env)
    pv = engine.preview(res, ["shader_nvidia"])
    assert pv.needs_shader_confirmation
    rep = engine.clean(res, ["shader_nvidia"], remover=Bin(), env=env, running=set())
    assert rep.removed == 0 and (root / "Local" / "NVIDIA" / "DXCache" / "shader.bin").exists()
    rep = engine.clean(res, ["shader_nvidia"], remover=Bin(), env=env, running=set(), confirm_shaders=True)
    assert rep.removed == 1


def test_browser_running_blocks_its_cache(pc):
    env, home, cats, root = pc
    res = _scan(cats, env, running={"chrome.exe"})
    c = res.get("browser_google_chrome")
    assert c.status == "app_running" and c.running == ["chrome.exe"]
    assert engine.preview(res, ["browser_google_chrome"]).blocked
    rep = engine.clean(res, ["browser_google_chrome"], remover=Bin(), env=env, running={"chrome.exe"})
    assert rep.removed == 0 and rep.skipped["app_running"] == 1


def test_large_files_are_never_cleaned(pc):
    env, home, cats, root = pc
    res = _scan(cats, env)
    assert engine.preview(res, ["large_files"]).blocked
    rep = engine.clean(res, ["large_files"], remover=Bin(), env=env, running=set())
    assert rep.removed == 0 and (root / "User" / "Downloads" / "big.iso").exists()


def test_cancelled_scan_and_clean(pc):
    env, home, cats, root = pc
    ctx = TaskContext()
    ctx.cancel()
    with pytest.raises(Cancelled):
        engine.scan(cats, ctx, env=env, running=set())
    res = _scan(cats, env)
    rep = engine.clean(res, ["user_temp"], ctx, remover=Bin(), env=env, running=set())
    assert rep.cancelled and rep.removed == 0


def test_no_recycle_bin_on_drive_is_reported_not_forced(pc):
    env, home, cats, root = pc
    res = _scan(cats, env)
    rep = engine.clean(res, ["user_temp"], remover=Bin(supports=False), env=env, running=set())
    assert rep.removed == 0 and rep.skipped == {"no_bin": 2}


def test_permanent_mode_is_explicit(pc):
    env, home, cats, root = pc
    res = _scan(cats, env)
    assert engine.preview(res, ["user_temp"], permanent=True).permanent
    rep = engine.clean(res, ["user_temp"], permanent=True, env=env, running=set())
    assert rep.permanent and rep.removed == 2 and not (root / "Local" / "Temp" / "old.tmp").exists()


def test_free_space_measured_before_and_after(pc):
    env, home, cats, root = pc
    res = _scan(cats, env)
    rep = engine.clean(res, ["user_temp"], remover=Bin(), env=env, running=set())
    assert rep.free_before and set(rep.free_before) == set(rep.free_after)


def test_recycle_bin_fallback_moves_to_akeno_trash(tmp_path):
    if os.name == "nt":
        pytest.skip("the real Recycle Bin is used on Windows")
    f = _file(tmp_path / "a.tmp")
    b = fs.RecycleBin(tmp_path / "trash")
    b.remove(str(f))
    assert not f.exists() and len(list((tmp_path / "trash").iterdir())) == 1


# -- disk space ---------------------------------------------------------------------------------------------------
def test_folder_sizes_and_largest(tmp_path):
    _file(tmp_path / "a" / "1.bin", b"\0" * 5000)
    _file(tmp_path / "a" / "deep" / "2.bin", b"\0" * 3000)
    _file(tmp_path / "b" / "3.bin", b"\0" * 100)
    _file(tmp_path / "top.bin", b"\0" * 10)
    rep = diskspace.folder_sizes(str(tmp_path))
    assert rep.total == 8110 and rep.files == 4
    assert [(c.name, c.bytes) for c in rep.children] == [("a", 8000), ("b", 100), ("top.bin", 10)]
    assert rep.largest[0][0] == 5000


def test_duplicates_are_byte_identical(tmp_path):
    data = os.urandom(2 << 20)
    _file(tmp_path / "x" / "one.bin", data)
    _file(tmp_path / "y" / "two.bin", data)
    _file(tmp_path / "z" / "same_size.bin", data[:-1] + bytes([data[-1] ^ 1]))
    groups, errors = diskspace.duplicates(str(tmp_path), min_size=1 << 20)
    assert len(groups) == 1 and len(groups[0].paths) == 2 and groups[0].wasted == len(data)


def test_game_sizes_count_shared_folder_once(tmp_path):
    from types import SimpleNamespace as G
    _file(tmp_path / "g1" / "game.exe", b"\0" * 1000)
    games = [G(name="A", install_dir=str(tmp_path / "g1"), launcher="steam"),
             G(name="B", install_dir=str(tmp_path / "g1"), launcher="steam"),
             G(name="C", install_dir=str(tmp_path / "missing"), launcher="epic")]
    out = diskspace.game_sizes(games)
    assert len(out) == 1 and out[0]["bytes"] == 1000


def test_steam_library_folders_from_games():
    from types import SimpleNamespace as G
    games = [G(launcher="steam", install_dir=os.path.join("D:" + os.sep, "SteamLibrary", "steamapps", "common", "X")),
             G(launcher="epic", install_dir="E:/Epic/Y")]
    assert steam_libraries(games) == [os.path.join("D:" + os.sep, "SteamLibrary")]


# -- maintenance --------------------------------------------------------------------------------------------------
def test_maintenance_is_off_by_default_and_never_cleans(pc, tmp_path):
    env, home, cats, root = pc
    store = SystemStore(tmp_path / "s.sqlite3")
    svc = CleanerService(store, env=env, akeno_home=str(home))
    assert not svc.maintenance_due()
    svc.set_maintenance(7)
    assert svc.maintenance_due()
    out = svc.maintenance_scan()
    assert out["bytes"] > 0 and (root / "Local" / "Temp" / "old.tmp").exists(), "a check never removes anything"
    assert not svc.maintenance_due()
    assert svc.maintenance_due(now=time.time() + 8 * 86400)
    with pytest.raises(ValueError):
        svc.set_maintenance(3)
    store.close()
