"""Raw logs to streams of price events, stamped by one clock.

The half of the lead-lag meter that reads disk; `lead_lag` pairs what it
produces. Three steps, one of them provider-specific:

* A **quote** is one outcome as one frame showed it: odds, and whether it could
  be bet. Decoding frames into quotes is the provider-specific step, and
  `betboom_quotes` is the one decoder so far.
* A **price event** says "the probability of this outcome became X", X being
  its book's probability with the margin removed (Shin by default, as in
  `measure`). One is emitted only when X changes, and a book is priced only
  while every outcome in it can be bet. That is `fold`.
* A **stream** is every capture under one path, folded run by run, with its
  clock sampled for the check below. That is `load_stream`.

One clock, or no answer
-----------------------
Who was first is measurable only when both logs were stamped by one clock, that
is, written on one machine. That is checked, not assumed: logs that ran at the
same time are compared on their wall-minus-monotonic offset at the same
moments. On one machine every process reads the same two clocks, so the offset
is one number for all of them; two machines differ by their boot times. Every
frame carries both readings, so no hostname is needed. Measured on the owner's
laptop, 22-23.09: four runs carried the same offset to within 0.03 s, and the
two that ran together agreed at the same moments to 0.0 ms.

The same laptop's wall clock stepped by 6.6 s on waking from 8.8 hours asleep.
So order and the pairing window are read on the wall clock, `ts_received_ns`,
and the size of a lag on the monotonic one, which does not step. For a pair of
moves seconds apart the two agree, except across a step, where only the
monotonic clock is right.
"""
from __future__ import annotations

import bisect
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

from tennis.ingest.rawlog import find_logs, read_raw
from tennis.market.diff import diff_snapshots
from tennis.market.names import parse_market
from tennis.market.overround import normalize_proportional, shin

__all__ = [
    "Quote", "PriceEvent", "RunClock", "Stream", "ClockMismatch", "SOURCES",
    "fold", "betboom_quotes", "load_stream", "clock_offset", "check_same_clock",
]

# Updates to one book within this of the first are one repricing. A two-way
# book pushed outcome by outcome arrives as two frames milliseconds apart.
SETTLE_S = 0.25
# A capture that heard nothing at all for this long was not listening: asleep,
# or offline. Nothing it knew before is trusted after.
GAP_S = 60.0
# Two logs whose clocks disagree by more than this were not written on one
# machine. One machine agrees with itself to microseconds; two differ by their
# boot times, which is seconds at the very least.
CLOCK_TOLERANCE_S = 1.0

# BetBoom frame kinds, by what they carry. `full`: every market of a subscribed
# match, re-sent on each score change. `stake`: one outcome per frame, pushed
# on a price change, for the outcomes the recorder follows. `tour`: the
# headline odds of every live match, subscribed or not.
SOURCES = ("full", "stake", "tour")


@dataclass(frozen=True)
class Quote:
    """One outcome as one frame showed it.

    `match` and `market` only have to be comparable across the streams being
    compared; the outcomes that share a (match, market) form one book, whose
    margin is removed together. `odds` is None when the outcome left the board.
    """

    ts_received_ns: int
    ts_mono_ns: int
    match: object
    market: tuple
    outcome: str
    odds: float | None
    active: bool = True


@dataclass(frozen=True)
class PriceEvent:
    """The margin-free probability of one outcome became `prob`.

    `prev` is the probability it replaced in the same unbroken capture, None
    when there was none -- the first quote, or the first after a gap. Only an
    event with a `prev` is a move.
    """

    ts_received_ns: int
    ts_mono_ns: int
    match: object
    market: tuple
    outcome: str
    prob: float
    prev: float | None = None

    @property
    def key(self) -> tuple:
        return (self.match, self.market, self.outcome)

    @property
    def is_move(self) -> bool:
        return self.prev is not None

    @property
    def direction(self) -> int:
        if self.prev is None:
            return 0
        return (self.prob > self.prev) - (self.prob < self.prev)


# --------------------------------------------------------------- quotes to events


def _stripper(method: str) -> Callable[[list[float]], list[float]]:
    if method == "shin":
        return lambda odds: shin(odds)[0]
    if method == "proportional":
        return normalize_proportional
    raise ValueError(f"unknown margin method {method!r}")


