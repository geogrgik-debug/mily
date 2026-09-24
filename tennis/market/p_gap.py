"""How far two books disagree on p, the server's chance of winning a point.

START_HERE carried "two books differ on p by 1.6 points" that nothing in the
repository computed (HANDOFF, "Сверка чисел"). This measures it on BetBoom
and 1win recorded together on one machine:

    python -m tennis.market.p_gap betboom=PATH 1win=PATH [--games games.csv]

For every game both books priced before it began, one number:

* each book's two-way "winner of the game", margin removed (Shin, as in
  `measure`), read as P(the server holds). The server is BetBoom's, from its
  scoreboard, by alternation (`GameContext.server_of`);
* p from P(hold), by inverting `markov.p_game`;
* at one moment: the last one before either book shut the market at which
  both were open and neither had moved for `settle_s`. 1win follows BetBoom
  by a median of 2 s (lead_lag, 23.09), so a moment right after a move would
  measure that lag, not a disagreement.

A game counts only while the one before it is being played. The price of a
game in progress depends on its point score, which an inversion from 0-0 does
not know. Games of one match are not independent, so the interval resamples
matches, not games.

Which side serves does not change the size of the gap: a game is won by the
server with p exactly as often as it is lost by the server with 1 - p, so
p_game(1 - p) = 1 - p_game(p), and both books' p flip together.

Research and paper only: nothing here places, sizes or times a bet.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import math
import random
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from tennis.ingest.rawlog import find_logs, read_raw
from tennis.market.join import pair_matches
from tennis.market.lead_lag import _stream_arg, quantile
from tennis.market.measure import HOME, game_context
from tennis.market.names import GAME_WINNER
from tennis.market.overround import overround
from tennis.market.streams import (
    GAP_S,
    ClockMismatch,
    Quote,
    RunClock,
    Stream,
    _rows,
    _stripper,
    betboom_quotes,
    check_same_clock,
    fold,
    onewin_quotes,
)
from tennis.markov import p_game

__all__ = ["SETTLE_S", "GameGap", "p_from_hold", "timelines", "last_settled",
           "game_gaps", "median_ci", "mean_ci", "main"]

# Neither book may have moved for this long before the moment compared: five
# times the median lag of 1win behind BetBoom, and the pairing window of
# lead_lag.
SETTLE_S = 10.0
# A game of the gap's size needs this many resamples for its 2.5% tail.
BOOTSTRAP_ROUNDS = 2000
# The precision the number is wanted to: half the width of its interval.
TARGET_HALF_WIDTH = 0.003
# Fewer matches than this give an interval that says nothing.
MIN_MATCHES = 5


def p_from_hold(hold: float, *, iters: int = 100) -> float:
    """The point probability whose game from 0-0 is held with `hold`.

    Bisection over the whole of (0, 1): p_game rises strictly, and the old
    `invert()` bug -- a search that only looked on one side of 0.5 -- is the
    reason this does not start from a favourite.
    """
    if not 0.0 < hold < 1.0:
        raise ValueError(f"hold must be in (0, 1), got {hold!r}")
    lo, hi = 0.0, 1.0
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if p_game(mid) < hold:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# --------------------------------------------------------------- books over time

State = tuple | None          # ((outcome, odds), ...) while the book can be bet


def _state(book: dict) -> State:
    """A two-way book's odds while both outcomes can be bet; else None."""
    if len(book) != 2:
        return None
    for odds, active in book.values():
        if not active or odds is None or not math.isfinite(odds) or odds <= 1.0:
            return None
    return tuple(sorted((name, odds) for name, (odds, _) in book.items()))


def timelines(quotes: Iterable[Quote | None],
              rename: Callable[[Quote], tuple | None] | None = None
              ) -> dict[tuple, list[tuple[int, State]]]:
    """Each book's states in time, as (ts_received_ns, state) at every change.

    `rename` gives a quote its (match, outcome) in the other book's names, or
    None to drop it. A None in the input is a break in the capture: every book
    is shut at the last time seen, so no state reaches across a gap.
    """
    books: dict[tuple, dict] = defaultdict(dict)
    out: dict[tuple, list] = defaultdict(list)
    last_ts = None
    for q in quotes:
        if q is None:
            for key, line in out.items():
                if line[-1][1] is not None and last_ts is not None:
                    line.append((last_ts, None))
            books.clear()
            continue
        match, outcome = (q.match, q.outcome) if rename is None else (rename(q) or (None, None))
        if match is None:
            continue
        last_ts = q.ts_received_ns
        key = (match, q.market)
        book = books[key]
        if q.odds is None:
            book.pop(outcome, None)
        else:
            book[outcome] = (q.odds, q.active)
        state = _state(book)
        line = out[key]
        if not line or line[-1][1] != state:
            line.append((q.ts_received_ns, state))
    return dict(out)


