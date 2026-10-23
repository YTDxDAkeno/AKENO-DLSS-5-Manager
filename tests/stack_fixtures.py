"""Fixtures for the stack engine: release archives shaped like the real ones, a local HTTP stand-in and games.

The archives mirror the original layouts checked on 2026-10-23: Skynizz ``DLSS5-Optimised/`` root, Dagherbou's
``OptiScaler/`` dependency folder, DLSS5-Feeder 1.19.0's Windows backslash member names, single-DLL rhi-repo
archives. File *contents* are synthetic PE files; the real archives are covered by tests/test_stack_real_assets.py.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

from helpers import make_pe, rtx, system_with

ROOT = Path(__file__).resolve().parents[1]

SKY_INI = """; OptiScaler (fixture of the shipped template)
[Upscalers]
Dx11Upscaler=auto
Dx12Upscaler=auto
VulkanUpscaler=auto

[Log]
LogToFile=auto
LogLevel=auto

[DlssNr]
; comment kept
ToggleKey=auto
PreSr=auto
PreSrReduced=auto
Enabled=auto
TransferStrength=auto
WorkingScale=auto
Style=auto
Passes=auto
Intensity=auto
CacheEnabled=auto
CacheInterval=auto
CacheRefreshBlend=auto
JbuUpsample=auto
"""

DAG_INI = """[Upscalers]
Dx12Upscaler=auto

[DlssNr]
Enabled=auto
TransferStrength=auto
WorkingScale=auto
Style=auto
Intensity=auto
"""


# dgVoodoo2's shipped dgVoodoo.conf: CRLF, column-aligned values, the same key in several sections.
DGV_CONF = "\r\n".join([
    ";==========================================================================",
    "Version                              = 0x287", "", "[General]", "",
    "OutputAPI                            = bestavailable", "Adapters                             = all", "",
    "[Glide]", "", "VideoCard                           = voodoo_2", "",
    "[DirectX]", "", "DisableAndPassThru                  = false", "",
    "VideoCard                           = internal3D", "VRAM                                = 256",
    "dgVoodooWatermark                   = true", ""])


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _pe_bytes(tmp: Path, name: str, **kw) -> bytes:
    p = make_pe(tmp / "pe" / name, ["kernel32.dll"], dll=True, **kw)
    return p.read_bytes()


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


class Server:
    """``opener`` for DownloadManager: serves bytes per URL, honours Range, can fail or count requests."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.requests: list[tuple[str, str | None]] = []
        self.fail: dict[str, int] = {}          # url -> HTTP status to raise
        self.cut_after: dict[str, int] = {}     # url -> bytes after which the connection drops (once)

    def open(self, req, timeout=None):
        import urllib.error
        url = req.full_url
        rng = req.headers.get("Range") or req.get_header("Range")
        self.requests.append((url, rng))
        if url in self.fail:
            raise urllib.error.HTTPError(url, self.fail[url], "fail", {}, None)
        data = self.files[url]
        status = 200
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            data = data[start:]
            status = 206
        cut = self.cut_after.pop(url, None)
        return _Resp(url, data, status, cut)


