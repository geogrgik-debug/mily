"""Разбор ролика в один лист: раскадровка, крючок, звук, соответствие правилам.

Нужен, чтобы ролик можно было обсуждать, не пересылая видео. На выходе
PNG-лист, по которому видно композицию, читаемость первых кадров и куда
попадает интерфейс площадки.

Отдельно вынесена первая секунда: в ленте решение о свайпе принимается
там, и если крючок не читается за это время, остальное неважно.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .shell import require, run, probe

OUT_DIR = Path("out/inspect")

# Доли кадра, которые перекрывает интерфейс вертикальной ленты.
SAFE_RIGHT = 0.22
SAFE_BOTTOM = 0.18
SAFE_TOP = 0.10

GRID_COLS = 4
THUMB_W = 240


@dataclass
class Report:
    path: Path
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool
    mean_volume: float | None
    sheet: Path | None

    @property
    def aspect(self) -> str:
        if not self.height:
            return "?"
        r = self.width / self.height
        if abs(r - 9 / 16) < 0.02:
            return "9:16 вертикаль"
        if abs(r - 16 / 9) < 0.02:
            return "16:9 горизонталь"
        return f"{r:.2f}:1"

    def warnings(self) -> list[str]:
        out = []
        if self.aspect != "9:16 вертикаль":
            out.append(f"не вертикаль ({self.aspect}) — в ленте обрежется или ляжет в рамки")
        if self.duration < 10:
            out.append(f"длительность {self.duration:.1f} c — кампания не принимает короче 10 c")
        if not self.has_audio:
            out.append("нет звуковой дорожки — баннер со звуком вставить будет некуда")
        elif self.mean_volume is not None and self.mean_volume < -30:
            out.append(f"звук очень тихий ({self.mean_volume:.1f} dB)")
        if self.fps and self.fps < 24:
            out.append(f"низкий fps ({self.fps:.0f}) — движение будет рваным")
        return out


def mean_volume(path: Path) -> float | None:
    # volumedetect печатает итог на уровне info: с -v error его не видно.
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    for line in proc.stderr.splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0].strip())
            except (IndexError, ValueError):
                return None
    return None


def has_audio(path: Path) -> bool:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return bool(proc.stdout.strip())


def _grab(src: Path, at: float, dst: Path, overlay_zones: bool) -> bool:
    vf = []
    if overlay_zones:
        vf += [
            f"drawbox=x=iw*{1 - SAFE_RIGHT}:y=0:w=iw*{SAFE_RIGHT}:h=ih"
            ":color=red@0.25:t=fill",
            f"drawbox=x=0:y=ih*{1 - SAFE_BOTTOM}:w=iw:h=ih*{SAFE_BOTTOM}"
            ":color=red@0.25:t=fill",
            f"drawbox=x=0:y=0:w=iw:h=ih*{SAFE_TOP}:color=red@0.25:t=fill",
        ]
    vf.append(f"scale={THUMB_W}:-2")

    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(src),
           "-vframes", "1", "-vf", ",".join(vf), str(dst)]
    return subprocess.run(cmd, capture_output=True).returncode == 0


def contact_sheet(
    src: Path, duration: float, out: Path, *, frames: int = 8, zones: bool = True
) -> Path | None:
    """Лист кадров: первая секунда подробно, дальше равномерно по ролику."""
    require("ffmpeg")
    tmp = out.parent / f".{out.stem}_frames"
    tmp.mkdir(parents=True, exist_ok=True)

    # Крючок важнее всего, поэтому начало сэмплируем плотнее.
    early = [0.0, 0.35, 0.7, 1.2]
    rest_count = max(0, frames - len(early))
    rest = [
        duration * (i + 1) / (rest_count + 1) for i in range(rest_count)
    ] if rest_count else []
    times = [t for t in early + rest if t < max(duration - 0.05, 0.05)]

    shots = []
    for i, t in enumerate(times):
        f = tmp / f"{i:02d}.png"
        if _grab(src, t, f, zones):
            shots.append(f)

    if not shots:
        return None

    rows = (len(shots) + GRID_COLS - 1) // GRID_COLS
    inputs = []
    for f in shots:
        inputs += ["-i", str(f)]
    labels = "".join(f"[{i}]" for i in range(len(shots)))
    graph = f"{labels}xstack=inputs={len(shots)}:grid={GRID_COLS}x{rows}:fill=black"

    out.parent.mkdir(parents=True, exist_ok=True)
    ok = run(["ffmpeg", "-y", "-v", "error", *inputs,
              "-filter_complex", graph, str(out)]).returncode == 0

    for f in shots:
        f.unlink(missing_ok=True)
    tmp.rmdir()

    return out if ok else None


def inspect(src: Path, *, out_dir: Path = OUT_DIR, frames: int = 8,
            zones: bool = True) -> Report:
    require("ffprobe")
    info = probe(str(src))
    audio = has_audio(src)

    sheet = contact_sheet(
        src, info["duration"], out_dir / f"{src.stem}.png",
        frames=frames, zones=zones,
    )

    return Report(
        path=src,
        width=info["width"] or 0,
        height=info["height"] or 0,
        fps=info["fps"],
        duration=info["duration"],
        has_audio=audio,
        mean_volume=mean_volume(src) if audio else None,
        sheet=sheet,
    )
