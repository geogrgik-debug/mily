"""Per-stake pushes against full snapshots: which arrives first, and whether a
late snapshot undoes a push.

BetBoom sends a game market's prices two ways: the whole board of a match on
every score change (`newsletters_full_match`), and one outcome at a time on
every change to it (`newsletters_stake`, asked for since 3f695d8). Only the
capture host's logs have both; the laptop's recorder is older. Two questions:

(a) **Rollbacks.** `betboom_quotes` differences a snapshot against the
    previous snapshot, not against the pushes. A snapshot sent after a push
    but still carrying the price from before it would put that price back,
    and the next change would move it on: moves the book never made. So every
    snapshot quote of an outcome already pushed is held against its latest
    push.
(b) **Lead.** How much sooner a change of an outcome's odds arrives by push
    than by snapshot. Changes are paired by value, in order, within a window
    -- the lead-lag meter's alignment. The first measurement, 16 outcomes over
    7 minutes, gave a median of 0.5 s, p90 78 s, max 153 s.

Measured on the capture host's logs of 22-23.09, 19.7 h, 12,385 pushed
outcomes. Of 76,063 snapshot quotes of a pushed outcome, 63,811 carried its
latest pushed price, none a price the pushes had left, and the other 12,252
took the outcome off the board before a push said so: nothing rolls back.
The push was first in every one of 57,638 paired changes, by a median of
0.52 s, p90 0.61 s, max 10.63 s -- not the 78 s p90 of the first look.

    python -m tennis.market.push_vs_snapshot PATH [--window 300]
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from tennis.ingest.rawlog import find_logs
from tennis.market.lead_lag import _align, _clusters, quantile
from tennis.market.streams import GAP_S, Quote, RunClock, _rows, betboom_quotes

__all__ = ["WINDOW_S", "Agreement", "Change", "Lead", "agreement", "changes", "lead",
           "load_quotes", "main"]

# How far apart a push and a snapshot of one change may be: about twice the
# longest lead measured before, 153 s.
WINDOW_S = 300.0


# --------------------------------------------------------------- (a) rollbacks


@dataclass(frozen=True)
class Agreement:
    """Snapshot quotes of outcomes already pushed, against the latest push.

    `older` is what would roll the meter back: a price the pushes had
    already left. `gone` is the snapshot taking the outcome off the board
    before any push said so, and `other` a price no push had shown yet.
    """

    same: int
    older: int
    gone: int
    other: int

    @property
    def total(self) -> int:
        return self.same + self.older + self.gone + self.other


def agreement(quotes: Iterable[Quote | None]) -> Agreement:
    """Hold each snapshot quote against the pushes of its outcome. A price is
    odds and whether they can be bet; a break forgets the pushes."""
    pushed: dict[tuple, tuple] = {}        # outcome -> (latest price, the ones before)
    counts: Counter = Counter()
    for q in quotes:
        if q is None:
            pushed.clear()
            continue
        key = (q.match, q.market, q.outcome)
        price = (q.odds, q.active)
        if q.source == "stake":
            now = pushed.get(key)
            if now is None:
                pushed[key] = (price, frozenset())
            elif price != now[0]:
                pushed[key] = (price, now[1] | {now[0]})
        elif q.source == "full" and key in pushed:
            now, before = pushed[key]
            counts["same" if price == now else "older" if price in before
                   else "gone" if q.odds is None else "other"] += 1
    return Agreement(counts["same"], counts["older"], counts["gone"], counts["other"])


# --------------------------------------------------------------- (b) lead


@dataclass(frozen=True)
class Change:
    """An outcome's odds as one source reported them, and the odds they
    replaced. `segment` numbers the unbroken stretch of capture it came in."""

    ts_received_ns: int
    ts_mono_ns: int
    odds: float
    prev: float
    segment: int = 0


def _bettable(q: Quote) -> bool:
    return q.active and q.odds is not None and math.isfinite(q.odds) and q.odds > 1.0


def changes(quotes: Iterable[Quote | None], source: str) -> dict[tuple, list[Change]]:
    """Per outcome that `source` quoted, the changes of its odds it reported.

    A change is measured from the source's own last price, or, before it has
    one, from the last price anyone quoted: the recorder asks for an outcome's
    pushes off the snapshot, so the first push is already a change from what
    the snapshot said. Only bettable odds are prices -- a suspended outcome
    carries a placeholder -- and a break forgets every price.
    """
    out: dict[tuple, list[Change]] = {}
    own: dict[tuple, float] = {}
    anyone: dict[tuple, float] = {}
    segment = 0
    for q in quotes:
        if q is None:
            own.clear()
            anyone.clear()
            segment += 1
            continue
        if not _bettable(q):
            continue
        key = (q.match, q.market, q.outcome)
        if q.source == source:
            prev = own.get(key, anyone.get(key))
            own[key] = q.odds
            out.setdefault(key, [])
            if prev is not None and prev != q.odds:
                out[key].append(Change(q.ts_received_ns, q.ts_mono_ns, q.odds, prev, segment))
        anyone[key] = q.odds
    return out


@dataclass(frozen=True)
class Lead:
    """The changes of the pushed outcomes, by push and by snapshot, paired."""

    outcomes: int                    # outcomes that were pushed
    pushed: int                      # changes by push on them
    snapped: int                     # changes by snapshot on them
    leads: tuple[float, ...]         # snapshot minus push, s, per paired change

    @property
    def paired(self) -> int:
        return len(self.leads)

    @property
    def order(self) -> tuple[int, int, int]:
        """Paired changes the push brought first, at the same instant, later.
        No band of "level" here, unlike the lead-lag meter: both kinds of
        frame come down one socket to one process, so their order is exact."""
        first = sum(1 for x in self.leads if x > 0)
        later = sum(1 for x in self.leads if x < 0)
        return first, self.paired - first - later, later


def _same_odds(a: Change, b: Change) -> bool:
    return a.odds == b.odds


def lead(push: dict[tuple, list[Change]], snap: dict[tuple, list[Change]],
         window_s: float = WINDOW_S) -> Lead:
    """Pair each pushed outcome's changes by push and by snapshot.

    Two changes pair when they reach the same odds within `window_s` in one
    unbroken stretch of capture -- across a break the gap would time the
    break; among all pairings, the one with the most pairs and then the
    least total gap, in time order on both sides.
    """
    window_ns = int(window_s * 1e9)
    leads: list[float] = []
    for key, by_push in push.items():
        by_snap = snap.get(key, [])
        for segment in sorted({c.segment for c in by_push}):
            pa = [c for c in by_push if c.segment == segment]
            sb = [c for c in by_snap if c.segment == segment]
            for ca, cb in _clusters(pa, sb, window_ns):
                leads.extend((s.ts_mono_ns - p.ts_mono_ns) / 1e9
                             for p, s in _align(ca, cb, window_ns, _same_odds))
    return Lead(len(push), sum(len(v) for v in push.values()),
                sum(len(snap.get(key, ())) for key in push), tuple(leads))


# --------------------------------------------------------------- input and report


def load_quotes(path: str | Path, pb) -> tuple[list[Quote | None], list[RunClock]]:
    """Every BetBoom capture under `path`, decoded run by run, with a break
    after each run."""
    files = find_logs(path)
    if not files:
        raise FileNotFoundError(f"no logs under {path}")
    by_run: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        by_run[f.name.split(".")[0]].append(f)
    quotes: list[Quote | None] = []
    clocks: list[RunClock] = []
    for run_id, run_files in sorted(by_run.items()):
        clock = RunClock(run_id, [], [])
        quotes.extend(betboom_quotes(_rows(run_files, clock, "betboom", GAP_S), pb))
        quotes.append(None)
        if clock.walls:
            clocks.append(clock)
    clocks.sort(key=lambda c: c.start)
    for before, after in zip(clocks, clocks[1:]):
        if after.start < before.end:
            raise ValueError(f"{path}: {before.run_id} and {after.run_id} were recording "
                             "at the same time, and every change would count twice; "
                             "pass each capture on its own")
    return quotes, clocks


def print_report(path, clocks: Sequence[RunClock], ag: Agreement, ld: Lead,
                 window_s: float) -> None:
    hours = sum(c.end - c.start for c in clocks) / 3.6e12
    print(f"{path}: {len(clocks)} run(s), {hours:.1f} h, {ld.outcomes} outcomes pushed\n")
    print("(a) snapshot quotes of an outcome already pushed, against its latest push")
    print(f"    {ag.total}: at the latest pushed price {ag.same}, at a price the pushes had "
          f"left {ag.older} (each a rollback), off the board before a push said so "
          f"{ag.gone}, at a price no push had shown {ag.other}\n")
    print("(b) lead of the push over the snapshot, per change of an outcome's odds")
    print(f"    {ld.pushed} changes by push, {ld.snapped} by snapshot; {ld.paired} paired "
          f"(same odds, in order, within {window_s:g} s)")
    if ld.leads:
        first, level, later = ld.order
        print(f"    push first {first} ({100 * first / ld.paired:.1f}%), "
              f"same instant {level}, snapshot first {later}")
        print(f"    snapshot minus push: median {statistics.median(ld.leads):+.2f} s, "
              f"p90 {quantile(ld.leads, 0.9):+.2f}, max {max(ld.leads):+.2f}, "
              f"min {min(ld.leads):+.2f}")
    print(f"    seen by push only {ld.pushed - ld.paired}, by snapshot only "
          f"{ld.snapped - ld.paired}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="a BetBoom raw log or a directory of them")
    ap.add_argument("--window", type=float, default=WINDOW_S,
                    help="seconds within which a push and a snapshot can be one change")
    args = ap.parse_args(argv)

    from tennis.ingest.betboom.client import load_pb
    try:
        quotes, clocks = load_quotes(args.path, load_pb())
    except (FileNotFoundError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    ld = lead(changes(quotes, "stake"), changes(quotes, "full"), args.window)
    if not ld.outcomes:
        print(f"refused: no pushes under {args.path} -- a recorder older than 3f695d8 "
              "does not ask for them", file=sys.stderr)
        return 2
    print_report(args.path, clocks, agreement(quotes), ld, args.window)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
