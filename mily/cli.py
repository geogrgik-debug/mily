"""CLI: python -m mily <команда>"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from . import fetch as fetch_mod
from . import cut as cut_mod
from . import variate as variate_mod
from .shell import MissingBinary

CONFIG_DIR = Path("config")


def load_yaml(path: Path):
    if not path.exists():
        print(f"[!] нет файла {path}", file=sys.stderr)
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def cmd_fetch(args) -> int:
    if args.url:
        fetch_mod.download(args.url)
        return 0

    data = load_yaml(CONFIG_DIR / "sources.yaml")
    if not data:
        return 1

    sources = data.get("sources", [])
    if args.only:
        sources = [s for s in sources if s.get("slug") == args.only]
        if not sources:
            print(f"[!] в sources.yaml нет slug={args.only!r}", file=sys.stderr)
            return 1

    fetch_mod.download_all(sources)
    return 0


def cmd_cut(args) -> int:
    data = load_yaml(CONFIG_DIR / "marks.yaml")
    if not data:
        return 1

    marks = data.get("marks", [])
    if args.player:
        marks = [m for m in marks if m.get("player") == args.player]

    results = cut_mod.cut_from_marks(marks)
    print(f"\nнарезано клипов: {len(results)}")
    return 0


def _clips(pattern: str) -> list[Path]:
    return sorted(p for p in Path("clips/cut").glob(pattern) if p.is_file())


def cmd_variate(args) -> int:
    clips = _clips(args.glob)
    if not clips:
        print(f"[!] в clips/cut ничего не нашлось по {args.glob!r}", file=sys.stderr)
        return 1

    audio = Path(args.audio) if args.audio else None
    if audio and not audio.exists():
        print(f"[!] нет аудио: {audio}", file=sys.stderr)
        return 1

    total = 0
    for clip in clips:
        total += len(variate_mod.render_batch(
            clip, args.count, seed=args.seed, audio=audio
        ))

    print(f"\nготово вариантов: {total}  (в out/)")
    return 0


def cmd_plan(args) -> int:
    """Сухой прогон: показать параметры и фильтры, ничего не рендеря."""
    clips = _clips(args.glob) or [Path("clips/cut/example.mp4")]

    for clip in clips[: args.limit]:
        print(f"\n=== {clip.name} ===")
        for i in range(args.count):
            v = variate_mod.plan(clip, i, seed=args.seed)
            print(f"\n[v{i:02d}] speed={v.speed} zoom={v.zoom} "
                  f"sat={v.saturation} hue={v.hue} grain={v.grain} crf={v.crf}")
            print("  " + variate_mod.build_filter(v))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mily",
        description="Пайплайн футбольных эдитов: скачать -> нарезать -> размножить",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="скачать исходники (config/sources.yaml)")
    p.add_argument("--url", help="разовая ссылка вместо конфига")
    p.add_argument("--only", help="взять из конфига только этот slug")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("cut", help="нарезать моменты (config/marks.yaml)")
    p.add_argument("--player", help="только этот игрок")
    p.set_defaults(func=cmd_cut)

    p = sub.add_parser("variate", help="размножить нарезки в варианты")
    p.add_argument("-n", "--count", type=int, default=10, help="вариантов на клип")
    p.add_argument("--glob", default="*.mp4", help="фильтр по clips/cut")
    p.add_argument("--audio", help="трек для подложки")
    p.add_argument("--seed", type=int, default=0, help="сид рандомизации")
    p.set_defaults(func=cmd_variate)

    p = sub.add_parser("plan", help="показать параметры вариантов без рендера")
    p.add_argument("-n", "--count", type=int, default=3)
    p.add_argument("--glob", default="*.mp4")
    p.add_argument("--limit", type=int, default=2, help="сколько клипов показать")
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=cmd_plan)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MissingBinary as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
