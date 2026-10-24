# PyInstaller spec for AKENO DLSS 5 Manager (Windows x64, one-folder build).
# Build:  pyinstaller --noconfirm packaging/akeno.spec
import re
from pathlib import Path

ROOT = Path(SPECPATH).parent
version = re.search(r'__version__ = "([^"]+)"', (ROOT / "akeno" / "__init__.py").read_text()).group(1)
nums = [int(x) for x in re.findall(r"\d+", version)[:3]] + [0]
vfile = ROOT / "packaging" / "version_info.txt"
vfile.write_text(f"""
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({nums[0]}, {nums[1]}, {nums[2]}, 0), prodvers=({nums[0]}, {nums[1]}, {nums[2]}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'AKENO community project'),
      StringStruct('FileDescription', 'AKENO DLSS 5 Manager'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'AKENO'),
      StringStruct('LegalCopyright', 'GPL-3.0-or-later'),
      StringStruct('OriginalFilename', 'AKENO.exe'),
      StringStruct('ProductName', 'AKENO DLSS 5 Manager'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")

# Desktop Studio needs Qt Multimedia (video wallpapers, FFmpeg backend), Qt WebEngine (HTML/WebGL wallpapers; it
# brings Qml, Quick, WebChannel and Positioning) and Qt Network (the local socket between suite, host and renderers).
EXCLUDES = ["PySide6.QtWebEngineQuick", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtQuick3D",
            "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth",
            "PySide6.QtSql", "PySide6.QtTest", "tkinter", "unittest", "pytest"]

a = Analysis(
    [str(ROOT / "akeno" / "__main__.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "akeno" / "data"), "akeno/data")],
    hiddenimports=["PySide6.QtSvg", "PySide6.QtNetwork", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
                   "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="AKENO",
    console=False,
    icon=str(ROOT / "packaging" / "akeno.ico"),
    version=str(vfile),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="AKENO", upx=False)