def last_settled(a: Sequence[tuple[int, State]], b: Sequence[tuple[int, State]],
                 ok: Callable[[int], bool], settle_ns: int) -> tuple[int, State, State] | None:
    """The last moment both books were open and had not moved for `settle_ns`.

    Moments are read just before each change of either book: that is when a
    state has lasted longest. `ok(t)` says whether the game had not begun yet
    at `t`.
    """
    times = sorted({t for t, _ in a} | {t for t, _ in b})
    ia = ib = 0
    sa = sb = None
    la = lb = None
    found = None
    for t in times:
        if (sa is not None and sb is not None and t - la >= settle_ns
                and t - lb >= settle_ns and ok(t)):
            found = (t, sa, sb)
        while ia < len(a) and a[ia][0] == t:
            sa, la = a[ia][1], t
            ia += 1
        while ib < len(b) and b[ib][0] == t:
            sb, lb = b[ib][1], t
            ib += 1
    return found


# --------------------------------------------------------------- games


@dataclass(frozen=True)
class GameGap:
    """One game both books priced before it began, at one settled moment.
    `a` is BetBoom, whose match id and scoreboard are used; `b` is 1win."""

    match: object
    tier: str
    set_no: int
    game_no: int
    ts_received_ns: int
    hold_a: float
    hold_b: float
    p_a: float
    p_b: float
    margin_a: float
    margin_b: float

    @property
    def dp(self) -> float:
        """1win minus BetBoom: positive when 1win rates the server higher."""
        return self.p_b - self.p_a


def _board_before(boards: list, t: int):
    """The scoreboard last seen strictly before `t`, or None."""
    i = bisect.bisect_left(boards, t, key=lambda row: row[0])
    return boards[i - 1][1] if i else None


def game_gaps(a_books: dict, b_books: dict, boards: dict, tiers: dict, *,
              method: str = "shin", settle_s: float = SETTLE_S) -> list[GameGap]:
    """Every game market both books quoted, at its last settled moment.

    `a_books` and `b_books` are `timelines` keyed by BetBoom's match ids and
    outcome names; `boards` holds each match's scoreboard in time, as
    (ts_received_ns, GameContext).
    """
    strip = _stripper(method)
    settle_ns = int(settle_s * 1e9)
    out = []
    for key in sorted(a_books.keys() & b_books.keys(), key=repr):
        match, market = key
        scope, set_no, game_no, kind = market
        if scope != "game" or kind != GAME_WINNER or match not in boards:
            continue
        board = boards[match]

        def before_it_began(t: int) -> bool:
            ctx = _board_before(board, t)
            return (ctx is not None and ctx.set_no == set_no
                    and ctx.game_no == game_no - 1 and ctx.server_of(game_no) is not None)

        found = last_settled(a_books[key], b_books[key], before_it_began, settle_ns)
        if found is None:
            continue
        t, sa, sb = found
        server = "П1" if _board_before(board, t).server_of(game_no) == HOME else "П2"
        holds, margins = [], []
        for state in (sa, sb):
            names = [name for name, _ in state]
            if server not in names:
                break
            odds = [o for _, o in state]
            holds.append(strip(odds)[names.index(server)])
            margins.append(overround(odds))
        if len(holds) != 2:
            continue
        out.append(GameGap(match, tiers.get(match, "?"), set_no, game_no, t,
                           holds[0], holds[1], p_from_hold(holds[0]), p_from_hold(holds[1]),
                           margins[0], margins[1]))
    return out


# --------------------------------------------------------------- statistics


def _bootstrap(games: Sequence[GameGap], value: Callable[[GameGap], float],
               stat: Callable, rounds: int, seed: int) -> tuple[float, float, float]:
    by_match: dict[object, list[float]] = defaultdict(list)
    for g in games:
        by_match[g.match].append(value(g))
    matches = sorted(by_match, key=repr)
    rng = random.Random(seed)
    draws = []
    for _ in range(rounds):
        picked = [rng.choice(matches) for _ in matches]
        draws.append(stat([v for m in picked for v in by_match[m]]))
    return stat([value(g) for g in games]), quantile(draws, 0.025), quantile(draws, 0.975)