def _price(book: dict, strip) -> dict[str, float] | None:
    """Margin-free probabilities of a book, or None while it cannot be bet.

    A suspended outcome carries a placeholder (1.01, 100.0, 0.0 are all seen
    live), and a book missing an outcome is not a book, so either leaves the
    whole book unpriced rather than priced from what is left.
    """
    if len(book) < 2:
        return None
    names = sorted(book)
    odds = []
    for name in names:
        price, active = book[name]
        if not active or not math.isfinite(price) or price <= 1.0:
            return None
        odds.append(price)
    return dict(zip(names, strip(odds)))


def fold(quotes: Iterable[Quote | None], *, method: str = "shin",
         min_move: float = 0.0, settle_s: float = SETTLE_S) -> list[PriceEvent]:
    """One capture's quotes, in arrival order, to price events.

    A None in the input is a break in the capture -- a reconnect, a silence.
    Everything known before it is forgotten, so no move is measured across a
    gap: its time would be when the capture came back, not when the book moved.

    Updates to one book within `settle_s` of the first are one repricing,
    stamped with the time of the first. Priced after each frame, a two-way book
    pushed outcome by outcome would move twice, the first time to a number the
    book never quoted.

    A move smaller than `min_move` is not emitted, and the value it would have
    replaced stays the reference, so slow drift still surfaces once it adds up.
    """
    strip = _stripper(method)
    settle_ns = int(settle_s * 1e9)
    books: dict[tuple, dict] = defaultdict(dict)
    pending: dict[tuple, Quote] = {}
    last: dict[tuple, float] = {}
    out: list[PriceEvent] = []

    def flush(upto_ns: int | None = None) -> None:
        # `pending` is in order of first update, which is arrival order, so the
        # scan stops at the first burst that is not due yet.
        due = []
        for key, first in pending.items():
            if upto_ns is not None and first.ts_mono_ns > upto_ns:
                break
            due.append(key)
        for key in due:
            first = pending.pop(key)
            probs = _price(books[key], strip)
            if probs is None:
                continue
            for outcome, prob in probs.items():
                okey = (*key, outcome)
                prev = last.get(okey)
                if prev is not None and (prob == prev or abs(prob - prev) < min_move):
                    continue
                last[okey] = prob
                out.append(PriceEvent(first.ts_received_ns, first.ts_mono_ns,
                                      key[0], key[1], outcome, prob, prev))

    for quote in quotes:
        if quote is None:
            flush()
            books.clear()
            last.clear()
            continue
        flush(quote.ts_mono_ns - settle_ns)
        key = (quote.match, quote.market)
        if quote.odds is None:
            books[key].pop(quote.outcome, None)
        else:
            books[key][quote.outcome] = (quote.odds, quote.active)
        pending.setdefault(key, quote)
    flush()
    out.sort(key=lambda e: e.ts_mono_ns)
    return out


# --------------------------------------------------------------- BetBoom frames


def _is_line(stake) -> bool:
    """A stake on one line of a market quoting several: totals, handicaps.

    Such a market_name holds several books at once (live: "1-й сет: Тотал" at
    9.5 and 10.5 in one snapshot), and which outcomes share a margin is not
    stated -- a handicap's "(+1.5)" of one player pairs with the other's
    "(-1.5)", and both players carry both signs. Pairing them would be a guess,
    so lines are left out; what is left is one book per market_name.
    """
    has_field = getattr(stake, "HasField", None)
    if has_field is not None:
        return has_field("argument")
    return getattr(stake, "argument", None) is not None


