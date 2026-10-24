"""Wallpapers → Find wallpapers (akeno/desktop/sources.py).

The Pixabay client is tested against a recorded-shape answer through a fake opener, not against the live
service. The answer follows the format of Pixabay's API documentation, and it is not hardware or network
evidence."""

from __future__ import annotations

import io
import json
import os
import time
import urllib.parse

import pytest

from akeno.desktop import sources

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096


def answer(**over):
    hit = {"id": 125, "pageURL": "https://pixabay.com/videos/id-125/", "type": "animation",
           "tags": "neon, city, night, rain", "duration": 21, "user": "Creator",
           "videos": {"large": {"url": "", "width": 0, "height": 0, "size": 0, "thumbnail": ""},
                      "medium": {"url": "https://cdn.pixabay.com/video/1/125_medium.mp4", "width": 1920,
                                 "height": 1080, "size": len(MP4), "thumbnail": "https://cdn.pixabay.com/video/1/125_medium.jpg"},
                      "small": {"url": "https://cdn.pixabay.com/video/1/125_small.mp4", "width": 1280, "height": 720,
                                "size": len(MP4), "thumbnail": "https://cdn.pixabay.com/video/1/125_small.jpg"},
                      "tiny": {"url": "https://evil.example/125_tiny.mp4", "width": 960, "height": 540, "size": 1,
                               "thumbnail": ""}}}
    hit.update(over)
    return {"total": 1, "totalHits": 1, "hits": [hit]}


class Resp(io.BytesIO):
    def __init__(self, url, body, headers=None):
        super().__init__(body)
        self.url = url
        self.headers = headers or {"Content-Length": str(len(body))}

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Opener:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def open(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        for prefix, body in self.routes.items():
            if url.startswith(prefix):
                return Resp(url, body() if callable(body) else body)
        raise AssertionError(f"unexpected request {url}")


@pytest.fixture
def client(tmp_path):
    opener = Opener({sources.API_URL: json.dumps(answer()).encode(), "https://cdn.pixabay.com/video/1/125_medium.mp4": MP4,
                     "https://cdn.pixabay.com/video/1/125_small.jpg": b"\xff\xd8jpeg"})
    return sources.Pixabay("12345678-abcdef0123456789abcd", tmp_path / "cache", opener=opener), opener


def test_answer_is_parsed_and_foreign_or_empty_files_are_dropped():
    res = sources.parse(answer())
    v = res.videos[0]
    assert set(v.files) == {"medium", "small"}, "the empty 4K entry and the foreign host are dropped"
    assert v.best("4k").size_key == "medium", "4K falls back to the best size there is"
    assert v.best("720p").size_key == "small" and v.title == "neon, city, night"
    assert v.thumbnail.endswith("125_small.jpg")
    assert sources.parse(answer(pageURL="https://phish.example/x")).videos[0].page_url == \
        "https://pixabay.com/videos/id-125/"
    with pytest.raises(sources.SourceError):
        sources.parse({"error": "bad"})


def test_search_sends_the_documented_parameters_and_caches_for_24_hours(client):
    px, opener = client
    res = px.search("neon city", "animation", 2, 24, "4k")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(opener.calls[0]).query)
    assert q["q"] == ["neon city"] and q["video_type"] == ["animation"] and q["page"] == ["2"]
    assert q["safesearch"] == ["true"] and q["min_width"] == ["3840"] and q["key"] == ["12345678-abcdef0123456789abcd"]
    assert not res.from_cache and res.videos
    again = px.search("neon city", "animation", 2, 24, "4k")
    assert again.from_cache and len(opener.calls) == 1, "the same search within 24 hours is not sent again"
    stamp = time.time() - sources.CACHE_SECONDS - 10
    for f in px.cache.glob("*.json"):
        os.utime(f, (stamp, stamp))
    assert not px.search("neon city", "animation", 2, 24, "4k").from_cache and len(opener.calls) == 2


def test_rate_limit_and_missing_key(tmp_path):
    clock = [1000.0]
    opener = Opener({sources.API_URL: json.dumps(answer()).encode()})
    px = sources.Pixabay("12345678-abcdef0123456789abcd", tmp_path, opener=opener, clock=lambda: clock[0])
    for i in range(sources.RATE_LIMIT[0]):
        px.search(f"q{i}")
    with pytest.raises(sources.SourceError):
        px.search("one more")
    clock[0] += sources.RATE_LIMIT[1] + 1
    px.search("after a minute")
    with pytest.raises(sources.SourceError):
        sources.Pixabay("", tmp_path).search("x")