def median_ci(games: Sequence[GameGap], value: Callable[[GameGap], float], *,
              rounds: int = BOOTSTRAP_ROUNDS, seed: int = 0) -> tuple[float, float, float]:
    """Median and its 95% interval, resampling whole matches."""
    return _bootstrap(games, value, statistics.median, rounds, seed)


def mean_ci(games: Sequence[GameGap], value: Callable[[GameGap], float], *,
            rounds: int = BOOTSTRAP_ROUNDS, seed: int = 0) -> tuple[float, float, float]:
    """Mean and its 95% interval, resampling whole matches."""
    return _bootstrap(games, value, statistics.fmean, rounds, seed)


# --------------------------------------------------------------- reading logs


def _scoreboards(rows, pb, boards: dict, tiers: dict):
    """Pass BetBoom's rows on, noting each match's scoreboard and tier."""
    for row in rows:
        if row is not None and row.get("dir") == "rx" and isinstance(row.get("payload"), bytes):
            msg = pb.MainResponse()
            try:
                msg.ParseFromString(row["payload"])
            except Exception:
                msg = None
            which = msg.WhichOneof("type") if msg is not None else None
            matches = []
            if which == "matches_subscribe_full":
                for it in msg.matches_subscribe_full.full_matches:
                    tiers[it.match.info.id] = (it.category.info.name
                                               or it.tournament.info.name or "?")
                    matches.append(it.match)
            elif which in ("newsletters_full_match", "newsletters_match"):
                matches.append(getattr(msg, which).match)
            for match in matches:
                ctx = game_context(match.info, pb) if match.info.id else None
                if ctx is not None:
                    boards[match.info.id].append((row["ts_received_ns"], ctx))
        yield row


def _is_game_winner(q: Quote | None) -> bool:
    return q is None or (q.market[0] == "game" and q.market[3] == GAME_WINNER)


def _read(label: str, path: Path):
    """One book's game-winner quotes, run by run, with its clock, players,
    and -- for BetBoom -- its scoreboards and tiers."""
    files = find_logs(path)
    if not files:
        raise FileNotFoundError(f"no logs under {path}")
    provider = next((row.get("provider") for row in read_raw(files[0])), None)
    by_run: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        by_run[f.name.split(".")[0]].append(f)
    players: dict = {}
    boards: dict = defaultdict(list)
    tiers: dict = {}
    quotes: list[Quote | None] = []
    runs: list[RunClock] = []
    pb = None
    if provider == "betboom":
        from tennis.ingest.betboom.client import load_pb
        pb = load_pb()
    elif provider != "1win":
        raise NotImplementedError(f"no quote decoder for provider {provider!r}")
    for run_id, run_files in sorted(by_run.items()):
        clock = RunClock(run_id, [], [])
        rows = _rows(run_files, clock, provider, GAP_S)
        if pb is not None:
            decoded = betboom_quotes(_scoreboards(rows, pb, boards, tiers), pb,
                                     players=players)
        else:
            decoded = onewin_quotes(rows, players)
        quotes.extend(q for q in decoded if _is_game_winner(q))
        quotes.append(None)                    # a new run knows nothing of the last
        if clock.walls:
            runs.append(clock)
    for line in boards.values():
        line.sort(key=lambda row: row[0])
    stream = Stream(label, provider, str(path), fold(quotes), runs, players)
    return stream, quotes, dict(boards), tiers


# --------------------------------------------------------------- report


def _pp(x: float) -> str:
    return f"{100 * x:.2f}"


def _line(label: str, est: tuple[float, float, float], unit: str = "pp") -> str:
    mid, lo, hi = est
    return f"  {label:34s} {_pp(mid)} {unit}  (95% CI {_pp(lo)} to {_pp(hi)})"


