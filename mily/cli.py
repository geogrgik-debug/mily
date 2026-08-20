"""CLI: python -m mily <команда>"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from . import fetch as fetch_mod
from . import cut as cut_mod
from . import variate as variate_mod
from . import anim as anim_mod
from . import finish as finish_mod
from . import brand as brand_mod
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


def cmd_anim(args) -> int:
    brief_path = CONFIG_DIR / "brief.yaml"
    shots_path = Path("config/shots.json")

    if args.step == "prompt":
        brief = anim_mod.load_brief(brief_path)
        print(anim_mod.prompt_template(brief, args.scenes))
        return 0

    if args.step == "shots":
        brief = anim_mod.load_brief(brief_path)
        if not brief.shots:
            print("[!] в brief.yaml пустой shots — сначала сделай раскадровку",
                  file=sys.stderr)
            return 1
        anim_mod.save_shots(brief, shots_path)
        print(f"раскадровка записана: {shots_path} ({len(brief.shots)} сцен)")
        return 0

    if args.step == "assemble":
        data = anim_mod.load_shots(shots_path)
        footage = Path(args.footage)
        try:
            script = anim_mod.build_aescript(data, footage)
        except ValueError as exc:
            print(f"[!] {exc}", file=sys.stderr)
            return 1
        out = Path(args.out)
        out.write_text(script, encoding="utf-8")
        print(f"скрипт сборки готов: {out}")
        print("в After Effects: File > Scripts > Run Script File")
        return 0

    return 1


def cmd_finish(args) -> int:
    try:
        look = finish_mod.FilmLook.preset(args.preset)
    except ValueError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1

    src = Path(args.src)
    out_dir = Path(args.out)

    files = [src] if src.is_file() else sorted(src.glob(args.glob))
    if not files:
        print(f"[!] нечего обрабатывать: {src}", file=sys.stderr)
        return 1

    done = 0
    for f in files:
        if finish_mod.apply(f, out_dir / f.name, look, crf=args.crf):
            done += 1

    print(f"\nобработано: {done} из {len(files)}  (в {out_dir}/)")
    return 0 if done else 1


def cmd_brand(args) -> int:
    image = Path(args.image)
    if not image.exists():
        print(f"[!] нет баннера: {image}", file=sys.stderr)
        return 1

    src = Path(args.src)
    files = [src] if src.is_file() else sorted(src.glob(args.glob))
    if not files:
        print(f"[!] нечего обрабатывать: {src}", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    if args.position:
        banner = brand_mod.Banner(
            image=image,
            position=args.position,
            width_ratio=args.width,
            opacity=args.opacity,
        )
        done = [d for f in files
                if (d := brand_mod.apply(f, out_dir / f.name, banner, crf=args.crf))]
    else:
        done = brand_mod.apply_batch(files, out_dir, image, seed=args.seed, crf=args.crf)

    print(f"\nс баннером: {len(done)} из {len(files)}  (в {out_dir}/)")
    return 0 if done else 1


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

    p = sub.add_parser("anim", help="оригинальная анимация: бриф -> сцены -> сборка")
    p.add_argument("step", choices=["prompt", "shots", "assemble"])
    p.add_argument("--scenes", type=int, default=8, help="сцен в раскадровке")
    p.add_argument("--footage", default="clips/anim", help="где лежат сгенерированные сцены")
    p.add_argument("--out", default="out/assemble.jsx", help="куда писать скрипт AE")
    p.set_defaults(func=cmd_anim)

    p = sub.add_parser("finish", help="финишный проход: убрать следы генерации")
    p.add_argument("src", help="файл или папка со сценами")
    p.add_argument("--preset", default="medium", choices=list(finish_mod.PRESETS))
    p.add_argument("--glob", default="*.mp4", help="фильтр, если src это папка")
    p.add_argument("--out", default="out/finished", help="куда складывать")
    p.add_argument("--crf", type=int, default=18)
    p.set_defaults(func=cmd_finish)

    p = sub.add_parser("brand", help="наложить баннер с учётом безопасных зон")
    p.add_argument("src", help="файл или папка")
    p.add_argument("--image", required=True, help="PNG баннера, желательно с альфой")
    p.add_argument("--glob", default="*.mp4")
    p.add_argument("--out", default="out/branded")
    p.add_argument("--position", choices=list(brand_mod.POSITIONS),
                   help="фиксировать позицию; без него разводится по роликам")
    p.add_argument("--width", type=float, default=0.34, help="ширина от кадра")
    p.add_argument("--opacity", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--crf", type=int, default=20)
    p.set_defaults(func=cmd_brand)

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