class _Resp:
    def __init__(self, url, data, status, cut):
        self.url, self.status = url, status
        self._buf = io.BytesIO(data)
        self.headers = {"Content-Length": str(len(data))}
        self._cut = cut
        self._sent = 0

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status

    def read(self, n=-1):
        if self._cut is not None and self._sent >= self._cut:
            raise ConnectionResetError("connection dropped")
        chunk = self._buf.read(n if self._cut is None else min(n, self._cut - self._sent) or 1)
        self._sent += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def build(tmp: Path) -> dict:
    """Create fixture archives, bundle and catalog folder. Returns paths, URLs and the server."""
    server = Server()
    comps = []

    def release(repo, tag, asset, members, root=""):
        url = f"https://github.com/{repo}/releases/download/{tag}/{asset}"
        path = _zip(tmp / "assets" / asset, members)
        data = path.read_bytes()
        server.files[url] = data
        files = {}
        for name, blob in members.items():
            rel = name.replace("\\", "/")
            if root:
                if not rel.startswith(root + "/"):
                    continue
                rel = rel[len(root) + 1:]
            files[rel] = _sha(blob)
        return {"tag": tag, "asset": asset, "url": url, "sha256": _sha(data), "size": len(data), "root": root,
                "files": files}

    def pick(v, names):
        v = dict(v)
        v["files"] = {k: s for k, s in v["files"].items() if k in names}
        return v

    sky = {}
    for version in ("1.3.0", "1.2.1"):
        dll = _pe_bytes(tmp, f"opti{version}.dll", strings={"ProductName": "OptiScaler " + version})
        fwd = _pe_bytes(tmp, f"fwd{version}.dll")
        members = {f"DLSS5-Optimised/{n}": b for n, b in (("OptiScaler.dll", dll), ("nvngx.dll_dlssnr.dll", fwd),
                                                          ("OptiScaler.ini", SKY_INI.encode()),
                                                          ("INSTALL.bat", b"@echo off"), ("install.py", b"x=1"))}
        rel = release("Skynizz/optiscaler-dlss5", "v" + version, f"OptiScaler-DLSS5-{version}-win-x64.zip", members,
                      "DLSS5-Optimised")
        sky[version] = dict(pick(rel, ["OptiScaler.dll", "OptiScaler.ini", "nvngx.dll_dlssnr.dll"]), version=version,
                            channel="stable", settings=["ToggleKey", "PreSr", "PreSrReduced", "Enabled",
                                                        "TransferStrength", "WorkingScale", "Style", "Passes",
                                                        "Intensity", "CacheEnabled", "CacheInterval",
                                                        "CacheRefreshBlend", "JbuUpsample"])
    comps.append({"id": "optiscaler-dlss5", "role": "consumer_optiscaler", "name": "OptiScaler DLSS 5 (Skynizz)",
                  "repository": "Skynizz/optiscaler-dlss5", "license": "GPL-3.0", "homepage": "",
                  "features": ["presr", "cache", "passes"],
                  "release": {"tag": r"^v(\d+\.\d+\.\d+)$", "asset": r"^OptiScaler-DLSS5-(?P<v>[\d.]+)-win-x64\.zip$",
                              "root": "DLSS5-Optimised", "files": ["OptiScaler.dll", "OptiScaler.ini",
                                                                   "nvngx.dll_dlssnr.dll"]},
                  "versions": [sky["1.3.0"], sky["1.2.1"]]})
    dag_members = {"OptiScaler.dll": _pe_bytes(tmp, "dag.dll", strings={"ProductName": "OptiScaler"}),
                   "nvngx.dll_dlssnr.dll": _pe_bytes(tmp, "dagfwd.dll"), "OptiScaler.ini": DAG_INI.encode(),
                   "OptiScaler\\libxess.dll": _pe_bytes(tmp, "xess.dll"),
                   "OptiScaler\\D3D12_OptiScaler\\D3D12Core.dll": _pe_bytes(tmp, "core.dll"),
                   "setup_windows.bat": b"@echo off", "!! EXTRACT ALL FILES TO GAME FOLDER !!": b""}
    dag = release("Dagherbou/OptiScaler_DLSSNR", "v0.2.0-patch1", "OptiScaler-DLSSNR-v0.2.0-onimusha-fix.zip",
                  dag_members)
    dag = pick(dag, ["OptiScaler.dll", "OptiScaler.ini", "nvngx.dll_dlssnr.dll", "OptiScaler/libxess.dll",
                     "OptiScaler/D3D12_OptiScaler/D3D12Core.dll"])
    comps.append({"id": "optiscaler-dlssnr", "role": "consumer_optiscaler", "name": "OptiScaler DLSSNR (Dagherbou)",
                  "repository": "Dagherbou/OptiScaler_DLSSNR", "license": "GPL-3.0", "homepage": "", "features": [],
                  "versions": [dict(dag, version="0.2.0-patch1", channel="stable",
                                    settings=["Enabled", "TransferStrength", "WorkingScale", "Style", "Intensity"])]})
    reno = {}
    for version in ("7.0.0-rc8", "8.5.0-rc10"):
        addon = _pe_bytes(tmp, f"reno{version}.addon64", pad=64) + b"[RenoDX.DLSS5] NRResolutionScale"
        reno[version] = release("RankFTW/rhi-repo", "renodx-dlss5-" + version, f"renodx-dlss5_{version}.zip",
                                {"renodx-dlss5.addon64": addon})
    settings = ["NeuralUplift", "NRResolutionScale", "NRPasses", "NRStyle", "NRIntensity", "EnableHooks",
                "NREnableUpscaling"]
    comps.append({"id": "renodx-dlss5", "role": "consumer_renodx", "name": "RenoDX DLSS 5 add-on",
                  "repository": "RankFTW/rhi-repo", "license": "", "homepage": "", "features": [],
                  "release": {"tag": r"^renodx-dlss5-(\d[\w.\-]*)$", "asset": r"^renodx-dlss5_.*\.zip$", "root": "",
                              "files": ["renodx-dlss5.addon64"]},
                  "versions": [dict(reno["7.0.0-rc8"], version="7.0.0-rc8", channel="stable", recommended=True,
                                    settings=settings),
                               dict(reno["8.5.0-rc10"], version="8.5.0-rc10", channel="prerelease",
                                    config_version=7, settings=settings)]})
    feed_members = {"host64\\dlss5-feed-host64.exe": _pe_bytes(tmp, "host.exe"),
                    "reshade-shaders\\Shaders\\DLSS5_Feed.fx": b"technique DLSS5_Feed {}",
                    "dlss5-feed-helper.addon64": _pe_bytes(tmp, "helper.addon64"),
                    "dlss5-feed.addon32": make_pe(tmp / "pe" / "f32", [], machine="x86", dll=True).read_bytes(),
                    "dlss5-feed.addon64": _pe_bytes(tmp, "feed.addon64"), "READ-ME-FIRST.txt": b"read",
                    "Verify-DLSS5Feeder.ps1": b"Write-Host"}
    feed_all = release("jlrouzies-fr/DLSS5-Feeder", "v1.19.0", "DLSS5-Feeder-1.19.0.zip", feed_members)
    feed = pick(feed_all, ["dlss5-feed.addon64", "reshade-shaders/Shaders/DLSS5_Feed.fx"])
    comps.append({"id": "dlss5-feeder", "role": "feeder", "name": "DLSS5-Feeder", "repository": "jlrouzies-fr/DLSS5-Feeder",
                  "license": "MIT", "homepage": "", "features": [],
                  "versions": [dict(feed, version="1.19.0", channel="stable", recommended=True)]})
    # the same release archive serves the 32-bit route: addon32 beside the game, the 64-bit helper in host64
    feed32 = pick(feed_all, ["dlss5-feed.addon32", "host64/dlss5-feed-host64.exe", "reshade-shaders/Shaders/DLSS5_Feed.fx"])
    comps.append({"id": "dlss5-feeder-x86", "role": "feeder32", "name": "DLSS5-Feeder (32-bit)",
                  "repository": "jlrouzies-fr/DLSS5-Feeder", "license": "MIT", "homepage": "", "features": [],
                  "versions": [dict(feed32, version="1.19.0", channel="stable", recommended=True)]})
    comps.append({"id": "reshade", "role": "reshade", "name": "ReShade", "repository": "crosire/reshade",
                  "license": "BSD-3-Clause", "homepage": "", "features": [],
                  "versions": [{"version": "6.8.0", "label": "6.8.0 (AKENO build)", "source": "bundled",
                                "bundle_role": "reshade", "channel": "stable", "recommended": True}]})
    comps.append({"id": "reshade-x86", "role": "reshade32", "name": "ReShade 32-bit", "repository": "crosire/reshade",
                  "license": "BSD-3-Clause", "homepage": "", "features": [],
                  "versions": [{"version": "6.8.0", "label": "6.8.0 (AKENO build)", "source": "bundled",
                                "bundle_role": "reshade32", "channel": "stable", "recommended": True}]})
    dgv_strings = {"ProductName": "dgVoodoo", "FileDescription": "dgVoodoo DirectX Wrapper"}
    dgv_members = {"MS/x86/D3D9.dll": make_pe(tmp / "pe" / "dgv9", [], machine="x86", dll=True,
                                              strings=dgv_strings).read_bytes(),
                   "MS/x86/D3D8.dll": make_pe(tmp / "pe" / "dgv8", [], machine="x86", dll=True,
                                              strings=dgv_strings, pad=8).read_bytes(),
                   "MS/x64/D3D9.dll": _pe_bytes(tmp, "dgv64.dll", strings=dgv_strings),
                   "dgVoodoo.conf": DGV_CONF.encode(), "dgVoodooCpl.exe": _pe_bytes(tmp, "cpl.exe")}
    dgv = release("dege-diosg/dgVoodoo2", "v2.87.5", "dgVoodoo2_87_5.zip", dgv_members)
    dgv = pick(dgv, ["MS/x86/D3D9.dll", "MS/x86/D3D8.dll", "MS/x64/D3D9.dll", "dgVoodoo.conf"])
    comps.append({"id": "dgvoodoo2", "role": "translator", "name": "dgVoodoo2", "repository": "dege-diosg/dgVoodoo2",
                  "license": "Freeware", "homepage": "", "features": [],
                  "versions": [dict(dgv, version="2.87.5", channel="stable", recommended=True)]})
    comps.append({"id": "dh-uber-motion", "role": "motion", "name": "dh_uber_motion", "repository": None,
                  "license": "GPL-2.0", "homepage": "", "features": [],
                  "versions": [{"version": "0.2.0", "source": "bundled", "bundle_role": "motion_provider",
                                "channel": "stable", "recommended": True, "provider_id": 0,
                                "technique": "DH_UBER_MOTION_020", "shader": "dh_uber_motion.fx"}]})
    sr = release("RankFTW/rhi-repo", "dlss-310.9.16", "nvngx_dlss_310.9.16.zip",
                 {"nvngx_dlss.dll": _pe_bytes(tmp, "sr.dll", version=(310, 9, 16, 0),
                                              strings={"ProductName": "NVIDIA Deep Learning SuperSampling",
                                                       "FileDescription": "NVIDIA DLSS - DVS PRODUCTION"})})
    comps.append({"id": "nvngx-dlss", "role": "sr_runtime", "name": "DLSS SR", "repository": "RankFTW/rhi-repo",
                  "license": "", "homepage": "", "features": [],
                  "versions": [dict(sr, version="310.9.16", channel="stable", signed="nvidia", recommended=True)]})
    nr_official = release("RankFTW/rhi-repo", "dlssnr-310.8.0", "nvngx_dlssnr_310.8.0.zip",
                          {"nvngx_dlssnr.dll": _pe_bytes(tmp, "nr0.dll", version=(310, 8, 0, 0),
                                                         strings={"ProductName": "NVIDIA DLSSNR"})})
    nr_sf = release("RankFTW/rhi-repo", "dlssnr-310.8.SF-v2", "nvngx_dlssnr_310.8.SF-v2.zip",
                    {"nvngx_dlssnr.dll": _pe_bytes(tmp, "nrsf.dll", pad=17)})
    comps.append({"id": "nvngx-dlssnr", "role": "nr_runtime", "name": "DLSS 5 model", "repository": "RankFTW/rhi-repo",
                  "license": "", "homepage": "", "features": [],
                  "release": {"tag": r"^dlssnr-(.+)$", "asset": r"^nvngx_dlssnr_.*\.zip$", "root": "",
                              "files": ["nvngx_dlssnr.dll"]},
                  "versions": [dict(nr_official, version="310.8.0", label="310.8.0 (NVIDIA)", channel="stable",
                                    signed="nvidia", gpus=["RTX 50"], recommended_for=["RTX 50"]),
                               dict(nr_sf, version="310.8.2", label="310.8.2 (SF-v2)", channel="community",
                                    signed="modified", gpus=["RTX 20", "RTX 30", "RTX 40", "RTX 50"],
                                    recommended_for=["RTX 20", "RTX 30", "RTX 40"])]})
    cat = tmp / "catalog"
    cat.mkdir(parents=True, exist_ok=True)
    (cat / "components.json").write_text(json.dumps({"schema": 3, "version": "test", "components": comps}))
    shutil.copyfile(ROOT / "akeno/data/stack/recipes.json", cat / "recipes.json")
    shutil.copyfile(ROOT / "akeno/data/stack/games.json", cat / "games.json")
    # The CI dependency bundle: ReShade with add-on exports, the motion shader and framework header.
    bundle = tmp / "bundle"
    reshade = make_pe(bundle / "ReShade64.dll", ["kernel32.dll"], dll=True, version=(6, 8, 0, 0),
                      strings={"ProductName": "ReShade"}, exports=["ReShadeRegisterAddon", "ReShadeUnregisterAddon"])
    reshade32 = make_pe(bundle / "ReShade32.dll", ["kernel32.dll"], machine="x86", dll=True, version=(6, 8, 0, 0),
                        strings={"ProductName": "ReShade"}, exports=["ReShadeRegisterAddon", "ReShadeUnregisterAddon"])
    (bundle / "dh-motion").mkdir(parents=True)
    (bundle / "dh-motion" / "dh_uber_motion.fx").write_text("technique DH_UBER_MOTION_020 {}")
    (bundle / "reshade-framework").mkdir()
    (bundle / "reshade-framework" / "ReShade.fxh").write_text("// framework")
    manifest = {"schema": 1, "components": [
        {"role": "reshade", "version": "6.8.0", "path": "ReShade64.dll", "sha256": _sha(reshade.read_bytes())},
        {"role": "reshade32", "version": "6.8.0", "path": "ReShade32.dll", "sha256": _sha(reshade32.read_bytes())},
        {"role": "motion_provider", "version": "0.2.0", "path": "dh-motion/dh_uber_motion.fx",
         "destination": "reshade-shaders/Shaders/dh_uber_motion.fx",
         "sha256": _sha((bundle / "dh-motion" / "dh_uber_motion.fx").read_bytes()),
         "assets": [{"path": "reshade-framework/ReShade.fxh", "destination": "reshade-shaders/Shaders/ReShade.fxh",
                     "sha256": _sha((bundle / "reshade-framework" / "ReShade.fxh").read_bytes())}]}],
        "packages": []}
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    return {"server": server, "catalog": cat, "bundle": bundle, "sky": sky, "reno": reno}


