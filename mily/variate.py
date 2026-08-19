"""Размножение одного клипа в N непохожих вариантов.

Смысл модуля: TikTok матчит загрузки по видео-отпечатку, и одинаковый
ролик на десяти аккаунтах режется по охватам. Здесь из одной нарезки
собирается пачка версий, у каждой свои цветокор, кроп, скорость, зерно
и точки входа — для фингерпринта это разные видео.

Параметры выводятся детерминированно из (seed, имя клипа, индекс):
один и тот же вызов всегда даёт тот же результат, и по манифесту
всегда видно, какой вариант ушёл на какой аккаунт.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, asdict
from pathlib import Path

from .shell import require, run, probe

OUT_DIR = Path("out")


@dataclass
class Variation:
    """Полный набор параметров одного варианта."""

    index: int
    speed: float          # множитель скорости
    zoom: float           # доля кадра, которая остаётся после кропа
    pan_x: float          # смещение кропа, -1..1
    pan_y: float
    brightness: float
    contrast: float
    saturation: float
    gamma: float
    shadow_r: float       # сплит-тон: тени
    shadow_g: float
    shadow_b: float
    high_r: float         # сплит-тон: света
    high_g: float
    high_b: float
    hue: float            # сдвиг оттенка, градусы
    sharpen: float
    grain: float
    vignette: float       # 0 = выключена
    head_trim: float      # сколько срезать спереди, сек
    tail_trim: float
    crf: int


def plan(clip: Path, index: int, *, seed: int = 0) -> Variation:
    """Собрать параметры варианта. Детерминированно по seed+клипу+индексу."""
    rng = random.Random(f"{seed}:{clip.stem}:{index}")

    return Variation(
        index=index,
        speed=round(rng.uniform(0.92, 1.08), 4),
        zoom=round(rng.uniform(0.80, 0.96), 4),
        pan_x=round(rng.uniform(-0.5, 0.5), 4),
        pan_y=round(rng.uniform(-0.4, 0.4), 4),
        brightness=round(rng.uniform(-0.05, 0.05), 4),
        contrast=round(rng.uniform(0.96, 1.22), 4),
        saturation=round(rng.uniform(0.90, 1.40), 4),
        gamma=round(rng.uniform(0.92, 1.08), 4),
        shadow_r=round(rng.uniform(-0.12, 0.04), 4),
        shadow_g=round(rng.uniform(-0.06, 0.06), 4),
        shadow_b=round(rng.uniform(-0.02, 0.14), 4),
        high_r=round(rng.uniform(-0.04, 0.12), 4),
        high_g=round(rng.uniform(-0.06, 0.06), 4),
        high_b=round(rng.uniform(-0.12, 0.04), 4),
        hue=round(rng.uniform(-8, 8), 2),
        sharpen=round(rng.uniform(0.3, 1.2), 2),
        grain=round(rng.uniform(3, 13), 1),
        vignette=rng.choice([0.0, 0.0, 4.0, 5.0, 6.0]),
        head_trim=round(rng.uniform(0.0, 0.45), 3),
        tail_trim=round(rng.uniform(0.0, 0.45), 3),
        crf=rng.randint(18, 23),
    )


def build_filter(v: Variation, *, width: int = 1080, height: int = 1920) -> str:
    """Собрать цепочку фильтров ffmpeg под вариант."""
    z = v.zoom
    steps = [
        # скорость
        f"setpts={1 / v.speed:.5f}*PTS",
        # зум-кроп со смещением; trunc до чётного — иначе кодек ругается
        (
            f"crop=w=trunc(iw*{z:.4f}/2)*2:h=trunc(ih*{z:.4f}/2)*2"
            f":x=(iw-iw*{z:.4f})/2*(1{v.pan_x:+.4f})"
            f":y=(ih-ih*{z:.4f})/2*(1{v.pan_y:+.4f})"
        ),
        # вертикаль 9:16 с заполнением
        f"scale={width}:{height}:force_original_aspect_ratio=increase",
        f"crop={width}:{height}",
        # цветокор
        (
            f"eq=brightness={v.brightness:.4f}:contrast={v.contrast:.4f}"
            f":saturation={v.saturation:.4f}:gamma={v.gamma:.4f}"
        ),
        (
            f"colorbalance=rs={v.shadow_r:.4f}:gs={v.shadow_g:.4f}:bs={v.shadow_b:.4f}"
            f":rh={v.high_r:.4f}:gh={v.high_g:.4f}:bh={v.high_b:.4f}"
        ),
        f"hue=h={v.hue:.2f}",
        f"unsharp=5:5:{v.sharpen:.2f}",
        f"noise=alls={v.grain:.0f}:allf=t+u",
    ]

    if v.vignette:
        steps.append(f"vignette=a=PI/{v.vignette:.0f}")

    steps += ["fps=30", "format=yuv420p"]
    return ",".join(steps)


def render(
    clip: Path,
    v: Variation,
    *,
    out_dir: Path,
    audio: Path | None = None,
    audio_offset: float = 0.0,
) -> Path | None:
    """Отрендерить один вариант."""
    require("ffmpeg")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"v{v.index:02d}.mp4"

    info = probe(str(clip))
    duration = max(0.5, info["duration"] - v.head_trim - v.tail_trim)

    cmd = ["ffmpeg", "-y", "-ss", f"{v.head_trim:.3f}", "-i", str(clip)]
    if audio:
        cmd += ["-ss", f"{audio_offset:.3f}", "-i", str(audio)]

    cmd += [
        "-t", f"{duration:.3f}",
        "-vf", build_filter(v),
        "-c:v", "libx264", "-preset", "medium", "-crf", str(v.crf),
        "-profile:v", "high", "-level", "4.1",
        "-movflags", "+faststart",
        # чистим метаданные: одинаковые теги на всей сетке — лишний след
        "-map_metadata", "-1",
    ]

    if audio:
        cmd += ["-map", "0:v:0", "-map", "1:a:0",
                "-c:a", "aac", "-b:a", "192k", "-shortest"]
    else:
        cmd += ["-an"]

    cmd.append(str(out))

    print(f"[render] {clip.name} -> {out.name}  "
          f"(speed {v.speed}, zoom {v.zoom}, crf {v.crf})")
    if run(cmd).returncode != 0:
        return None
    return out


def render_batch(
    clip: Path,
    count: int,
    *,
    out_root: Path = OUT_DIR,
    seed: int = 0,
    audio: Path | None = None,
) -> list[Path]:
    """Сделать `count` вариантов клипа и записать манифест."""
    out_dir = out_root / clip.stem
    rendered: list[Path] = []
    manifest = []

    for i in range(count):
        v = plan(clip, i, seed=seed)
        offset = random.Random(f"{seed}:{clip.stem}:{i}:audio").uniform(0, 20)
        out = render(clip, v, out_dir=out_dir,
                     audio=audio, audio_offset=offset if audio else 0.0)
        if out:
            rendered.append(out)
            manifest.append({"file": out.name, "source": str(clip), **asdict(v)})

    if manifest:
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return rendered