def test_only_https_on_pixabay_hosts(client):
    px, _ = client
    for url in ("http://pixabay.com/api/videos/", "https://pixabay.com.evil.example/api/", "https://example.com/x"):
        with pytest.raises(sources.SourceError):
            px._open(url, sources.API_HOSTS)


def test_redirect_away_from_pixabay_is_refused():
    handler = sources._OnlyHosts(sources.API_HOSTS | sources.FILE_HOSTS)
    with pytest.raises(sources.SourceError):
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/video.mp4")


def test_download_saves_the_video_with_its_credit(client, tmp_path):
    px, _ = client
    video = px.search("neon").videos[0]
    got = []
    path = px.download(video, "1080p", tmp_path / "lib", lambda n, total: got.append((n, total)))
    assert path.read_bytes() == MP4 and got[-1][0] == len(MP4)
    credit = json.loads(path.with_suffix(".json").read_text())
    assert credit["source"] == "Pixabay" and credit["creator"] == "Creator" and credit["page"].startswith("https://pixabay.com")
    assert not list((tmp_path / "lib").glob("*.part"))


def test_download_refuses_a_file_that_is_not_a_video(tmp_path):
    opener = Opener({sources.API_URL: json.dumps(answer()).encode(),
                     "https://cdn.pixabay.com/video/1/125_medium.mp4": b"<html>" + b"x" * (len(MP4) - 6)})
    px = sources.Pixabay("12345678-abcdef0123456789abcd", tmp_path / "c", opener=opener)
    video = px.search("neon").videos[0]
    with pytest.raises(sources.SourceError):
        px.download(video, "1080p", tmp_path / "lib")
    assert not list((tmp_path / "lib").glob("*.mp4")) and not list((tmp_path / "lib").glob("*.part"))


def test_download_refuses_more_bytes_than_announced(tmp_path):
    data = answer()
    data["hits"][0]["videos"]["medium"]["size"] = 100_000           # Pixabay says 100 kB...
    big = MP4 + b"\x00" * 300_000                                    # ...the server sends 300 kB
    opener = Opener({sources.API_URL: json.dumps(data).encode(),
                     "https://cdn.pixabay.com/video/1/125_medium.mp4": big})
    px = sources.Pixabay("12345678-abcdef0123456789abcd", tmp_path / "c", opener=opener)
    with pytest.raises(sources.SourceError):
        px.download(px.search("neon").videos[0], "1080p", tmp_path / "lib")


def test_api_key_is_stored_apart_from_settings(tmp_path):
    keys = sources.KeyStore(tmp_path / "pixabay")
    with pytest.raises(sources.SourceError):
        keys.set("not a key!")
    keys.set("12345678-abcdef0123456789abcd")
    assert keys.get() == "12345678-abcdef0123456789abcd"
    keys.clear()
    assert keys.get() == ""


def test_recent_downloads_lists_new_videos_and_import_copies_them(tmp_path):
    dl = tmp_path / "Downloads"
    dl.mkdir()
    now = time.time()
    (dl / "Neon City 4K.mp4").write_bytes(MP4)
    (dl / "loop.gif").write_bytes(b"GIF89a")
    (dl / "setup.exe").write_bytes(b"MZ")
    old = dl / "old.webm"
    old.write_bytes(b"x")
    os.utime(old, (now - 30 * 86400, now - 30 * 86400))
    found = sources.recent_downloads(dl, now=now)
    assert {p.name for p in found} == {"Neon City 4K.mp4", "loop.gif"}
    lib = tmp_path / "lib"
    dest = sources.import_file(dl / "Neon City 4K.mp4", lib)
    assert dest.read_bytes() == MP4 and dest.parent == lib
    assert sources.import_file(dl / "Neon City 4K.mp4", lib) == dest, "importing again reuses the copy"
    with pytest.raises(sources.SourceError):
        sources.import_file(dl / "setup.exe", lib)


def test_sites_without_an_api_are_links_only():
    assert {s.key for s in sources.SITES} == {"motionbgs", "mylivewallpapers", "desktophut"}
    for s in sources.SITES:
        assert s.url.startswith("https://")
        assert urllib.parse.urlsplit(s.url).hostname not in sources.API_HOSTS | sources.FILE_HOSTS
