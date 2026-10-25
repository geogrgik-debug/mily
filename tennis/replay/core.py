"""Leak-safe score replay and next-game forecasts, independent of the feed.

Only fully observed point transitions move the Beta state. An unknown jump
invalidates that match for the rest of the run; it is never filled in.
Quotes for game N+1 arrive while game N is in progress. At N's 0-0 the
calibrated probability of N's two outcomes weights the two possible contexts
of N+1. An in-progress N needs a separately validated conditional calibration
and is deliberately skipped by this v1 replay.
"""
from __future__ import annotations

from dataclasses import dataclass

from tennis.features.context import GameContext
from tennis.model.hold import HoldParams, p_hold, start_state

POINTS = {0: 0, 15: 1, 30: 2, 40: 3, 50: 4}


def next_point(score: tuple[int, int], winner: int) -> tuple[int, int] | None:
    """The unique next displayed tennis score, or None when the game ends."""
    if winner not in (1, 2) or any(v not in POINTS for v in score):
        raise ValueError((score, winner))
    a, b = score if winner == 1 else score[::-1]
    if a == 50:
        return None
    if b == 50:
        nxt = (40, 40)
    elif a == b == 40:
        nxt = (50, 40)
    elif a == 40:
        return None
    else:
        nxt = ({0: 15, 15: 30, 30: 40}[a], b)
    return nxt if winner == 1 else nxt[::-1]


def set_finished(score: tuple[int, int]) -> bool:
    a, b = score
    return (max(a, b) >= 6 and abs(a - b) >= 2) or max(a, b) == 7


@dataclass(frozen=True)
class Score:
    """Provider-independent tennis board, always in home/away order."""

    sets: tuple[tuple[int, int], ...]
    points: tuple[int, int]
    serving_side: int  # 1 home, 2 away; 0 means the feed has not said

    @property
    def set_no(self) -> int:
        return next((i for i, s in enumerate(self.sets, 1) if not set_finished(s)),
                    len(self.sets))

    @property
    def games(self) -> tuple[int, int]:
        return self.sets[self.set_no - 1]

    @property
    def game_no(self) -> int:
        return sum(self.games) + 1


@dataclass(frozen=True)
class Forecast:
    match_id: int
    set_no: int
    game_no: int
    ts_received_ns: int
    server: int
    p_home: float

    @property
    def p_hold(self) -> float:
        return self.p_home if self.server == 1 else 1 - self.p_home


