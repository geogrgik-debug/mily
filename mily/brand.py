"""Наложение баннера на готовые ролики.

Отдельный модуль, потому что баннер живёт по своим правилам: у вертикальных
лент интерфейс площадки перекрывает часть кадра, и всё, что попало под него,
зритель просто не увидит.

Безопасные зоны для 1080x1920 (доля от стороны):
    справа   ~22%  — колонка кнопок (лайк, коммент, шер, звук)
    снизу    ~18%  — подпись, ник, музыка
    сверху   ~10%  — поиск, вкладки

Позиции ниже расставлены так, чтобы в эти зоны не залезать.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from .shell import require, run, probe

# Доли безопасных полей от ширины/высоты кадра.
SAFE_RIGHT = 0.22
SAFE_BOTTOM = 0.18
SAFE_TOP = 0.10

POSITIONS = ("top-left", "top-center", "upper-third", "center", "lower-third")


@dataclass
class Banner:
    image: Path
    position: str = "lower-third"
    width_ratio: float = 0.34      # ширина баннера от ширины кадра
    opacity: float = 1.0
    margin: float = 0.04           # отступ от края, доля от ширины
    fade_in: float = 0.0           # секунды на проявление, 0 = сразу


def _anchor(
    position: str,
    width: int,
    height: int,
    banner_w: int,
    banner_h: int,
    margin_px: int,
) -> tuple[int, int]:
    """Координаты левого верхнего угла баннера, числами.

    Именно числами, а не выражениями ffmpeg: запятая внутри min()/max()
    парсится как разделитель фильтров и разносит весь граф.
    """
    top = int(height * SAFE_TOP) + margin_px
    bottom_limit = height - int(height * SAFE_BOTTOM)
    right_limit = width - int(width * SAFE_RIGHT)

    centered = (width - banner_w) // 2

    places = {
        "top-left":    (margin_px, top),
        "top-center":  (centered, top),
        "upper-third": (centered, int(height * 0.24)),
        "center":      (centered, (height - banner_h) // 2),
        # над подписью и левее колонки кнопок
        "lower-third": (min(centered, right_limit - margin_px - banner_w),
                        bottom_limit - margin_px - banner_h),
    }
    if position not in places:
        raise ValueError(f"нет позиции {position!r}, есть: {', '.join(POSITIONS)}")

    x, y = places[position]
    # не даём уехать за кадр на узких баннерах и больших отступах
    return max(0, min(x, width - banner_w)), max(0, min(y, height - banner_h))


def build_filtergraph(
    banner: Banner, width: int, height: int, banner_w: int, banner_h: int
) -> str:
    margin_px = int(width * banner.margin)
    x, y = _anchor(banner.position, width, height, banner_w, banner_h, margin_px)

    steps = [f"[1:v]scale={banner_w}:{banner_h}"]
    if banner.opacity < 1.0:
        # colorchannelmixer правит альфу целиком, поэтому формат с альфой явно.
        steps.append(f"format=rgba,colorchannelmixer=aa={banner.opacity:.3f}")
    if banner.fade_in > 0:
        steps.append(f"format=rgba,fade=t=in:st=0:d={banner.fade_in:.2f}:alpha=1")

    return f"{','.join(steps)}[bn];[0:v][bn]overlay=x={x}:y={y}[v]"


def apply(
    src: Path,
    dst: Path,
    banner: Banner,
    *,
    crf: int = 20,
    x264_preset: str = "medium",
) -> Path | None:
    require("ffmpeg")
    if not banner.image.exists():
        print(f"[!] нет файла баннера: {banner.image}")
        return None

    dst.parent.mkdir(parents=True, exist_ok=True)
    info = probe(str(src))
    width, height = info["width"], info["height"]
    if not width or not height:
        print(f"[!] не читаются размеры: {src}")
        return None

    img = probe(str(banner.image))
    if not img["width"] or not img["height"]:
        print(f"[!] не читаются размеры баннера: {banner.image}")
        return None

    # чётные стороны: x264 не любит нечётные размеры в yuv420p
    banner_w = max(2, int(width * banner.width_ratio) // 2 * 2)
    banner_h = max(2, int(banner_w * img["height"] / img["width"]) // 2 * 2)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-i", str(banner.image),
        "-filter_complex", build_filtergraph(banner, width, height, banner_w, banner_h),
        "-map", "[v]", "-map", "0:a?", "-c:a", "copy",
        "-c:v", "libx264", "-preset", x264_preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-map_metadata", "-1",
        str(dst),
    ]
    print(f"[brand] {src.name} -> {dst.name}  ({banner.position})")
    if run(cmd).returncode != 0:
        return None
    return dst


def apply_batch(
    files: list[Path],
    dst_dir: Path,
    image: Path,
    *,
    positions: list[str] | None = None,
    seed: int = 0,
    **kwargs,
) -> list[Path]:
    """Пачкой, с раскиданными позициями.

    Одинаковый баннер в одной точке на всех роликах — это подпись сетки.
    Позиция и размер разводятся детерминированно по имени файла.
    """
    pool = positions or ["lower-third", "upper-third", "top-center"]
    done: list[Path] = []

    for f in files:
        rng = random.Random(f"{seed}:{f.stem}")
        banner = Banner(
            image=image,
            position=rng.choice(pool),
            width_ratio=round(rng.uniform(0.28, 0.40), 3),
            opacity=round(rng.uniform(0.85, 1.0), 3),
            fade_in=round(rng.uniform(0.0, 0.5), 2),
        )
        out = apply(f, dst_dir / f.name, banner, **kwargs)
        if out:
            done.append(out)

    return done
