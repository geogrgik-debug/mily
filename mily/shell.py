"""Обёртки над внешними бинарями (ffmpeg, ffprobe, yt-dlp)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys


class MissingBinary(RuntimeError):
    pass


HINTS = {
    "ffmpeg": "brew install ffmpeg  |  apt install ffmpeg",
    "ffprobe": "ставится вместе с ffmpeg",
    "yt-dlp": "pip install -U yt-dlp",
}


def require(binary: str) -> str:
    """Вернуть путь к бинарю или упасть с внятным сообщением."""
    path = shutil.which(binary)
    if not path:
        hint = HINTS.get(binary, "")
        raise MissingBinary(f"не найден {binary!r}. Поставь: {hint}")
    return path


def run(cmd: list[str], *, quiet: bool = True) -> subprocess.CompletedProcess:
    """Запустить команду, показать stderr если упала."""
    proc = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-15:])
        print(f"[!] команда упала: {' '.join(cmd[:3])} ...\n{tail}", file=sys.stderr)
    return proc


def probe(path: str) -> dict:
    """Метаданные видео: длительность, размеры, fps."""
    require("ffprobe")
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate",
            "-show_entries", "format=duration",
            "-of", "json", path,
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe не смог прочитать {path}")

    data = json.loads(proc.stdout)
    stream = (data.get("streams") or [{}])[0]
    num, _, den = (stream.get("r_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1)

    return {
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": fps,
        "duration": float(data.get("format", {}).get("duration", 0)),
    }
