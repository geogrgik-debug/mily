"""What a capture host reports about itself, so someone else can check on it.

A capture runs unattended on a machine nobody is looking at, and the failure
that matters is silent: the process dies, or the socket drops and never comes
back, and nothing says so. A game market is gone a minute after it is quoted,
so a night of silence is a night lost for good.

This module answers one question from the files on disk -- is the capture still
writing? -- cheaply enough to run every few minutes. It does not decompress
anything: size and modification time of the newest log are the signal, because
a live capture touches its file every few seconds and a dead one never does.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from tennis.ingest.clock import Clock
from tennis.ingest.rawlog import find_logs

__all__ = ["CaptureStatus", "capture_status"]

# A live capture writes far more often than this; the threshold is loose on
# purpose so a quiet minute between snapshots never reads as a failure.
STALE_AFTER_S = 300.0


@dataclass(frozen=True)
class CaptureStatus:
    ok: bool
    reason: str
    checked_at_s: float
    newest_file: str | None
    newest_bytes: int
    age_s: float | None
    files: int
    total_bytes: int
    disk_free_bytes: int
    disk_free_days: float | None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, sort_keys=True)


def capture_status(root: str | Path, *, clock: Clock | None = None,
                   stale_after_s: float = STALE_AFTER_S,
                   bytes_per_day: float = 900_000_000.0) -> CaptureStatus:
    """Judge a capture directory. Never raises -- a checker must not need one.

    `bytes_per_day` is the measured upper end of ten matches around the clock
    (0.3-0.9 GB); it turns free disk into the number actually worth knowing,
    which is how many more days the host can record before it stops being able
    to.
    """
    clock = clock or Clock()
    now = clock.now().wall_ns / 1e9
    root = Path(root)

    try:
        free = shutil.disk_usage(root if root.exists() else Path(".")).free
    except OSError:
        free = 0
    free_days = (free / bytes_per_day) if bytes_per_day > 0 else None

    def result(ok, reason, newest=None, newest_bytes=0, age=None,
               files=0, total=0):
        return CaptureStatus(
            ok=ok, reason=reason, checked_at_s=now,
            newest_file=newest, newest_bytes=newest_bytes, age_s=age,
            files=files, total_bytes=total,
            disk_free_bytes=free,
            disk_free_days=round(free_days, 1) if free_days is not None else None,
        )

    if not root.exists():
        return result(False, f"нет каталога {root}")
    logs = find_logs(root)
    if not logs:
        return result(False, f"в {root} нет ни одного лога — запись не начиналась")

    stats = []
    for p in logs:
        try:
            stats.append((p, p.stat()))
        except OSError:
            continue
    if not stats:
        return result(False, "логи есть, но ни один не читается")

    newest, st = max(stats, key=lambda ps: ps[1].st_mtime)
    age = now - st.st_mtime
    total = sum(s.st_size for _, s in stats)
    common = dict(newest=str(newest), newest_bytes=st.st_size, age=round(age, 1),
                  files=len(stats), total=total)

    if age > stale_after_s:
        return result(False, f"последняя запись {age / 60:.0f} мин назад — "
                             f"похоже, запись встала", **common)
    if st.st_size == 0:
        return result(False, "файл создан, но пуст", **common)
    if free_days is not None and free_days < 2:
        return result(False, f"на диске осталось на {free_days:.1f} дня записи",
                      **common)
    return result(True, f"пишет, последний кадр {age:.0f} с назад", **common)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default="data/raw")
    ap.add_argument("--out", help="also write the report here, atomically")
    args = ap.parse_args(argv)

    status = capture_status(args.root)
    text = status.to_json()
    print(text)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: a reader must never catch a half-written report.
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(text + "\n", encoding="utf-8")
        tmp.replace(out)
    return 0 if status.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