def betboom_quotes(rows: Iterable[dict | None], pb,
                   sources: Sequence[str] = SOURCES) -> Iterator[Quote | None]:
    """Frames of one BetBoom capture to quotes; None wherever the capture broke.

    Each source reports only what changed in its own stream: a full snapshot is
    differenced against the previous one of its match (`diff_snapshots`), and a
    tour frame against what the tour stream last said. A source that lags must
    not put back a price another source has already moved on from just by
    repeating itself.

    Absence means different things per source. A full snapshot is the whole
    board, so an outcome missing from it is gone; a tour frame carries only
    headline odds, and 355 of 6940 of them live carried none at all, so absence
    there means nothing. Full snapshots without stakes (UPDATE_INFO, DELETE: 46
    of 2351 live) are not boards either, and are skipped as `measure` does.

    A per-stake push is keyed by its own market_name when it has one, and
    otherwise by the snapshot that named its stake_id -- the recorder takes the
    ids from that snapshot, so one always came first.
    """
    full_last: dict[int, list] = {}
    tour_last: dict[int, dict] = {}
    names_by_id: dict[str, tuple] = {}
    delete = pb.NEWSLETTER_ACTIONS_DELETE
    for row in rows:
        if row is None:
            full_last.clear()
            tour_last.clear()
            yield None
            continue
        if row.get("dir") != "rx" or not isinstance(row.get("payload"), bytes):
            continue
        msg = pb.MainResponse()
        try:
            msg.ParseFromString(row["payload"])
        except Exception:
            continue
        which = msg.WhichOneof("type")
        stamp = (row["ts_received_ns"], row["ts_mono_ns"])

        if which in ("matches_subscribe_full", "newsletters_full_match") and "full" in sources:
            if which == "matches_subscribe_full":
                boards = [item.match for item in msg.matches_subscribe_full.full_matches]
            else:
                boards = [msg.newsletters_full_match.match]
            for match in boards:
                mid = match.info.id
                if not mid or not match.stakes:
                    continue
                stakes = [s for s in match.stakes if not _is_line(s)]
                for s in stakes:
                    names_by_id[s.stake_id] = (parse_market(s.market_name).key(), s.name)
                for ch in diff_snapshots(full_last.get(mid, []), stakes):
                    yield Quote(*stamp, mid, ch.ref.key(), ch.outcome, ch.new, ch.is_active)
                full_last[mid] = stakes

        elif which == "newsletters_stake" and "stake" in sources:
            body = msg.newsletters_stake
            s = body.stake
            if not s.match_id or _is_line(s):
                continue
            if s.market_name:
                market, outcome = parse_market(s.market_name).key(), s.name
            elif s.stake_id in names_by_id:
                market, outcome = names_by_id[s.stake_id]
            else:
                continue
            gone = body.action == delete
            yield Quote(*stamp, s.match_id, market, outcome,
                        None if gone else s.factor, not gone and s.is_active)

        elif which == "newsletters_match" and "tour" in sources:
            match = msg.newsletters_match.match
            mid = match.info.id
            frame: dict[tuple, object] = {}
            for s in match.stakes:
                if not _is_line(s):
                    frame.setdefault((parse_market(s.market_name), s.name), s)
            if not mid or not frame:
                continue
            seen = tour_last.setdefault(mid, {})
            for ch in diff_snapshots(list(seen.values()), list(frame.values())):
                if ch.new is not None:
                    yield Quote(*stamp, mid, ch.ref.key(), ch.outcome, ch.new, ch.is_active)
            seen.update(frame)


def _decoder(provider: str, sources: Sequence[str]):
    if provider == "betboom":
        from tennis.ingest.betboom.client import load_pb
        pb = load_pb()
        return lambda rows: betboom_quotes(rows, pb, sources)
    raise NotImplementedError(
        f"no quote decoder for provider {provider!r}. 1win's odds channel is not "
        "known yet (tennis/ingest/onewin/README.md); its decoder belongs here, "
        "next to betboom_quotes, once a captured response shows the shape.")


# --------------------------------------------------------------- logs and clocks


class ClockMismatch(ValueError):
    """Two logs were not stamped by one clock, so their order means nothing."""


@dataclass
class RunClock:
    """One capture's clock, sampled once a second: wall, and wall - monotonic."""

    run_id: str
    walls: list[int]
    anchors: list[int]

    @property
    def start(self) -> int:
        return self.walls[0]

    @property
    def end(self) -> int:
        return self.walls[-1]


@dataclass
class Stream:
    """One side of a comparison: every capture under one path, folded."""

    label: str
    provider: str
    path: str
    events: list[PriceEvent]
    runs: list[RunClock]


