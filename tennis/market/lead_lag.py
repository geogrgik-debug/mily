"""Who moves a price first: order, lag and its stability between two feeds.

Plan item 3.8, the latency meter, in its bookmaker-against-bookmaker form. When
two books price one outcome, three numbers say whether they move apart in time:

1. **Order** -- which one moved the price first, which moved with the other,
   which followed; for every move and in aggregate.
2. **Lag** -- how many seconds the follower is behind: median and p90.
3. **Stability** -- whether the leader is the same book move after move and
   match after match, or whoever was quicker that time. Only a stable lead is
   worth knowing about.

    python -m tennis.market.lead_lag betboom=PATH 1win=PATH [...]

Research and paper only: nothing here places, sizes or times a bet.

Each path becomes a stream of price events -- "the margin-free probability of
this outcome became X" -- in `streams`, which also refuses logs that were not
written on one machine. Here a **move** is an event with a previous value, and
moves of one outcome in two streams are paired in time order and in the same
direction, within a window. Outcomes of one book that moved together count as
one move: a two-way book's second outcome is its first one mirrored.

What the zero test measured
---------------------------
Two recorders on the owner's laptop, 22.09, 36 minutes of overlap, 2553
byte-identical frames of the tour stream: B - A median -3 ms, |B - A| p90
86 ms, p99 0.92 s, max 1.38 s -- and B was first in 64% of them. One feed on
one machine, so that spread is the recorders' own receive delay, and it is not
even symmetric between two processes. A lead inside it is not evidence of
anything, which is what `TIE_S` encodes. A comparison of two bookmakers runs
two recorder processes as well, so the same floor applies to it. This module,
run on the same two logs, reports the same: 3247 moves paired, B - A median
-3 ms, |lag| p90 86 ms, 99.2% level, no leader.

What it cannot see
------------------
A lag longer than the window. It does not show up as unpaired moves alone: the
pairing reaches for the neighbouring move and reports a small lag instead.
Measured by delaying a copy of a real capture by 12 s, under a 10 s window:
31% of moves paired, none at 12 s, median +0.91 s. The report therefore reruns
every pair under a doubled window and says so when the answer moves.
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Callable, Iterable, Sequence

from tennis.market.streams import (
    SOURCES,
    ClockMismatch,
    PriceEvent,
    Stream,
    check_same_clock,
    load_stream,
)

__all__ = [
    "TIE_S", "WINDOW_S", "MatchedMove", "PairReport", "Stability",
    "pair_moves", "compare", "stability", "paired_share", "robust_to_window",
    "quantile", "wilson", "print_report", "write_events", "main",
]

# Moves this close are level. Two captures of one feed on one machine differed
# by up to 1.38 s (p99 0.92 s), so anything inside a second is receive jitter.
TIE_S = 1.0
# How far apart two moves of one outcome may be and still be the same move:
# about half the usual gap between two moves of one book, which on 22.09 was
# a median of 19.2 s on the tour stream and 14.9 s on full snapshots.
WINDOW_S = 10.0
# A match says who led it only with this many decided moves, and stability is
# judged only over at least this many such matches.
MIN_DECIDED_PER_MATCH = 5
MIN_JUDGED_MATCHES = 3


@dataclass(frozen=True)
class MatchedMove:
    """One move of one book, seen in two streams.

    `lag_s` is how much later `b` saw it than `a`, on the monotonic clock:
    positive means `a` was first.
    """

    match: object
    market: tuple
    outcomes: tuple[str, ...]
    a: PriceEvent
    b: PriceEvent
    lag_s: float

    def first(self, tie_s: float = TIE_S) -> str:
        """'a', 'b', or '=' when the two are level within `tie_s`."""
        if self.lag_s > tie_s:
            return "a"
        if self.lag_s < -tie_s:
            return "b"
        return "="


# --------------------------------------------------------------- pairing moves


def _clusters(a: list[PriceEvent], b: list[PriceEvent], window_ns: int):
    """Split two time-sorted move lists where nothing is within reach.

    No pair can span a gap longer than the window, and none of the ordering
    constraints do either, so each cluster is aligned on its own. It keeps the
    alignment small: a cluster is usually one repricing.
    """
    tagged = sorted([(e.ts_received_ns, 0, e) for e in a]
                    + [(e.ts_received_ns, 1, e) for e in b],
                    key=lambda t: (t[0], t[1]))
    ca: list[PriceEvent] = []
    cb: list[PriceEvent] = []
    last = None
    for ts, side, event in tagged:
        if last is not None and ts - last > window_ns:
            if ca and cb:
                yield ca, cb
            ca, cb = [], []
        (ca if side == 0 else cb).append(event)
        last = ts
    if ca and cb:
        yield ca, cb


def _same_direction(ea: PriceEvent, eb: PriceEvent) -> bool:
    return ea.direction == eb.direction


def _align(a: list, b: list, window_ns: int,
           same: Callable[[object, object], bool] = _same_direction):
    """Most pairs, then least total gap, without two pairs crossing in time.

    Order matters here. Pairing each move with its nearest neighbour instead
    would, once one book lags by more than half the time between two moves,
    pair a move with the other book's *next* one -- reporting a small lead in
    the wrong direction, and so inventing simultaneity where there is a lag.
    Two items can pair only if `same` says so.
    """
    n, m = len(a), len(b)
    score = [[(0, 0)] * (m + 1) for _ in range(n + 1)]
    step = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        ea = a[i - 1]
        for j in range(1, m + 1):
            best, how = score[i - 1][j], 1
            if score[i][j - 1] > best:
                best, how = score[i][j - 1], 2
            eb = b[j - 1]
            gap = abs(eb.ts_received_ns - ea.ts_received_ns)
            if gap <= window_ns and same(ea, eb):
                done, cost = score[i - 1][j - 1]
                if (done + 1, cost - gap) > best:
                    best, how = (done + 1, cost - gap), 3
            score[i][j], step[i][j] = best, how
    pairs = []
    i, j = n, m
    while i and j:
        how = step[i][j]
        if how == 3:
            pairs.append((a[i - 1], b[j - 1]))
            i, j = i - 1, j - 1
        elif how == 1:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def pair_moves(a: Iterable[PriceEvent], b: Iterable[PriceEvent],
               window_s: float = WINDOW_S) -> list[tuple[PriceEvent, PriceEvent]]:
    """Pair the moves of one outcome seen in two streams.

    Two moves pair when they go the same way and are at most `window_s` apart;
    among all ways of pairing, the one with the most pairs and then the least
    total gap is taken, keeping both streams in time order.
    """
    ma = sorted((e for e in a if e.is_move), key=lambda e: e.ts_received_ns)
    mb = sorted((e for e in b if e.is_move), key=lambda e: e.ts_received_ns)
    window_ns = int(window_s * 1e9)
    pairs = []
    for ca, cb in _clusters(ma, mb, window_ns):
        pairs.extend(_align(ca, cb, window_ns))
    return pairs


# --------------------------------------------------------------- statistics


def quantile(values: Iterable[float], q: float) -> float:
    """Linear interpolation between closest ranks, numpy's default method."""
    vals = sorted(values)
    if not vals:
        raise ValueError("quantile of no values")
    pos = q * (len(vals) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials, 95% by default."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True)
class Stability:
    """Is the book that moves first the same one every time?

    `leader` is 'a' or 'b' when the share of decided moves it won is clear of
    one half at 95%; `stable` additionally needs it to lead in at least two
    thirds of the matches that had `MIN_DECIDED_PER_MATCH` decided moves, over
    at least `MIN_JUDGED_MATCHES` of them. A lead won on the total but lost
    match by match is the lead of whichever matches happened to be busiest.
    """

    a_first: int
    b_first: int
    ci: tuple[float, float]
    by_match: dict[str, int]
    leader: str | None
    stable: bool
    verdict: str

    @property
    def share_a(self) -> float | None:
        decided = self.a_first + self.b_first
        return self.a_first / decided if decided else None


def stability(matched: Sequence[MatchedMove], tie_s: float = TIE_S) -> Stability:
    per_match: dict[object, Counter] = defaultdict(Counter)
    for move in matched:
        side = move.first(tie_s)
        if side != "=":
            per_match[move.match][side] += 1
    a_first = sum(c["a"] for c in per_match.values())
    b_first = sum(c["b"] for c in per_match.values())
    leads = Counter()
    for c in per_match.values():
        if c["a"] + c["b"] >= MIN_DECIDED_PER_MATCH:
            leads["a" if c["a"] > c["b"] else "b" if c["b"] > c["a"] else "="] += 1
    by_match = {side: leads[side] for side in ("a", "b", "=")}
    ci = wilson(a_first, a_first + b_first)
    judged = sum(by_match.values())

    if a_first + b_first == 0:
        return Stability(0, 0, ci, by_match, None, False,
                         "no leader: every paired move was level")
    if ci[0] <= 0.5 <= ci[1]:
        return Stability(a_first, b_first, ci, by_match, None, False,
                         "no leader: who is first is a coin toss")
    leader = "a" if ci[0] > 0.5 else "b"
    if judged < MIN_JUDGED_MATCHES:
        verdict = (f"{{{leader}}} is first overall, but only {judged} match(es) had "
                   f"{MIN_DECIDED_PER_MATCH}+ decided moves: too few to say it holds")
        return Stability(a_first, b_first, ci, by_match, leader, False, verdict)
    won = by_match[leader]
    stable = won * 3 >= judged * 2
    verdict = (f"{'stable' if stable else 'unstable'}: {{{leader}}} is first overall "
               f"and leads {won} of {judged} matches")
    return Stability(a_first, b_first, ci, by_match, leader, stable, verdict)


@dataclass(frozen=True)
class PairReport:
    """Everything the three numbers are computed from, for one pair of streams."""

    a: str
    b: str
    outcomes: int                    # outcomes both streams quoted
    moves_a: int                     # book moves of `a` on those outcomes
    moves_b: int
    matched: tuple[MatchedMove, ...]
    window_s: float
    tie_s: float

    def order(self) -> dict[str, int]:
        counts = Counter(m.first(self.tie_s) for m in self.matched)
        return {side: counts[side] for side in ("a", "=", "b")}

    def lags(self) -> list[float]:
        return [m.lag_s for m in self.matched]

    def stability(self) -> Stability:
        return stability(self.matched, self.tie_s)


def _by_key(events: Iterable[PriceEvent]) -> dict[tuple, list[PriceEvent]]:
    out: dict[tuple, list[PriceEvent]] = defaultdict(list)
    for e in events:
        out[e.key].append(e)
    return out


def _book_move(e: PriceEvent) -> tuple:
    return (e.match, e.market, e.ts_received_ns, e.ts_mono_ns)


def _collapse(pairs) -> list[MatchedMove]:
    """Outcome pairs of one book moved at the same instants are one move."""
    groups: dict[tuple, list] = {}
    for ea, eb in pairs:
        groups.setdefault(_book_move(ea) + _book_move(eb)[2:], []).append((ea, eb))
    out = []
    for group in groups.values():
        group.sort(key=lambda p: p[0].outcome)
        ea, eb = group[0]
        out.append(MatchedMove(ea.match, ea.market, tuple(p[0].outcome for p in group),
                               ea, eb, (eb.ts_mono_ns - ea.ts_mono_ns) / 1e9))
    out.sort(key=lambda m: m.a.ts_received_ns)
    return out


def compare(a: Stream, b: Stream, *, window_s: float = WINDOW_S,
            tie_s: float = TIE_S) -> PairReport:
    """The three numbers' raw material for streams `a` and `b`."""
    ka, kb = _by_key(a.events), _by_key(b.events)
    common = sorted(ka.keys() & kb.keys(), key=repr)
    pairs = [p for key in common for p in pair_moves(ka[key], kb[key], window_s)]
    moves_a = {_book_move(e) for key in common for e in ka[key] if e.is_move}
    moves_b = {_book_move(e) for key in common for e in kb[key] if e.is_move}
    return PairReport(a.label, b.label, len(common), len(moves_a), len(moves_b),
                      tuple(_collapse(pairs)), window_s, tie_s)


def paired_share(rep: PairReport) -> float:
    """Paired moves as a share of the side that moved less."""
    fewer = min(rep.moves_a, rep.moves_b)
    return len(rep.matched) / fewer if fewer else 0.0


def robust_to_window(rep: PairReport, wide: PairReport) -> bool:
    """Whether doubling the window leaves the answer where it was.

    Under a 10 s window a real capture delayed by 12 s paired 31% of 11,365
    moves, none of them at 12 s; under 20 s it paired every move at exactly
    12 s. So the answer is trusted only if a wider window pairs no more than a
    tenth more of the moves and moves the median lag by less than the tie band.
    """
    if not rep.matched or not wide.matched:
        return len(rep.matched) == len(wide.matched)
    jump = paired_share(wide) - paired_share(rep)
    shift = abs(statistics.median(wide.lags()) - statistics.median(rep.lags()))
    return jump <= 0.1 and shift <= rep.tie_s


# --------------------------------------------------------------- report


def _utc(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1e9, timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _market(market: tuple) -> str:
    scope, set_no, game_no, kind = market
    if scope == "game":
        return f"s{set_no}g{game_no} {kind}"
    if scope == "set":
        return f"s{set_no} {kind}"
    return kind


def _print_pair(rep: PairReport, wide: PairReport, offset: float | None, sample: int) -> None:
    names = {"a": rep.a, "b": rep.b}
    print(f"=== {rep.a} vs {rep.b} ===")
    if offset is None:
        print("  never recorded at the same time: nothing to compare\n")
        return
    print(f"  same clock: offsets agree to {abs(offset) * 1e3:.1f} ms")
    print(f"  {rep.moves_a} moves of {rep.a} and {rep.moves_b} of {rep.b} on "
          f"{rep.outcomes} outcomes both quoted; {len(rep.matched)} paired, "
          f"{100 * paired_share(rep):.0f}% of the fewer "
          f"(window {rep.window_s:g} s, level within {rep.tie_s:g} s)")
    if not rep.matched:
        print()
        return
    if not robust_to_window(rep, wide):
        median_wide = statistics.median(wide.lags()) if wide.matched else float("nan")
        print(f"  WARNING: a {wide.window_s:g} s window pairs {100 * paired_share(wide):.0f}% "
              f"with median lag {median_wide:+.3f} s. The lag may be longer than the "
              "window: rerun with a larger --window")
    elif paired_share(rep) < 0.5:
        print("  note: most moves found no partner -- the two feeds move on different "
              "occasions, so read the numbers below as a minority's")

    n = len(rep.matched)
    order = rep.order()
    print(f"  1 order      {rep.a} first {order['a']} ({100 * order['a'] / n:.1f}%) | "
          f"level {order['=']} ({100 * order['='] / n:.1f}%) | "
          f"{rep.b} first {order['b']} ({100 * order['b'] / n:.1f}%)")
    lags = rep.lags()
    absl = [abs(x) for x in lags]
    print(f"  2 lag        {rep.b} after {rep.a}: median {statistics.median(lags):+.3f} s, "
          f"p10 {quantile(lags, 0.1):+.3f}, p90 {quantile(lags, 0.9):+.3f}; "
          f"|lag| median {statistics.median(absl):.3f} s, p90 {quantile(absl, 0.9):.3f}")
    st = rep.stability()
    share = "-" if st.share_a is None else f"{100 * st.share_a:.0f}%"
    by = ", ".join(f"{names.get(k, 'level')} {v}" for k, v in st.by_match.items())
    print(f"  3 stability  {rep.a} first in {share} of {st.a_first + st.b_first} decided "
          f"(95% CI {100 * st.ci[0]:.0f}-{100 * st.ci[1]:.0f}%); leads by match: {by}")
    print(f"               -> {st.verdict.format(**names)}")
    print(f"  first {min(sample, n)} paired moves:")
    for mv in rep.matched[:sample]:
        print(f"    {_utc(mv.a.ts_received_ns)}  {mv.match}  {_market(mv.market)} "
              f"{'/'.join(mv.outcomes)}  {mv.a.prev:.3f}->{mv.a.prob:.3f} | "
              f"{mv.b.prev:.3f}->{mv.b.prob:.3f}  lag {mv.lag_s:+.3f} s  "
              f"first: {names.get(mv.first(rep.tie_s), 'level')}")
    print()


def print_report(streams: Sequence[Stream], offsets: dict,
                 reports: Sequence[tuple[PairReport, PairReport]],
                 *, method: str, sample: int = 10) -> None:
    """The three numbers for every pair; `reports` holds each pair's report
    and the same pair under a doubled window."""
    for s in streams:
        moves = len({_book_move(e) for e in s.events if e.is_move})
        matches = len({e.match for e in s.events})
        span = (sum(r.end - r.start for r in s.runs) / 60e9) if s.runs else 0.0
        print(f"{s.label} = {s.provider}, {s.path}: {len(s.runs)} run(s), {span:.0f} min, "
              f"{len(s.events)} price events, {moves} moves, {matches} matches")
    print(f"margin removed by {method}\n")
    for rep, wide in reports:
        _print_pair(rep, wide, offsets.get((rep.a, rep.b)), sample)


def write_events(path: str | Path, reports: Sequence[PairReport]) -> int:
    """Every paired move as a CSV row: the per-move half of the order question."""
    rows = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["a", "b", "match", "market", "outcomes", "a_utc", "b_utc", "lag_s",
                    "first", "a_prev", "a_prob", "b_prev", "b_prob"])
        for rep in reports:
            names = {"a": rep.a, "b": rep.b, "=": "level"}
            for mv in rep.matched:
                w.writerow([rep.a, rep.b, mv.match, _market(mv.market), "/".join(mv.outcomes),
                            _utc(mv.a.ts_received_ns), _utc(mv.b.ts_received_ns),
                            f"{mv.lag_s:.6f}", names[mv.first(rep.tie_s)],
                            f"{mv.a.prev:.6f}", f"{mv.a.prob:.6f}",
                            f"{mv.b.prev:.6f}", f"{mv.b.prob:.6f}"])
                rows += 1
    return rows


def _stream_arg(arg: str, index: int) -> tuple[str, Path]:
    """`NAME=PATH` or a bare path, which gets a letter. A hive-style path such
    as data/raw/provider=betboom holds '=' too, so an existing path wins."""
    if Path(arg).exists():
        return chr(ord("A") + index), Path(arg)
    name, sep, rest = arg.partition("=")
    if sep and name and not any(c in name for c in "/\\") and Path(rest).exists():
        return name, Path(rest)
    raise SystemExit(f"no such log or directory: {arg}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("streams", nargs="+", metavar="[NAME=]PATH",
                    help="a raw log or a directory of them; each argument is one stream")
    ap.add_argument("--sources", default=",".join(SOURCES),
                    help="BetBoom frame kinds to read, of: full, stake, tour")
    ap.add_argument("--window", type=float, default=WINDOW_S,
                    help="seconds within which two moves can be the same move")
    ap.add_argument("--tie", type=float, default=TIE_S,
                    help="seconds within which two moves are level")
    ap.add_argument("--min-move", type=float, default=0.0,
                    help="smallest probability change that counts as a move")
    ap.add_argument("--method", choices=["shin", "proportional"], default="shin",
                    help="how to remove the bookmaker's margin")
    ap.add_argument("--events", metavar="CSV", help="write every paired move here")
    args = ap.parse_args(argv)
    if len(args.streams) < 2:
        ap.error("need at least two streams")
    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
    unknown = set(sources) - set(SOURCES)
    if unknown:
        ap.error(f"unknown sources: {sorted(unknown)}")

    try:
        streams = [load_stream(label, path, sources=sources, method=args.method,
                               min_move=args.min_move)
                   for label, path in (_stream_arg(arg, i) for i, arg in enumerate(args.streams))]
        offsets = check_same_clock(streams)
    except (ClockMismatch, NotImplementedError, FileNotFoundError, ValueError) as exc:
        # Refusals about the input -- two machines, two captures in one stream,
        # a provider with no decoder yet -- are answers, not crashes.
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    reports = [(compare(a, b, window_s=args.window, tie_s=args.tie),
                compare(a, b, window_s=2 * args.window, tie_s=args.tie))
               for a, b in combinations(streams, 2)]
    print_report(streams, offsets, reports, method=args.method)
    if args.events:
        rows = write_events(args.events, [rep for rep, _ in reports])
        print(f"{rows} paired moves -> {args.events}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
