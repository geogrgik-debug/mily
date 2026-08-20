"""Финишный проход: убрать с картинки следы генерации.

Сгенерированное видео выдают себя не сюжетом, а фактурой: слишком чистый
кадр, идеально гладкая камера, ровный «гиперреальный» свет, отсутствие
оптических дефектов. Глаз читает это за долю секунды, даже когда зритель
не может объяснить, что не так.

Здесь собран проход, который возвращает картинке физику оптики: плёночная
кривая с приподнятыми тенями, хроматическая аберрация, ореол на светах,
дрожание кадра в рамке, зерно, лёгкая расфокусировка и виньетка.

Ни один фильтр не делает погоды поодиночке — работает сумма.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .shell import require, run

# Пресеты по силе воздействия. heavy заметен как приём, light — почти нет.
PRESETS = {
    "light":  {"grain": 3.0,  "aberration": 0.6, "halation": 0.14, "weave": 0.7, "soften": 0.15, "vignette": 11.0},
    "medium": {"grain": 6.0,  "aberration": 1.0, "halation": 0.22, "weave": 1.2, "soften": 0.30, "vignette": 8.5},
    "heavy":  {"grain": 10.0, "aberration": 1.8, "halation": 0.34, "weave": 2.0, "soften": 0.50, "vignette": 6.5},
}


@dataclass
class FilmLook:
    grain: float
    aberration: float
    halation: float
    weave: float
    soften: float
    vignette: float

    @classmethod
    def preset(cls, name: str) -> "FilmLook":
        if name not in PRESETS:
            raise ValueError(f"нет пресета {name!r}, есть: {', '.join(PRESETS)}")
        return cls(**PRESETS[name])


def build_filtergraph(look: FilmLook) -> str:
    """Собрать filter_complex. Ореол требует split/blend, поэтому не -vf."""
    chain = []

    # Плёночная S-кривая: слегка уплотнить тени, приподнять света.
    chain.append("curves=all='0/0 0.25/0.22 0.5/0.5 0.75/0.78 1/1'")

    # Разведение теней в холод, светов в тепло: у настоящей оптики
    # каналы никогда не сходятся идеально.
    chain.append("colorbalance=rs=-0.03:bs=0.05:rh=0.04:bh=-0.03")

    # Хроматическая аберрация — расхождение каналов к краям кадра.
    if look.aberration > 0:
        shift = max(1, int(round(look.aberration)))
        chain.append(f"rgbashift=rh=-{shift}:bh={shift}")

    # Дрожание кадра в рамке: у генерации камера неестественно гладкая.
    # Период по x и y разный, иначе движение читается как маятник.
    if look.weave > 0:
        amp = look.weave
        pad = int(amp * 2) + 2
        chain.append(
            f"crop=iw-{pad * 2}:ih-{pad * 2}"
            f":{pad}+{amp:.2f}*sin(n/13):{pad}+{amp:.2f}*cos(n/17)"
        )

    # Лёгкая расфокусировка: генерация держит резким весь кадр целиком,
    # реальный объектив так не умеет.
    if look.soften > 0:
        chain.append(f"unsharp=3:3:-{look.soften:.2f}")

    if look.vignette > 0:
        chain.append(f"vignette=a=PI/{look.vignette:.1f}")

    # Подъём чёрного — обязательно ПОСЛЕ виньетки, иначе она давит углы
    # обратно в абсолютный ноль и весь смысл теряется. Плёнка не даёт
    # чистого чёрного нигде, и глаз цепляется за это первым делом.
    chain.append("curves=all='0/0.035 0.5/0.52 1/0.98'")

    # Зерно последним, чтобы его не размывали предыдущие фильтры.
    if look.grain > 0:
        chain.append(f"noise=alls={look.grain:.0f}:allf=t+u")

    chain.append("format=yuv420p")
    base = ",".join(chain)

    if look.halation <= 0:
        return f"[0:v]{base}[v]"

    # Ореол: света растекаются по кадру. Берём только верхнюю часть
    # диапазона, размываем и подмешиваем по screen.
    # Уменьшить -> размыть -> вернуть обратно: на глаз как полноразмерный
    # блюр, работы в разы меньше. Возврат делаем через scale2ref, потому что
    # scale=iw*4 не восстанавливает размер, не кратный четырём, и blend
    # падает на несовпадении сторон.
    return (
        f"[0:v]{base}[base];"
        f"[base]split[keep][glow];"
        f"[glow]curves=all='0/0 0.72/0 1/1',"
        f"scale=iw/4:ih/4,gblur=sigma=6[small];"
        f"[small][keep]scale2ref=w=iw:h=ih[bloom][keep2];"
        f"[keep2][bloom]blend=all_mode=screen:all_opacity={look.halation:.2f},"
        f"format=yuv420p[v]"
    )


def apply(
    src: Path,
    dst: Path,
    look: FilmLook,
    *,
    crf: int = 18,
    x264_preset: str = "medium",
) -> Path | None:
    """Прогнать файл через финишный проход."""
    require("ffmpeg")
    dst.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-filter_complex", build_filtergraph(look),
        "-map", "[v]",
    ]
    # Звук пропускаем как есть, если он был.
    cmd += ["-map", "0:a?", "-c:a", "copy"]
    cmd += [
        "-c:v", "libx264", "-preset", x264_preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-map_metadata", "-1",
        str(dst),
    ]

    print(f"[finish] {src.name} -> {dst.name}")
    if run(cmd).returncode != 0:
        return None
    return dst
