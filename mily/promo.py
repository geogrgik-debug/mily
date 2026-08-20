"""Вставка анимированного баннера кампании по её правилам.

Отличается от brand.py принципиально: там статичная картинка в углу, тут
анимация со звуком в середине ролика, занимающая четверть экрана. Разные
требования — разный модуль.

Правила, зашитые здесь:
    - баннер строго в середине ролика
    - один баннер на каждую полную минуту хронометража
    - не меньше 25% площади экрана
    - обязательно со звуком
    - ускорение баннера не больше 1.2x
    - ролик не короче 10 секунд

Площадь считается именно как площадь, а не как доля ширины: баннер
пропорции шире 2.25:1 не способен занять четверть кадра 1080x1920 даже
растянутый во всю ширину.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .shell import require, run, probe

MIN_VIDEO_SECONDS = 10.0
MIN_AREA_RATIO = 0.25
MAX_SPEED = 1.2
SECONDS_PER_BANNER = 60.0

# Небольшой запас над минимумом: приёмка может считать площадь чуть иначе,
# а недобор по площади — это ноль за ролик.
DEFAULT_AREA_RATIO = 0.27


class RuleViolation(Exception):
    """Ролик или настройки не проходят правила кампании."""


@dataclass
class PromoSpec:
    banner: Path
    area_ratio: float = DEFAULT_AREA_RATIO
    speed: float = 1.0

    def validate(self) -> None:
        if not self.banner.exists():
            raise RuleViolation(f"нет файла баннера: {self.banner}")
        if self.speed > MAX_SPEED:
            raise RuleViolation(
                f"ускорение {self.speed} больше разрешённых {MAX_SPEED}"
            )
        if self.speed <= 0:
            raise RuleViolation("ускорение должно быть больше нуля")
        if self.area_ratio < MIN_AREA_RATIO:
            raise RuleViolation(
                f"площадь {self.area_ratio:.0%} меньше требуемых {MIN_AREA_RATIO:.0%}"
            )


def banner_size(
    video_w: int, video_h: int, banner_w: int, banner_h: int, area_ratio: float
) -> tuple[int, int]:
    """Размер баннера под заданную долю площади кадра, с сохранением пропорции.

    Если запрошенная доля не помещается, баннер прижимается к максимуму,
    который влезает. Отказ — только когда даже максимум ниже минимума
    кампании: широкий ассет физически не способен занять четверть
    вертикального кадра.
    """
    target_area = video_w * video_h * area_ratio
    aspect = banner_w / banner_h

    width = (target_area * aspect) ** 0.5
    height = width / aspect

    if width > video_w or height > video_h:
        # прижимаем к тому, что влезает, сохраняя пропорцию
        scale = min(video_w / width, video_h / height)
        width *= scale
        height *= scale

        got = width * height / (video_w * video_h)
        if got < MIN_AREA_RATIO:
            max_aspect = video_w / (video_h * MIN_AREA_RATIO)
            raise RuleViolation(
                f"баннер {banner_w}x{banner_h} (пропорция {aspect:.2f}:1) не способен "
                f"занять {MIN_AREA_RATIO:.0%} кадра {video_w}x{video_h}: даже во всю "
                f"ширину выходит {got:.1%}. Нужен ассет не шире {max_aspect:.2f}:1"
            )
        print(f"[promo] запрошенные {area_ratio:.0%} не влезают, "
              f"прижимаю к {got:.1%} — минимум кампании соблюдён")

    # чётные стороны ради yuv420p
    return max(2, int(width) // 2 * 2), max(2, int(height) // 2 * 2)


def insertion_points(video_seconds: float, banner_seconds: float) -> list[float]:
    """Моменты вставки: по одному баннеру на каждую полную минуту.

    Для ролика короче минуты — ровно середина. Для длинных — середина
    каждого минутного отрезка, чтобы баннеры не слипались.
    """
    count = max(1, int(video_seconds // SECONDS_PER_BANNER) + (
        1 if video_seconds % SECONDS_PER_BANNER else 0
    ))
    if video_seconds <= SECONDS_PER_BANNER:
        count = 1

    points = []
    segment = video_seconds / count
    for i in range(count):
        centre = segment * i + segment / 2
        start = centre - banner_seconds / 2
        # не вылезаем за края ролика
        start = max(0.0, min(start, video_seconds - banner_seconds))
        points.append(round(start, 3))
    return points


def build_filtergraph(
    points: list[float], bw: int, bh: int, banner_seconds: float, speed: float
) -> str:
    """Граф: баннер масштабируется, копируется по числу вставок, каждая
    сдвигается во времени и накладывается по центру кадра.

    Запятые внутри between() экранируются: в filter_complex запятая —
    разделитель фильтров, и неэкранированная разносит весь граф.
    """
    n = len(points)
    parts = [f"[1:v]setpts=PTS/{speed:.4f},scale={bw}:{bh},format=rgba[bn]"]

    if n > 1:
        labels = "".join(f"[b{i}]" for i in range(n))
        parts.append(f"[bn]split={n}{labels}")
    else:
        parts.append("[bn]null[b0]")

    current = "[0:v]"
    for i, start in enumerate(points):
        end = start + banner_seconds
        parts.append(f"[b{i}]setpts=PTS+{start:.3f}/TB[d{i}]")
        out = "[v]" if i == n - 1 else f"[v{i}]"
        parts.append(
            f"{current}[d{i}]overlay=x=(W-w)/2:y=(H-h)/2"
            f":enable=between(t\\,{start:.3f}\\,{end:.3f}){out}"
        )
        current = out

    return ";".join(parts)


def build_audiograph(points: list[float], speed: float, has_audio: bool) -> str:
    """Звук баннера обязателен, поэтому подмешиваем его на каждой вставке."""
    parts = []
    labels = []

    for i, start in enumerate(points):
        delay_ms = int(start * 1000)
        chain = "[1:a]"
        if abs(speed - 1.0) > 1e-6:
            chain += f"atempo={speed:.4f},"
        else:
            chain += "anull,"
        chain += f"adelay={delay_ms}|{delay_ms}[ba{i}]"
        parts.append(chain)
        labels.append(f"[ba{i}]")

    inputs = ("[0:a]" if has_audio else "") + "".join(labels)
    count = len(labels) + (1 if has_audio else 0)
    parts.append(f"{inputs}amix=inputs={count}:duration=first:dropout_transition=0[a]")
    return ";".join(parts)


def apply(
    src: Path,
    dst: Path,
    spec: PromoSpec,
    *,
    crf: int = 20,
    x264_preset: str = "medium",
) -> Path:
    """Вставить баннер. Кидает RuleViolation, если ролик не проходит правила."""
    require("ffmpeg")
    spec.validate()

    video = probe(str(src))
    if video["duration"] < MIN_VIDEO_SECONDS:
        raise RuleViolation(
            f"{src.name}: длительность {video['duration']:.1f} c меньше "
            f"минимальных {MIN_VIDEO_SECONDS:.0f} c"
        )

    banner = probe(str(spec.banner))
    bw, bh = banner_size(
        video["width"], video["height"],
        banner["width"], banner["height"],
        spec.area_ratio,
    )
    banner_seconds = banner["duration"] / spec.speed
    if banner_seconds >= video["duration"]:
        raise RuleViolation(
            f"{src.name}: баннер ({banner_seconds:.1f} c) не короче ролика "
            f"({video['duration']:.1f} c)"
        )

    points = insertion_points(video["duration"], banner_seconds)
    has_audio = _has_audio(src)

    graph = (
        build_filtergraph(points, bw, bh, banner_seconds, spec.speed)
        + ";"
        + build_audiograph(points, spec.speed, has_audio)
    )

    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src), "-i", str(spec.banner),
        "-filter_complex", graph,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", x264_preset, "-crf", str(crf),
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-map_metadata", "-1",
        str(dst),
    ]

    area = bw * bh / (video["width"] * video["height"])
    print(f"[promo] {src.name}: баннер {bw}x{bh} ({area:.0%} кадра), "
          f"вставок {len(points)} в {', '.join(f'{p:.1f}c' for p in points)}")

    if run(cmd).returncode != 0:
        raise RuleViolation(f"{src.name}: ffmpeg не справился")
    return dst


def _has_audio(path: Path) -> bool:
    import subprocess

    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return bool(proc.stdout.strip())