def print_report(games: Sequence[GameGap], note: str, offset: float | None,
                 settle_s: float) -> None:
    print(note)
    if offset is not None:
        print(f"same clock: offsets agree to {abs(offset) * 1e3:.1f} ms")
    matches = {g.match for g in games}
    print(f"games both books priced before they began, neither moving for {settle_s:g} s: "
          f"{len(games)} in {len(matches)} matches")
    if not games:
        return
    tiers = defaultdict(list)
    for g in games:
        tiers[g.tier].append(g)
    print("  by tier: " + ", ".join(f"{t} {len(v)}" for t, v in
                                   sorted(tiers.items(), key=lambda kv: -len(kv[1]))))
    gap = median_ci(games, lambda g: abs(g.dp))
    print(_line("|p 1win - p BetBoom|, median", gap))
    print(_line("p 1win - p BetBoom, mean", mean_ci(games, lambda g: g.dp))
          + "  (+: 1win rates the server higher)")
    print(_line("|P(hold) 1win - BetBoom|, median", median_ci(games, lambda g: abs(g.hold_b - g.hold_a))))
    print(f"  {'P(hold), median':34s} BetBoom {statistics.median(g.hold_a for g in games):.3f}, "
          f"1win {statistics.median(g.hold_b for g in games):.3f}; "
          f"p BetBoom {statistics.median(g.p_a for g in games):.3f}")
    print(f"  {'margin of the game winner, median':34s} "
          f"1win {_pp(statistics.median(g.margin_b for g in games))}%, "
          f"BetBoom {_pp(statistics.median(g.margin_a for g in games))}% "
          "(same games, same moments)")
    for tier, rows in sorted(tiers.items(), key=lambda kv: -len(kv[1])):
        if len({g.match for g in rows}) >= MIN_MATCHES:
            print(_line(f"  {tier}: |dp| median", median_ci(rows, lambda g: abs(g.dp))))
    half = (gap[2] - gap[1]) / 2
    if len(matches) < MIN_MATCHES:
        print(f"  too few matches for an interval ({len(matches)} < {MIN_MATCHES}): "
              "a first look, not a number")
    if half > TARGET_HALF_WIDTH:
        need = math.ceil(len(games) * (half / TARGET_HALF_WIDTH) ** 2)
        print(f"  the interval is +-{_pp(half)} pp; +-{_pp(TARGET_HALF_WIDTH)} would take about "
              f"{need} games at this spread")


def write_games(path: str | Path, games: Sequence[GameGap]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["match", "tier", "set", "game", "ts_received_ns", "hold_betboom",
                    "hold_1win", "p_betboom", "p_1win", "dp", "margin_betboom", "margin_1win"])
        for g in games:
            w.writerow([g.match, g.tier, g.set_no, g.game_no, g.ts_received_ns,
                        f"{g.hold_a:.6f}", f"{g.hold_b:.6f}", f"{g.p_a:.6f}", f"{g.p_b:.6f}",
                        f"{g.dp:+.6f}", f"{g.margin_a:.6f}", f"{g.margin_b:.6f}"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("streams", nargs=2, metavar="NAME=PATH",
                    help="BetBoom's logs and 1win's, recorded on one machine")
    ap.add_argument("--settle", type=float, default=SETTLE_S,
                    help="seconds neither book may have moved before the moment compared")
    ap.add_argument("--method", choices=["shin", "proportional"], default="shin",
                    help="how to remove the bookmaker's margin")
    ap.add_argument("--games", metavar="CSV", help="write every game compared here")
    args = ap.parse_args(argv)

    try:
        read = [_read(label, path) for label, path in
                (_stream_arg(arg, i) for i, arg in enumerate(args.streams))]
        by_provider = {r[0].provider: r for r in read}
        if set(by_provider) != {"betboom", "1win"}:
            raise ValueError("need one BetBoom capture and one 1win capture, got "
                             f"{sorted(r[0].provider for r in read)}")
        a, a_quotes, boards, tiers = by_provider["betboom"]
        b, b_quotes, _, _ = by_provider["1win"]
        offset = check_same_clock([a, b])[(a.label, b.label)]
    except (ClockMismatch, NotImplementedError, FileNotFoundError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    paired, counts = pair_matches(a, b)

    def rename(q: Quote):
        if q.match not in paired:
            return None
        match, sides = paired[q.match]
        return (match, sides[q.outcome]) if q.outcome in sides else None

    games = game_gaps(timelines(a_quotes), timelines(b_quotes, rename), boards, tiers,
                      method=args.method, settle_s=args.settle)
    total = len({e.match for e in b.events})
    note = (f"{a.label} = {a.path}, {b.label} = {b.path}: {len(paired)} of 1win's {total} "
            f"matches with game markets paired with BetBoom's; margin removed by {args.method}")
    print_report(games, note, offset, args.settle)
    if args.games:
        write_games(args.games, games)
        print(f"{len(games)} games -> {args.games}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
