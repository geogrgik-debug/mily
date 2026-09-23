"""What a capture host reports about itself, so someone else can check on it.

A capture runs unattended on a machine nobody is looking at, and the failure
that matters is silent: the process dies, or the socket drops and never comes
back, and nothing says so. A game market is gone a minute after it is quoted,
so a night of silence is a night lost for good.

This module answers one question from the files on disk -- is the capture
actually collecting odds? -- cheaply enough to run every few minutes. It does
not decompress anything.

Size and modification time of the newest log are necessary but **not
sufficient**, and the first version of this module got that wrong. The feed
pushes a tour-wide score stream to anyone connected, subscribed to a match or
not, so a recorder that lost its subscriptions keeps writing and keeps looking
healthy. That happened on the first long run: one dropped socket, a successful
reconnect, and then hours of a growing file containing no prices at all -- and
this module called it fine.

So the recorder publishes its own counters to `_recorder.json` beside the log,
and they decide. Bytes on disk only answer "is a process writing"; the sidecar
answers "is it writing what we came for".
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

# A subscribed capture sees prices constantly; ten minutes without one means
# the subscriptions are gone even though the log keeps growing.
NO_STAKES_AFTER_S = 600.0

SIDECAR_GLOB = "_recorder*.json"


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
    subscribed: int | None = None
    stakes_seen: int | None = None
    last_stake_age_s: float | None = None
    reconnects: int | None = None
    # Why the socket last closed, as the recorder saw it. On 23.09 the report
    # said "not subscribed, 304 reconnects" and the cause -- the feed refusing
    # every session with 3010 "Access rejected" -- took a second machine to see.
    last_disconnect: str | None = None
    # The last ten drops (at_s, why, lived_s), and what all of them cost in
    # seconds without prices since the run started: the count alone does not
    # say whether eight reconnects lost eight seconds or eight minutes.
    disconnects: list | None = None
    blind_s: float | None = None
    last_blind_s: float | None = None

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

    side = _read_sidecar(root, now, stale_after_s)

    def result(ok, reason, newest=None, newest_bytes=0, age=None,
               files=0, total=0):
        last_stake = side.get("last_stake_at_s") if side else None
        return CaptureStatus(
            ok=ok, reason=reason, checked_at_s=now,
            newest_file=newest, newest_bytes=newest_bytes, age_s=age,
            files=files, total_bytes=total,
            disk_free_bytes=free,
            disk_free_days=round(free_days, 1) if free_days is not None else None,
            subscribed=side.get("subscribed") if side else None,
            stakes_seen=side.get("stakes_seen") if side else None,
            last_stake_age_s=(round(now - last_stake, 1)
                              if isinstance(last_stake, (int, float)) else None),
            reconnects=side.get("reconnects") if side else None,
            last_disconnect=side.get("last_disconnect") if side else None,
            disconnects=side.get("disconnects") if side else None,
            blind_s=side.get("blind_s") if side else None,
            last_blind_s=side.get("last_blind_s") if side else None,
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

    # Everything above only proves a process is writing. The feed pushes a
    # tour-wide score stream to anyone connected, so a recorder that lost its
    # subscriptions passes every check so far while collecting no prices.
    if side:
        subscribed = side.get("subscribed")
        if isinstance(subscribed, int) and subscribed <= 0:
            return result(False, "подключён, но НЕ ПОДПИСАН ни на один матч — "
                                 "пишет только счёт, котировок нет", **common)
        last_stake = side.get("last_stake_at_s")
        if isinstance(last_stake, (int, float)):
            quiet = now - last_stake
            if quiet > NO_STAKES_AFTER_S:
                return result(False, f"файл растёт, но котировок нет "
                                     f"{quiet / 60:.0f} мин — подписки потеряны",
                              **common)
        elif side.get("frames", 0) > 200:
            return result(False, "кадры идут, но ни одной котировки не было",
                          **common)
        return result(True, f"пишет, {subscribed} подписок, "
                            f"{side.get('stakes_seen', 0)} котировок", **common)

    return result(True, f"пишет, последний кадр {age:.0f} с назад "
                        f"(счётчиков рекордера нет — только по файлу)", **common)


def _read_sidecar(root: Path, now: float, max_age_s: float) -> dict:
    """The freshest recorder sidecar, or {} if none is recent enough to trust.

    There can be several: one per run, and a crashed run leaves its file
    behind. The one written most recently describes the process that is
    actually alive. A sidecar older than `max_age_s` is ignored outright --
    the live recorder rewrites its file every heartbeat, so an old one belongs
    to a process that is gone, and judging the capture by it would report a
    dead run while a live one keeps writing next to it. That exact false
    alarm was observed: a two-minute probe's file outlived the probe.
    """
    best, best_at = {}, -1.0
    for path in root.glob(SIDECAR_GLOB):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        at = data.get("written_at_s")
        if not isinstance(at, (int, float)):
            continue
        if at > best_at:
            best, best_at = data, at
    if best and now - best_at > max_age_s:
        return {}
    return best


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
