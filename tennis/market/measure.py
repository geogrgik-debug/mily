"""Measure a bookmaker's game markets from a captured raw log.

Answers the project's top open question -- what the margin on the two-way
"winner of the next game" market actually is -- with a distribution rather than
the single 9.9% screenshot it had before. And it checks the structural claim
the whole project rests on: that every outcome of a service game is a function
of one point-win probability, so a book quoting eight exact-score prices and a
book quoting one two-way price should agree.

    python -m tennis.market.measure data/raw

It reads only what is on disk. Nothing here touches the network, so a
measurement can be repeated on the same capture and get the same answer.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from tennis.ingest.betboom.client import load_pb
from tennis.ingest.rawlog import find_logs, read_raw
from tennis.market.names import (
    GAME_EXACT_SCORE,
    GAME_SERVER_SCORE_OR_BREAK,
    GAME_WINNER,
    parse_exact_score_outcome,
    parse_market,
)
from tennis.market.overround import normalize_proportional, overround, shin
from tennis.market.fit import fit_point_prob

__all__ = ["GameContext", "game_context", "collect", "main"]

HOME, AWAY = 1, 2


@dataclass(frozen=True)
class GameContext:
    """Which game is being played, and who is serving it.

    Needed because the eight-way exact-score market is indexed by player
    ("П1:0") while the model is indexed by server (hold/break). Without the
    serving side the two cannot be joined, and joining them wrong silently
    mirrors p to 1-p.
    """

    set_no: int
    game_no: int
    server: int          # 1 = home, 2 = away

    def matches(self, ref) -> bool:
        return ref.set_no == self.set_no and ref.game_no == self.game_no

    def server_of(self, game_no: int) -> int | None:
        """Who serves game `game_no` of this set, by alternation.

        This exists because of the single most useful thing the capture showed:
        **the book prices the next game, not the one being played.** With the
        scoreboard at game 9 the quoted markets are for game 10, consistently,
        across every match in the sample. That is exactly the quantity this
        project models -- the next, not-yet-started service game -- which means
        the bookmaker's own number is directly comparable to ours.

        It also means the server flips. Reading the scoreboard's serving_side
        for a next-game market would mirror every probability to its
        complement, which is a silent error that still produces plausible
        numbers, so it is worth the explicit helper.

        Returns None past the twelfth game: at 6-6 the tiebreak uses the 1-2-2
        rotation, not alternation, and guessing there would be wrong.
        """
        if game_no > 12 or game_no < 1:
            return None
        step = game_no - self.game_no
        return self.server if step % 2 == 0 else (HOME if self.server == AWAY else AWAY)


def game_context(info, pb) -> GameContext | None:
    """Current (set, game, server) from a match's scoreboard.

    The feed gives the set in `scoreboard.current_game_part` and the games won
    in each set as PART scores keyed by `sequence`. The game number is one more
    than the games already played in the current set, which is only correct
    while a game is in progress -- which is exactly when a game market is
    quoted, so it is the right reading here.
    """
    if not info.HasField("scoreboard"):
        return None
    sb = info.scoreboard
    set_no = getattr(sb, "current_game_part", 0)
    server = getattr(sb, "serving_side", 0)
    if not set_no or server not in (HOME, AWAY):
        return None
    for sc in sb.scores:
        if sc.type == pb.SCOREBOARD_SCORE_TYPES_PART and sc.sequence == set_no:
            return GameContext(set_no=set_no,
                               game_no=sc.home_score + sc.away_score + 1,
                               server=server)
    return None


def _books(stakes, ref_filter):
    """Group active stakes into {market_ref: {outcome_name: odds}}."""
    books = defaultdict(dict)
    for s in stakes:
        if not s.is_active or s.factor <= 1.0:
            continue
        ref = parse_market(s.market_name)
        if not ref_filter(ref):
            continue
        books[ref].setdefault(s.name, s.factor)
    return books


def _to_model_keys(book: dict, server: int) -> dict | None:
    """Eight player-indexed exact-score prices to server-indexed model keys."""
    out = {}
    for name, odds in book.items():
        parsed = parse_exact_score_outcome(name)
        if parsed is None:
            return None
        winner, loser_points = parsed
        side = "hold" if winner == server else "break"
        out[f"{side}_{['0', '15', '30', '40'][loser_points]}"] = odds
    return out if len(out) == 8 else None


def collect(paths, pb, method="shin"):
    """One row per (snapshot, game market) with its margin, plus fit rows.

    `method` picks how the margin is removed -- 'shin' or 'proportional'. It is
    a parameter rather than a constant because the two disagree by more than a
    point of hold probability on these books, which is a large fraction of the
    5.5-point prior error the project is trying to beat. Anything that size
    must be a stated choice, not a default buried in a helper.
    """
    strip = shin if method == "shin" else (
        lambda o: (normalize_proportional(o), 0.0))
    margins = []          # (kind, n_outcomes, overround, tier, match_id)
    agreements = []       # (match_id, tier, set, game, p_fit, hold_fit, hold_two_way, max_resid)
    lead = Counter()      # quoted game number minus the one being played
    tiers = {}
    snapshots = 0

    for path in paths:
        for row in read_raw(path):
            if row.get("dir") != "rx" or not isinstance(row.get("payload"), bytes):
                continue
            msg = pb.MainResponse()
            try:
                msg.ParseFromString(row["payload"])
            except Exception:
                continue
            which = msg.WhichOneof("type")
            items = []
            if which == "matches_subscribe_full":
                for it in msg.matches_subscribe_full.full_matches:
                    tiers[it.match.info.id] = (it.category.info.name
                                               or it.tournament.info.name or "?")
                    items.append(it.match)
            elif which == "newsletters_full_match":
                items.append(msg.newsletters_full_match.match)
            for match in items:
                if not match.stakes:
                    continue
                snapshots += 1
                ctx = game_context(match.info, pb)
                mid = match.info.id
                tier = tiers.get(mid, "?")
                books = _books(match.stakes, lambda r: r.is_game)
                for ref, book in books.items():
                    n = len(book)
                    expected = {GAME_WINNER: 2, GAME_EXACT_SCORE: 8,
                                GAME_SERVER_SCORE_OR_BREAK: 5}.get(ref.kind)
                    if expected is None or n != expected:
                        continue
                    margins.append((ref.kind, n, overround(list(book.values())),
                                    tier, mid))

                if ctx is None:
                    continue
                for ref in books:
                    if ref.kind == GAME_WINNER and ref.set_no == ctx.set_no:
                        lead[ref.game_no - ctx.game_no] += 1
                # Pair the two markets per game, whichever game they are for --
                # in practice the next one. Grouping by the market's own game
                # number rather than the scoreboard's is what makes this work.
                per_game = defaultdict(dict)
                for ref, book in books.items():
                    if ref.set_no != ctx.set_no:
                        continue
                    if ref.kind == GAME_WINNER and len(book) == 2:
                        per_game[ref.game_no]["two_way"] = book
                    elif ref.kind == GAME_EXACT_SCORE and len(book) == 8:
                        per_game[ref.game_no]["exact"] = book
                for game_no, pair in per_game.items():
                    if "two_way" not in pair or "exact" not in pair:
                        continue
                    server = ctx.server_of(game_no)
                    if server is None:
                        continue
                    keyed = _to_model_keys(pair["exact"], server)
                    if keyed is None:
                        continue
                    names = list(keyed)
                    probs, _ = strip([keyed[k] for k in names])
                    fit = fit_point_prob(dict(zip(names, probs)))
                    two_way = pair["two_way"]
                    tw_names = list(two_way)
                    tw_probs, _ = strip([two_way[k] for k in tw_names])
                    server_name = "П1" if server == HOME else "П2"
                    if server_name not in tw_names:
                        continue
                    hold_two_way = tw_probs[tw_names.index(server_name)]
                    agreements.append((mid, tier, ctx.set_no, game_no,
                                       fit.p, fit.hold, hold_two_way,
                                       fit.max_residual))
    return margins, agreements, snapshots, lead


def _report(label, values, unit="%"):
    if not values:
        print(f"  {label:44s} no observations")
        return
    vals = sorted(values)
    mult = 100.0 if unit == "%" else 1.0
    print(f"  {label:44s} n={len(vals):5d}  "
          f"median={statistics.median(vals)*mult:6.2f}{unit}  "
          f"mean={statistics.fmean(vals)*mult:6.2f}{unit}  "
          f"p10={vals[len(vals)//10]*mult:6.2f}  "
          f"p90={vals[-1-len(vals)//10]*mult:6.2f}  "
          f"min={vals[0]*mult:6.2f}  max={vals[-1]*mult:6.2f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default="data/raw")
    ap.add_argument("--method", choices=["shin", "proportional"], default="shin",
                    help="how to remove the bookmaker's margin")
    args = ap.parse_args(argv)

    paths = find_logs(args.root)
    if not paths:
        print(f"no logs under {args.root}", file=sys.stderr)
        return 1
    pb = load_pb()
    margins, agreements, snapshots, lead = collect(paths, pb, args.method)
    print(f"{len(paths)} log file(s), {snapshots} match snapshots with stakes, "
          f"margin removed by {args.method}\n")

    print("=== which game is quoted, relative to the one being played ===")
    total = sum(lead.values()) or 1
    for step, n in sorted(lead.items()):
        label = {0: "the game in progress", 1: "the NEXT game"}.get(step, f"{step:+d} games")
        print(f"  {label:24s} {n:5d}  ({100.0*n/total:5.1f}%)")
    print()

    print("=== overround by game market ===")
    by_kind = defaultdict(list)
    for kind, n, ov, tier, mid in margins:
        by_kind[(kind, n)].append(ov)
    for (kind, n), vals in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
        _report(f"{kind!r} ({n} outcomes)", vals)

    print("\n=== two-way game winner, by tier ===")
    by_tier = defaultdict(list)
    for kind, n, ov, tier, mid in margins:
        if kind == GAME_WINNER:
            by_tier[tier].append(ov)
    for tier, vals in sorted(by_tier.items(), key=lambda kv: -len(kv[1])):
        _report(tier, vals)

    print("\n=== do the two markets on the same game agree? ===")
    if not agreements:
        print("  no game had both markets quoted with a readable scoreboard")
    else:
        diffs = [abs(h_fit - h_tw) for _, _, _, _, _, h_fit, h_tw, _ in agreements]
        resids = [r for *_, r in agreements]
        _report("|P(hold) from 8-way - from 2-way|", diffs)
        _report("max residual of the 8-outcome fit", resids, unit="")
        print("\n  sample of individual games:")
        print("   match      tier         set/game   p_fit  hold_8way  hold_2way   diff  max_resid")
        for mid, tier, s, g, p, h8, h2, res in agreements[:15]:
            print(f"   {mid}  {tier:11s}  {s}/{g:<8d} {p:.3f}     {h8:.3f}      {h2:.3f}  "
                  f"{h8-h2:+.3f}     {res:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
