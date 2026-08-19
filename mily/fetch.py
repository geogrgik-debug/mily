"""Скачивание исходников: полные матчи, паки, любые ссылки под yt-dlp."""

from __future__ import annotations

from pathlib import Path

from .shell import require, run

RAW_DIR = Path("clips/raw")

# Максимальное качество: эдиты потом апскейлятся, терять пиксели на входе нельзя.
FORMAT = "bestvideo[height>=1080]+bestaudio/best"


def download(url: str, *, out_dir: Path = RAW_DIR, slug: str | None = None) -> None:
    """Скачать один URL в clips/raw."""
    require("yt-dlp")
    out_dir.mkdir(parents=True, exist_ok=True)

    name = f"{slug}_%(title).60s.%(ext)s" if slug else "%(title).80s.%(ext)s"
    cmd = [
        "yt-dlp",
        "-f", FORMAT,
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--restrict-filenames",
        "-o", str(out_dir / name),
        url,
    ]
    print(f"[fetch] {url}")
    run(cmd, quiet=False)


def download_all(sources: list[dict], *, out_dir: Path = RAW_DIR) -> None:
    """Прогнать список источников из config/sources.yaml."""
    for item in sources:
        url = item.get("url") if isinstance(item, dict) else item
        if not url:
            continue
        slug = item.get("slug") if isinstance(item, dict) else None
        download(url, out_dir=out_dir, slug=slug)
