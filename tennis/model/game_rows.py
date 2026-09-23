"""One row per service game, built from only what was known before it.

Track B, step 3 (`HANDOFF.md`): the bridge between a match as it unfolds and the
model. Experiment B2 (`research/slam_process_study.py`) built the same rows with
whole-column pandas over a finished tournament; live there is no finished
tournament, only a game that just ended and a next one about to start. So this
builds the rows the way the live process meets them -- one at a time, in order --
and that shape is what makes leakage impossible rather than merely tested for.

**Leak safety is structural.** The row for game *k* is emitted from accumulators
that hold games 0..k-1; game *k* is folded in only afterwards. The future has not
been read when the row is made, so it cannot leak into it. `build_game_rows`
never looks past the game it is on. `tests/test_game_rows.py` still poisons every
later game and demands the earlier rows unchanged -- the guarantee is by
construction, and the test guards a future refactor from breaking it.

What a row carries, server's side first, all strictly before this game:

* `hold` -- the target: did the server win this game.
* `prior_p_serve` -- the pre-match prior, P(server wins a point on serve),
  constant across the match (it is as of the tournament start). Live it comes
  from `RatingsSnapshot.prior(server, returner, surface, best_of, as_of).p_serve_a`.
* `live_spw` -- serve points won so far this match, shrunk toward the prior.
* `live_hold_rate` -- service games held so far, shrunk toward the tour rate.
* the same two for the returner, from his own service games so far, his
  `live_spw` shrunk toward the prior of his own serve.
* `prev_hold` and its shape (easy, hard, broken) -- the last service game.
* context -- game score, serving for the set, serving to stay in it, set number.

The builder needs no data files and no pandas: the prior is supplied as a
callable, so the same code runs in a test with a stub and live against a
`RatingsSnapshot`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable

from tennis.state import DEFAULT_N0

# The tour holds its serve about this often; live_hold_rate is shrunk toward it
# while a server has few games behind them, exactly as B2 shrank toward the
# sample mean. A constant here, not measured per surface -- the prior already
# carries the surface, and this only steadies the first few games.
TOUR_HOLD_RATE = 0.80

# live_spw is the live state's belief (tennis.state) at the tour's prior
# strength, DEFAULT_N0 phantom serve points fitted on history; the live state
# itself uses N0[level] when the level is known, which on the training years
# costs this feature under 0.0002 of log loss. The 40 carried over from
# research/slam_process_study.py was too weak -- it lost 0.002-0.0035 of log
# loss on every test segment (tennis/state/README.md). live_hold_rate keeps
# research's 6 phantom games toward the tour hold rate.
SPW_SHRINK_POINTS = DEFAULT_N0
HOLD_SHRINK_GAMES = 6


@dataclass(frozen=True)
class GamePlay:
    """One completed service game as the live feed hands it over.

    `points` is the game point by point, 1 where the server won the point and 0
    where the returner did -- enough to decide the game and to feed the live
    per-point update (step 4). `server_games`/`returner_games` are the set score
    *before* this game, which the feed always knows.
    """

    server: int
    returner: int
    points: tuple[int, ...]
    set_no: int
    server_games: int
    returner_games: int


@dataclass(frozen=True)
class GameRow:
    """The model's view of one service game: the target and everything known
    before it. Frozen, so two builds are compared field for field in the leak
    test."""

    match_id: str
    set_no: int
    game_no: int          # 0-based index of this service game within the match
    server: int
    returner: int
    hold: int             # target: 1 if the server won this game

    prior_p_serve: float
    # server's own service games so far
    cum_serve_points: int
    cum_serve_won: int
    live_spw: float
    cum_games: int
    cum_holds: int
    live_hold_rate: float
    # returner's own service games so far
    opp_cum_serve_points: int
    opp_cum_serve_won: int
    opp_live_spw: float
    opp_cum_games: int
    opp_live_hold_rate: float
    # the server's previous service game
    has_prev: int
    prev_hold: int        # -1 when there is no previous game
    prev_easy_hold: int
    prev_hard_hold: int
    prev_broken: int
    # context, from the set score before this game
    game_diff: int
    serving_for_set: int
    serving_to_stay: int
    games_in_set: int


def game_winner(points: Iterable[int]) -> int:
    """1 if the server won the game, 0 if the returner did.

    Standard scoring: a side wins on reaching four points with a margin of two,
    which is the game-win test at deuce and advantage too. Raises if `points`
    ends with the game undecided -- a completed game is the contract, and a
    silent wrong answer here would poison every target.
    """
    s = r = 0
    for p in points:
        if p == 1:
            s += 1
        elif p == 0:
            r += 1
        else:
            raise ValueError(f"a point is 1 or 0, not {p!r}")
        if s >= 4 and s - r >= 2:
            return 1
        if r >= 4 and r - s >= 2:
            return 0
    raise ValueError(f"the game is not over: server {s}, returner {r}")


class _Serve:
    """Running serve totals for one player, updated only after a row is emitted."""

    __slots__ = ("points", "won", "games", "holds", "prev_hold", "prev_lost")

    def __init__(self) -> None:
        self.points = 0
        self.won = 0
        self.games = 0
        self.holds = 0
        self.prev_hold = -1     # -1 until the player has served a game
        self.prev_lost = -1     # points the server dropped in that previous game

    def spw(self, prior: float) -> float:
        return (self.won + SPW_SHRINK_POINTS * prior) / (self.points + SPW_SHRINK_POINTS)

    def hold_rate(self) -> float:
        return ((self.holds + HOLD_SHRINK_GAMES * TOUR_HOLD_RATE)
                / (self.games + HOLD_SHRINK_GAMES))

    def fold(self, won: int, lost: int, hold: int) -> None:
        self.points += won + lost
        self.won += won
        self.games += 1
        self.holds += hold
        self.prev_hold = hold
        self.prev_lost = lost


def build_game_rows(
    match_id: str,
    games: Iterable[GamePlay],
    prior_for: Callable[[int, int], float],
) -> list[GameRow]:
    """Rows for every completed service game of one match, in order.

    `prior_for(server, returner)` returns P(server wins a point on serve), the
    pre-match prior; it is called twice per game, once for each player's own
    serve (server first, then the returner's), and may be memoised by the
    caller. Live it wraps `RatingsSnapshot.prior(...).p_serve_a`.
    """
    hist: dict[int, _Serve] = {}

    def serve_of(pid: int) -> _Serve:
        s = hist.get(pid)
        if s is None:
            s = hist[pid] = _Serve()
        return s

    rows: list[GameRow] = []
    for game_no, g in enumerate(games):
        won = sum(g.points)
        lost = len(g.points) - won
        hold = game_winner(g.points)
        prior = prior_for(g.server, g.returner)
        opp_prior = prior_for(g.returner, g.server)

        me = serve_of(g.server)
        opp = serve_of(g.returner)

        row = GameRow(
            match_id=match_id,
            set_no=g.set_no,
            game_no=game_no,
            server=g.server,
            returner=g.returner,
            hold=hold,
            prior_p_serve=prior,
            cum_serve_points=me.points,
            cum_serve_won=me.won,
            live_spw=me.spw(prior),
            cum_games=me.games,
            cum_holds=me.holds,
            live_hold_rate=me.hold_rate(),
            opp_cum_serve_points=opp.points,
            opp_cum_serve_won=opp.won,
            opp_live_spw=opp.spw(opp_prior),
            opp_cum_games=opp.games,
            opp_live_hold_rate=opp.hold_rate(),
            has_prev=int(me.prev_hold != -1),
            prev_hold=me.prev_hold,
            prev_easy_hold=int(me.prev_hold == 1 and me.prev_lost <= 1),
            prev_hard_hold=int(me.prev_hold == 1 and me.prev_lost >= 3),
            prev_broken=int(me.prev_hold == 0),
            game_diff=g.server_games - g.returner_games,
            serving_for_set=int(g.server_games >= 5 and g.server_games - g.returner_games >= 1),
            serving_to_stay=int(g.returner_games >= 5 and g.returner_games - g.server_games >= 1),
            games_in_set=g.server_games + g.returner_games,
        )
        rows.append(row)

        # Only now does this game exist for the next row.
        me.fold(won, lost, hold)

    return rows


def poison_future(games: list[GamePlay], keep: int) -> list[GamePlay]:
    """The leak test's tool: keep the first `keep` games, replace the rest with a
    different plausible game. Rebuilding must leave rows[:keep] untouched."""
    fake = GamePlay(server=999, returner=998, points=(1, 0, 1, 0, 1, 1),
                    set_no=9, server_games=0, returner_games=0)
    return list(games[:keep]) + [replace(fake) for _ in games[keep:]]
