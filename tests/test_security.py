import io
import stat
import zipfile

import pytest

from akeno.security import archive
from akeno.security.catalog import Catalog, CatalogError
from akeno.security.downloads import (CancelToken, Cancelled, DownloadError, DownloadManager, VerificationError)
from helpers import sha


class FakeResp(io.BytesIO):
    def __init__(self, data, url, length=True):
        super().__init__(data)
        self._url = url
        self.headers = {"Content-Length": str(len(data))} if length else {}

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, data, final_url=None, fail_times=0):
        self.data, self.final_url, self.fail_times, self.calls = data, final_url, fail_times, 0

    def open(self, req, timeout=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise OSError("connection reset")
        return FakeResp(self.data, self.final_url or req.full_url)


def dm(tmp_path, opener, **kw):
    return DownloadManager(["github.com", "objects.githubusercontent.com"], tmp_path / "cache", opener=opener, **kw)


def test_download_verified_and_cached(tmp_path):
    data = b"package-bytes" * 100
    m = dm(tmp_path, FakeOpener(data))
    r = m.fetch("https://github.com/o/r/releases/download/v1/a.zip", sha(data), "a.zip", len(data))
    assert r.path.read_bytes() == data and not r.from_cache
    assert m.fetch("https://github.com/o/r/releases/download/v1/a.zip", sha(data), "a.zip").from_cache


def test_download_hash_mismatch_discards_file(tmp_path):
    m = dm(tmp_path, FakeOpener(b"tampered"))
    with pytest.raises(VerificationError):
        m.fetch("https://github.com/x/a.zip", "0" * 64, "a.zip")
    assert not list((tmp_path / "cache").glob("*"))


def test_download_rejects_http_unknown_host_and_bad_redirect(tmp_path):
    m = dm(tmp_path, FakeOpener(b"x", final_url="https://evil.example.com/a.zip"))
    with pytest.raises(DownloadError, match="HTTPS"):
        m.fetch("http://github.com/a.zip", "0" * 64, "a")
    with pytest.raises(DownloadError, match="allow-list"):
        m.fetch("https://github-mirror.example.org/a.zip", "0" * 64, "a")
    with pytest.raises(DownloadError, match="allow-list"):
        m.fetch("https://github.com/a.zip", "0" * 64, "a")


def test_download_size_limit_retry_and_cancel(tmp_path):
    data = b"x" * 10000
    with pytest.raises(DownloadError, match="larger than expected|size limit"):
        dm(tmp_path, FakeOpener(data)).fetch("https://github.com/a.zip", sha(data), "a", expected_size=100)
    op = FakeOpener(data, fail_times=1)
    m = dm(tmp_path, op, retries=3)
    m.fetch("https://github.com/a.zip", sha(data), "b")
    assert op.calls == 2
    tok = CancelToken()
    tok.cancel()
    with pytest.raises(Cancelled):
        dm(tmp_path, FakeOpener(data)).fetch("https://github.com/a.zip", sha(data), "c", token=tok)


def _zip(tmp_path, entries, name="t.zip", symlink=None):
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        for n, d in entries.items():
            z.writestr(n, d)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "/etc/passwd")
    return p


@pytest.mark.parametrize("bad", ["../evil.dll", "a/../../evil.dll", "/abs.dll", "C:/win/evil.dll",
                                 "..\\evil.dll", "con.txt", "a/b:c.dll"])
def test_archive_rejects_malicious_paths(tmp_path, bad):
    p = _zip(tmp_path, {"ok.txt": b"1", bad: b"2"})
    with pytest.raises(archive.ArchiveError):
        archive.inspect(p)


def test_archive_rejects_symlink_and_hash_mismatch(tmp_path):
    with pytest.raises(archive.ArchiveError, match="Symbolic"):
        archive.inspect(_zip(tmp_path, {"a.txt": b"1"}, symlink="link"))
    p = _zip(tmp_path, {"root/a.dll": b"real"}, "h.zip")
    with pytest.raises(archive.ArchiveError, match="Hash mismatch"):
        archive.extract(p, tmp_path / "out", {"a.dll": "0" * 64}, "root")
    assert not (tmp_path / "out" / "a.dll").exists()
    with pytest.raises(archive.ArchiveError, match="missing"):
        archive.extract(p, tmp_path / "out", {"b.dll": None}, "root")


def test_archive_extracts_only_wanted_with_backslash_names(tmp_path):
    p = _zip(tmp_path, {"host64\\helper.exe": b"h", "Verify.ps1": b"script", "a.addon64": b"a"}, "w.zip")
    out = archive.extract(p, tmp_path / "out", {"host64/helper.exe": sha(b"h"), "a.addon64": sha(b"a")})
    assert (tmp_path / "out" / "host64" / "helper.exe").read_bytes() == b"h"
    assert not (tmp_path / "out" / "Verify.ps1").exists()
    assert set(out) == {"host64/helper.exe", "a.addon64"}


def test_archive_rejects_zip_bomb_ratio(tmp_path):
    p = tmp_path / "bomb.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.bin", b"\0" * (20 * 1024 * 1024))
    with pytest.raises(archive.ArchiveError, match="ratio"):
        archive.inspect(p)


def test_bundled_catalog_is_valid_and_pinned():
    c = Catalog.load_bundled()
    for p in c.packages:
        if p.installable:
            assert p.url.startswith("https://github.com/") and len(p.sha256) == 64 and p.files
    assert not c.package("optiscaler-dlssnr-dagherbou").installable
    assert c.tool("presentmon").sha256


def test_catalog_validation_rejects_unpinned_verified():
    with pytest.raises(CatalogError):
        Catalog({"schema": 1, "packages": [{"id": "x", "name": "x", "version": "1", "backend": "b",
                                            "trust": "verified", "url": "https://github.com/a", "sha256": None}]})
    with pytest.raises(CatalogError):
        Catalog({"schema": 1, "packages": [{"id": "x", "name": "x", "version": "1", "backend": "b",
                                            "trust": "verified", "url": "http://github.com/a", "sha256": "a" * 64}]})