class MatchReplay:
    """One match, one ordered receive-time stream. No feed or file I/O."""

    def __init__(self, match_id: int, home: int, away: int, p_serve_home: float,
                 p_serve_away: float, *, level: str, surface: str, best_of: int,
                 params: HoldParams):
        self.match_id = match_id
        self.players = (home, away)
        self.level, self.surface, self.best_of = level, surface, best_of
        self.params = params
        self.state = start_state(params, level, home, away,
                                 p_serve_home, p_serve_away)
        self.score: Score | None = None
        self.server: int = 0
        self.served = {1: 0, 2: 0}
        self.was_broken = {1: False, 2: False}
        self.last_game: tuple[int, int] | None = None  # (winner, server)
        self.forecasts: dict[tuple[int, int], Forecast] = {}
        self.outcomes: dict[tuple[int, int], tuple[int, int, int]] = {}
        self.points_seen = 0
        self.invalid_reason: str | None = None

    def _invalidate(self, reason: str) -> None:
        self.invalid_reason = reason

    def _context(self, score: Score, server: int, *, just_broke: bool = False) -> GameContext:
        h, a = score.games
        home_sets = sum(s[0] > s[1] for s in score.sets[:score.set_no - 1])
        away_sets = sum(s[1] > s[0] for s in score.sets[:score.set_no - 1])
        return GameContext(
            level=self.level, surface=self.surface, best_of=self.best_of,
            set_no=score.set_no,
            server_games=h if server == 1 else a,
            returner_games=a if server == 1 else h,
            server_sets=home_sets if server == 1 else away_sets,
            returner_sets=away_sets if server == 1 else home_sets,
            served_before=self.served[server], just_broke=just_broke,
            was_broken=self.was_broken[server],
        )

    def _forecast(self, score: Score, t: int, server: int) -> Forecast:
        just_broke = bool(self.last_game and self.last_game[0] == server
                          and self.last_game[1] != server)
        hold = p_hold(self.state, self.players[server - 1],
                      self._context(score, server, just_broke=just_broke), self.params)
        return Forecast(self.match_id, score.set_no, score.game_no, t, server,
                        hold if server == 1 else 1 - hold)

    def observe(self, score: Score, t: int) -> None:
        """Fold one snapshot. Duplicate score frames do not update the state."""
        if self.invalid_reason:
            return
        if not score.sets or score.points[0] not in POINTS or score.points[1] not in POINTS:
            self._invalidate('invalid_score')
            return
        if self.score is None:
            if score.set_no != 1 or score.games != (0, 0) or score.points != (0, 0):
                self._invalidate('match_started_before_capture')
                return
            self.score = score
            self.server = score.serving_side
            if self.server in (1, 2):
                self.forecasts[(1, 1)] = self._forecast(score, t, self.server)
            return
        old = self.score
        if len(score.sets) < len(old.sets):
            self._invalidate('score_gap')
            return
        if old.sets == score.sets and old.points == score.points:
            self.score = score
            if self.server == 0 and score.serving_side in (1, 2):
                self.server = score.serving_side
                self.forecasts.setdefault((score.set_no, score.game_no),
                                          self._forecast(score, t, self.server))
            return
        changes = [(i, a, b) for i, (a, b) in enumerate(
            zip(old.sets + ((0, 0),) * max(0, len(score.sets) - len(old.sets)),
                score.sets), 1) if a != b]
        if self.server not in (1, 2):
            self._invalidate('server_unknown')
            return
        if not changes:
            winners = [side for side in (1, 2)
                       if next_point(old.points, side) == score.points]
            if len(winners) != 1:
                self._invalidate('point_gap')
                return
            self.state = self.state.after_point(self.players[self.server - 1],
                                                winners[0] == self.server)
            self.points_seen += 1
        elif len(changes) == 1 and score.points == (0, 0):
            set_no, before, after = changes[0]
            delta = (after[0] - before[0], after[1] - before[1])
            winner = 1 if delta == (1, 0) else 2 if delta == (0, 1) else 0
            if (winner == 0 or set_no != old.set_no or
                    next_point(old.points, winner) is not None):
                self._invalidate('game_gap')
                return
            self.state = self.state.after_point(self.players[self.server - 1],
                                                winner == self.server)
            self.points_seen += 1
            self.outcomes[(set_no, sum(before) + 1)] = (winner, self.server, t)
            self.served[self.server] += 1
            self.was_broken[self.server] = winner != self.server
            self.last_game = (winner, self.server)
            next_server = 3 - self.server
            if score.serving_side in (1, 2) and score.serving_side != next_server:
                self._invalidate('server_rotation_mismatch')
                return
            self.server = next_server
        else:
            self._invalidate('score_gap')
            return
        self.score = score
        if changes and not set_finished(score.games):
            key = score.set_no, score.game_no
            self.forecasts[key] = self._forecast(score, t, self.server)

    def project_next(self, t: int) -> tuple[float | None, str]:
        """P(home wins next game) at the current board; or a skip reason."""
        if self.invalid_reason:
            return None, self.invalid_reason
        score = self.score
        if score is None or self.server not in (1, 2):
            return None, 'score_or_server_unknown'
        if score.points != (0, 0):
            return None, 'in_game_weight_uncalibrated'
        if score.game_no >= 12:
            return None, 'tiebreak_or_set_transition'
        current = self.forecasts.get((score.set_no, score.game_no))
        if current is None:
            return None, 'current_game_forecast_missing'
        future_server = 3 - self.server
        h, a = score.games
        if any(set_finished((h + (winner == 1), a + (winner == 2)))
               for winner in (1, 2)):
            return None, 'set_transition_ambiguous'
        p_current_hold = current.p_hold
        value = 0.0
        for winner in (self.server, future_server):
            next_score = Score(score.sets[:score.set_no - 1] +
                               ((h + (winner == 1), a + (winner == 2)),) +
                               score.sets[score.set_no:], (0, 0), future_server)
            context = self._context(next_score, future_server,
                                    just_broke=winner == future_server)
            hold = p_hold(self.state, self.players[future_server - 1], context,
                          self.params)
            p_home = hold if future_server == 1 else 1 - hold
            weight = p_current_hold if winner == self.server else 1 - p_current_hold
            value += weight * p_home
        return value, ''
