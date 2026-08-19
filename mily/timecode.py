"""Разбор таймкодов: 90, '1:30', '01:02:03', '1:02:03.5'."""

from __future__ import annotations


def parse(value) -> float:
    """Строку или число превратить в секунды."""
    if isinstance(value, (int, float)):
        return float(value)

    parts = str(value).strip().split(":")
    if len(parts) > 3:
        raise ValueError(f"не понимаю таймкод: {value!r}")

    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def stamp(seconds: float) -> str:
    """Секунды -> компактный ярлык для имени файла: 1h02m03s."""
    total = int(seconds)
    hours, minutes, secs = total // 3600, (total % 3600) // 60, total % 60
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    return f"{minutes:02d}m{secs:02d}s"