def clock_offset(a: RunClock, b: RunClock, near_s: float = 5.0) -> float | None:
    """How far apart the two captures' clocks are, in seconds.

    The median difference of their wall-minus-monotonic offsets at the same
    moments. About zero on one machine; the difference in boot times on two.
    None when the captures never ran at the same time.
    """
    near_ns = int(near_s * 1e9)
    diffs = []
    for wall, anchor in zip(a.walls, a.anchors):
        k = bisect.bisect_left(b.walls, wall)
        near = [i for i in (k - 1, k) if 0 <= i < len(b.walls)
                and abs(b.walls[i] - wall) <= near_ns]
        if near:
            i = min(near, key=lambda i: abs(b.walls[i] - wall))
            diffs.append(anchor - b.anchors[i])
    if not diffs:
        return None
    return statistics.median(diffs) / 1e9


def check_same_clock(streams: Sequence[Stream],
                     tolerance_s: float = CLOCK_TOLERANCE_S) -> dict[tuple[str, str], float | None]:
    """Refuse streams that were not written against one clock.

    Returns, for each pair of streams, the largest clock offset among their
    captures that ran together, or None if none did. Raises ClockMismatch if
    any offset is over `tolerance_s`: comparing when two machines heard
    something is comparing their clocks, not their feeds.
    """
    out: dict[tuple[str, str], float | None] = {}
    for s, t in combinations(streams, 2):
        worst = None
        for ra in s.runs:
            for rb in t.runs:
                off = clock_offset(ra, rb)
                if off is None:
                    continue
                if abs(off) > tolerance_s:
                    raise ClockMismatch(
                        f"{s.label} ({ra.run_id}) and {t.label} ({rb.run_id}) were "
                        f"not written on one machine: their clocks differ by {off:+.3f} s. "
                        "Who moved first is measurable only from one machine's logs.")
                worst = off if worst is None or abs(off) > abs(worst) else worst
        out[(s.label, t.label)] = worst
    return out


def _rows(files: Sequence[Path], clock: RunClock, provider: str, gap_s: float):
    """Rows of one capture in order, None at every break, sampling its clock."""
    gap_ns = int(gap_s * 1e9)
    last_mono = None
    next_sample = None
    for path in sorted(files):
        for row in read_raw(path):
            if row.get("provider") != provider:
                raise ValueError(f"{path} mixes providers: {row.get('provider')!r} "
                                 f"in a {provider!r} capture")
            wall, mono = row["ts_received_ns"], row["ts_mono_ns"]
            if next_sample is None or wall >= next_sample:
                clock.walls.append(wall)
                clock.anchors.append(wall - mono)
                next_sample = wall + 1_000_000_000
            if last_mono is not None and mono - last_mono > gap_ns:
                yield None
            last_mono = mono
            if row.get("dir") == "meta" and row.get("channel") == "_conn":
                yield None                  # the recorder reconnected
                continue
            yield row


def load_stream(label: str, path: str | Path, *, sources: Sequence[str] = SOURCES,
                method: str = "shin", min_move: float = 0.0,
                settle_s: float = SETTLE_S, gap_s: float = GAP_S,
                decoder: Callable | None = None) -> Stream:
    """Every capture under `path`, folded into one stream of price events.

    A capture is a run: one recorder process, possibly across several daily
    files. Runs are folded one by one -- a restarted recorder knows nothing of
    the last one -- and must not overlap in time: two recorders running at
    once are two streams, which is exactly the zero test.
    """
    files = find_logs(path)
    if not files:
        raise FileNotFoundError(f"no logs under {path}")
    provider = next((row.get("provider") for row in read_raw(files[0])), None)
    if provider is None:
        raise ValueError(f"{files[0]} holds no readable frame")
    decode = decoder or _decoder(provider, sources)

    by_run: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        by_run[f.name.split(".")[0]].append(f)
    runs: list[RunClock] = []
    events: list[PriceEvent] = []
    for run_id, run_files in sorted(by_run.items()):
        clock = RunClock(run_id, [], [])
        events.extend(fold(decode(_rows(run_files, clock, provider, gap_s)),
                           method=method, min_move=min_move, settle_s=settle_s))
        if clock.walls:
            runs.append(clock)
    runs.sort(key=lambda r: r.start)
    for before, after in zip(runs, runs[1:]):
        if after.start < before.end:
            raise ValueError(f"{path}: {before.run_id} and {after.run_id} were recording "
                             "at the same time; pass each as a stream of its own")
    events.sort(key=lambda e: e.ts_received_ns)
    return Stream(label, provider, str(path), events, runs)
