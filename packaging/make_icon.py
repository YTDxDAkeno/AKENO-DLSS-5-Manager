"""Render the AKENO application icon (original artwork from akeno.ui.art) to packaging/akeno.ico."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QImageWriter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from akeno.ui import art  # noqa: E402

out = ROOT / "packaging" / "akeno.ico"
img = art.app_icon_pixmap(256).toImage()
writer = QImageWriter(str(out), b"ico")
if not writer.write(img):
    # Fallback: PNG-compressed single-image ICO written by hand (valid since Windows Vista).
    import struct
    png = ROOT / "packaging" / "akeno-256.png"
    img.save(str(png), "PNG")
    data = png.read_bytes()
    out.write_bytes(struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(data), 22) + data)
art.app_icon_pixmap(256).save(str(ROOT / "packaging" / "akeno-256.png"), "PNG")
print(f"wrote {out}")
