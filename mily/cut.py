"""Нарезка моментов из полных матчей по таймкодам."""

from __future__ import annotations

from pathlib import Path

from .shell import require, run
from .timecode import parse, stamp

CUT_DIR = Path("clips/cut")

# Небольшой запас с обеих сторон — потом варианты подрежут его по-разному,
# и точки входа у роликов перестанут совпадать покадрово.
PAD = 0.6


def cut_moment(
    source: Path,
    at: float,
    duration: float,
    *,
    out_dir: Path = CUT_DIR,
    label: str = "clip",
) -> Path | None:
    """Вырезать один момент. Перекодируем — иначе резы едут по ключевым кадрам."""
    require("ffmpeg")
    out_dir.mkdir(parents=True, exist_ok=True)

    start = max(0.0, at - PAD)
    out = out_dir / f"{label}_{stamp(at)}.mp4"

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-i", str(source),
        "-t", f"{duration + PAD * 2:.3f}",
        "-c:v", "libx264", "-preset", "slow", "-crf", "16",
        "-pix_fmt", "yuv420p",
        "-an",
        "-map_metadata", "-1",
        str(out),
    ]
    print(f"[cut] {source.name} @ {at:.1f}s -> {out.name}")
    if run(cmd).returncode != 0:
        return None
    return out


def cut_from_marks(marks: list[dict], *, out_dir: Path = CUT_DIR) -> list[Path]:
    """Обработать config/marks.yaml целиком."""
    results: list[Path] = []

    for entry in marks:
        source = Path(entry["source"])
        if not source.exists():
            print(f"[skip] нет исходника: {source}")
            continue

        player = entry.get("player", "clip")
        for moment in entry.get("moments", []):
            at = parse(moment["at"])
            duration = float(moment.get("dur", 5))
            tag = moment.get("tag", "")
            label = f"{player}_{tag}".strip("_") or player

            out = cut_moment(source, at, duration, out_dir=out_dir, label=label)
            if out:
                results.append(out)

    return results