def services(tmp: Path, db, monkeypatch, gpu=None, running=None):
    """AppServices with the fixture catalog, the real DownloadManager over the stub server and a fixed GPU."""
    from akeno.install.transaction import Installer
    from akeno.security.downloads import DownloadManager
    from akeno.services import AppServices
    from akeno.stack.catalog import StackCatalog
    from akeno.stack.service import StackManager
    from akeno.stack.sources import DOWNLOAD_HOSTS, ComponentStore
    fx = build(tmp)
    monkeypatch.setenv("AKENO_BUNDLE_DIR", str(fx["bundle"]))
    installer = Installer(db, tmp / "backups", running_check=running or (lambda d: []))
    svc = AppServices(db=db, installer=installer)
    svc.system = system_with(gpu or rtx("NVIDIA GeForce RTX 4080", 0x2704, 16, "32.0.16.1742"))
    catalog = StackCatalog.load(fx["catalog"])
    downloads = DownloadManager(DOWNLOAD_HOSTS, cache=tmp / "dl", opener=fx["server"], retries=2)
    store = ComponentStore(db, catalog, downloads, root=tmp / "components")
    svc.stack = StackManager(svc, catalog=catalog, store=store)
    return svc, fx


def dlss_game(svc, tmp: Path, name="Night City", streamline=False, extra=()):
    """A DX12 game with its own DLSS (OptiScaler/RenoDX native targets)."""
    root = tmp / "games" / name
    exe_dir = root / "bin" / "x64"
    make_pe(exe_dir / (name.replace(" ", "") + ".exe"), ["d3d12.dll", "dxgi.dll", "kernel32.dll"], pad=60000)
    make_pe(exe_dir / "nvngx_dlss.dll", [], dll=True, version=(310, 2, 1, 0))
    if streamline:
        make_pe(exe_dir / "sl.interposer.dll", [], dll=True)
    for rel, data in extra:
        (exe_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (exe_dir / rel).write_bytes(data)
    game = svc.library.add_manual(str(root), name)
    return game, exe_dir


def plain_game(svc, tmp: Path, name="Old Racer"):
    """A DX11 game without any upscaler (Feeder targets)."""
    root = tmp / "games" / name
    make_pe(root / "Racer.exe", ["d3d11.dll", "dxgi.dll", "kernel32.dll"], pad=60000)
    game = svc.library.add_manual(str(root), name)
    return game, root


def snapshot(d: Path) -> dict[str, str]:
    return {p.relative_to(d).as_posix(): _sha(p.read_bytes()) for p in sorted(d.rglob("*")) if p.is_file()}


def fivem_install(svc, tmp: Path, existing_plugins: dict[str, bytes] | None = None, shader_files: int = 0):
    """A FiveM Legacy client (FiveM.exe, FiveM.app, CitizenFX.ini -> GTA V Legacy) added to the library."""
    root, gta = tmp / "FiveM", tmp / "GTA V"
    app = root / "FiveM.app"
    app.mkdir(parents=True)
    make_pe(root / "FiveM.exe", ["kernel32.dll"])
    make_pe(gta / "GTA5.exe", ["kernel32.dll"])            # protected exe: no useful imports
    (app / "CitizenFX.ini").write_text(f"[Game]\nIVPath={gta}\nSavedBuildNumber=3258\nUpdateChannel=production\n")
    plugins = app / "plugins"
    for rel, data in (existing_plugins or {}).items():
        (plugins / rel).parent.mkdir(parents=True, exist_ok=True)
        (plugins / rel).write_bytes(data)
    for i in range(shader_files):
        p = plugins / "reshade-shaders" / "Shaders" / f"pack{i // 100}" / f"effect{i}.fx"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    game = svc.library.add_manual(str(root))
    return game, root, app, gta
