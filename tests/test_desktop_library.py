"""The wallpaper library and its thumbnails, with real media: pictures, an animated GIF, an H.264 MP4 and a VP9
WebM and an animated GIF (tests/fixtures/media, test patterns generated with ffmpeg), a broken file, folders and Lively packages."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from akeno.desktop import config as cfgmod
from akeno.desktop import library as libmod
from akeno.desktop import thumbs

MEDIA = Path(__file__).resolve().parent / "fixtures" / "media"


def picture(path: Path, color: str = "#4E31D5", size=(1920, 1080)) -> Path:
    from PySide6.QtGui import QColor, QImage
    img = QImage(size[0], size[1], QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path))
    return path


@pytest.fixture
def lib(akeno_home):
    return libmod.Library(akeno_home / "desktop" / "library.json")


def test_add_files_folders_duplicates_and_missing(lib, tmp_path):
    a = picture(tmp_path / "pics" / "Neon_City.png")
    copy = tmp_path / "other" / "neon copy.png"
    copy.parent.mkdir()
    shutil.copy(a, copy)
    report = lib.add_many([a, copy, tmp_path / "notes.txt", MEDIA / "sample.mp4"])
    assert [i.title for i in report.added] == ["Neon City", "sample"]
    assert len(report.duplicates) == 1 and report.duplicates[0][1].path == str(a.resolve()), "same bytes, other name"
    assert len(report.refused) == 1
    video = lib.by_path(MEDIA / "sample.mp4")
    assert video.kind == "video" and video.format == "MP4" and video.size > 0
    web = tmp_path / "pics" / "waves"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (web / "app.js").write_text("1", encoding="utf-8")
    picture(tmp_path / "pics" / "sub" / "b.jpg", "#00ff00")
    report = lib.add_folder(tmp_path / "pics")
    kinds = sorted(i.kind for i in report.added)
    assert kinds == ["image", "web"], "the web folder is one wallpaper, its files are not added one by one"
    again = libmod.Library(lib.file)
    assert len(again.items()) == 4, "saved and loaded"
    a.unlink()
    assert again.by_path(a).missing, "a missing file stays in the library, marked"


def test_query_sort_favorites_collections(lib, tmp_path):
    a = lib.add(picture(tmp_path / "alpha.png", "#111111"))
    b = lib.add(picture(tmp_path / "beta.png", "#222222"))
    c = lib.add(MEDIA / "sample.webm", title="Space loop")
    lib.update(b.id, favorite=True, collections=["Gaming"])
    assert [i.id for i in lib.query(sort="favorites")][0] == b.id
    assert [i.title for i in lib.query(sort="name")] == ["alpha", "beta", "Space loop"]
    assert [i.id for i in lib.query(kind="video")] == [c.id]
    assert [i.id for i in lib.query(text="space")] == [c.id]
    assert [i.id for i in lib.query(collection="Gaming")] == [b.id]
    assert lib.collections() == ["Gaming"]
    lib.remove(a.id)
    assert lib.get(a.id) is None and (tmp_path / "alpha.png").exists(), "removing never deletes the user's file"


def test_migration_from_playlists(lib, tmp_path):
    a = picture(tmp_path / "one.png")
    cfg = cfgmod.load()
    cfg["wallpapers"]["M1"] = dict(cfgmod.WALLPAPER_DEFAULTS, playlist=[
        {"path": str(a), "kind": "image"}, {"path": str(tmp_path / "gone.mp4"), "kind": "video"}])
    assert lib.sync_from_config(cfg) == 2
    assert lib.by_path(tmp_path / "gone.mp4").missing
    assert lib.sync_from_config(cfg) == 0, "nothing twice"


def lively_zip(path: Path, info: dict, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("LivelyInfo.json", json.dumps(info))
        for name, data in files.items():
            z.writestr(name, data)
    return path


def test_lively_packages_media_and_web_only(lib, tmp_path):
    video = lively_zip(tmp_path / "v.zip", {"Title": "Rain", "Author": "Someone", "License": "CC BY 4.0",
                                            "Type": 7, "FileName": "rain.mp4", "Thumbnail": "thumb.png"},
                       {"rain.mp4": (MEDIA / "sample.mp4").read_bytes(),
                        "thumb.png": picture(tmp_path / "t.png").read_bytes()})
    item = lib.import_lively(video, tmp_path / "lib")
    assert item.kind == "video" and item.title == "Rain" and item.author == "Someone" and item.source == "lively"
    assert Path(item.path).is_relative_to(tmp_path / "lib") and item.preview.endswith("thumb.png")
    web = lively_zip(tmp_path / "w.zip", {"Title": "Waves", "Type": "web", "FileName": "main.html"},
                     {"main.html": b"<!doctype html>", "js/app.js": b"1"})
    item = lib.import_lively(web, tmp_path / "lib")
    assert item.kind == "web" and (Path(item.path) / "index.html").is_file()
    for info, files, why in (
            ({"Type": 0, "FileName": "game.exe"}, {"game.exe": b"MZ"}, "runs a program"),
            ({"Type": 5, "FileName": "x.html"}, {"x.html": b""}, "runs a program"),
            ({"Type": 7, "FileName": "a.mp4"}, {"a.mp4": b"x", "tool.dll": b"MZ"}, "refused file"),
            ({"Type": 7, "FileName": "missing.mp4"}, {"a.mp4": b"x"}, "missing"),
            ({"Type": 7, "FileName": "C:/x.mp4", "IsAbsolutePath": True}, {}, "outside")):
        with pytest.raises(libmod.LibraryError, match=why):
            lib.import_lively(lively_zip(tmp_path / "bad.zip", info, files), tmp_path / "lib")
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("LivelyInfo.json", json.dumps({"Type": 7, "FileName": "a.mp4"}))
        z.writestr("../escape.mp4", b"x")
    with pytest.raises(libmod.LibraryError, match="refused path"):
        lib.import_lively(evil, tmp_path / "lib")


def test_thumbnails_for_pictures_gifs_videos_and_broken_files(qapp, tmp_path):
    big = picture(tmp_path / "big.png", "#FF0000", (3840, 2160))
    anim = MEDIA / "sample.gif"
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"\x00" * 4096)
    svc = thumbs.ThumbnailService(tmp_path / "thumbs")
    ready, meta, failed = {}, {}, {}
    svc.ready.connect(lambda p, img: ready.__setitem__(p, img))
    svc.meta.connect(lambda p, m: meta.__setitem__(p, m))
    svc.failed.connect(lambda p, e: failed.__setitem__(p, e))
    paths = {"big": str(big), "gif": str(anim), "mp4": str(MEDIA / "sample.mp4"), "webm": str(MEDIA / "sample.webm"),
             "broken": str(broken)}
    for key, kind in (("big", "image"), ("gif", "gif"), ("mp4", "video"), ("webm", "video"), ("broken", "video")):
        assert svc.request(paths[key], kind) is None
    assert len(svc.videos) + int(svc.grabber.busy()) == 3, "videos are decoded one at a time"
    assert svc.wait(40_000)
    for key in ("big", "gif", "mp4", "webm"):
        img = ready[paths[key]]
        assert (img.width(), img.height()) == (480, 270), key
    assert ready[paths["big"]].pixelColor(240, 135).name() == "#ff0000"
    assert meta[paths["big"]] == {"width": 3840, "height": 2160}
    assert meta[paths["mp4"]]["width"] == 320 and meta[paths["mp4"]]["height"] == 180
    assert 2.5 <= meta[paths["mp4"]]["duration"] <= 3.5
    assert meta[paths["gif"]]["duration"] == pytest.approx(0.6, abs=0.01)
    frame = ready[paths["mp4"]]
    colours = {frame.pixelColor(x, y).name() for x in range(0, 480, 40) for y in range(0, 270, 30)}
    assert len(colours) > 5, "a real frame of the test pattern, not a placeholder"
    assert paths["broken"] in failed and paths["broken"] not in ready
    again = thumbs.ThumbnailService(tmp_path / "thumbs")
    assert again.request(paths["mp4"], "video") is not None, "cached: never decoded twice"
    assert not again.pending
    big.write_bytes(picture(tmp_path / "x.png", "#00FF00", (64, 64)).read_bytes())
    assert thumbs.thumb_path(big, tmp_path / "thumbs") != thumbs.thumb_path(tmp_path / "x.png", tmp_path / "thumbs")
    assert again.cached(str(big)) is None, "a changed file gets a new thumbnail"
