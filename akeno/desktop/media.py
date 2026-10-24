"""Wallpaper media types, by file extension, with the checks a file must pass before it is shown."""

from __future__ import annotations

from pathlib import Path

KINDS = {
    "image": {".jpg", ".jpeg", ".png", ".bmp", ".webp"},
    "gif": {".gif"},
    "video": {".mp4", ".webm", ".mkv", ".mov", ".m4v"},
    "web": {".html", ".htm"},
}
MAX_IMAGE_MB = 200
MAX_VIDEO_MB = 4096


class MediaError(ValueError):
    pass


def kind_of(path: str | Path) -> str | None:
    p = Path(path)
    if p.is_dir() and (p / "index.html").is_file():
        return "web"                        # a web wallpaper folder (index.html plus its assets)
    ext = p.suffix.lower()
    return next((k for k, exts in KINDS.items() if ext in exts), None)


def check(path: str | Path) -> str:
    p = Path(path)
    kind = kind_of(p)
    if kind is None:
        raise MediaError(f"{p.name}: not a supported wallpaper (image, GIF, video, or a web page)")
    if not p.exists():
        raise MediaError(f"{p} does not exist")
    if p.is_file():
        size = p.stat().st_size / 2 ** 20
        if size > (MAX_VIDEO_MB if kind == "video" else MAX_IMAGE_MB):
            raise MediaError(f"{p.name} is too large ({size:.0f} MB)")
    return kind


def web_entry(path: str | Path) -> Path:
    p = Path(path)
    return p / "index.html" if p.is_dir() else p
