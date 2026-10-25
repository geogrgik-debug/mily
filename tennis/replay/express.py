"""Joint pricing of two consecutive exact-game outcomes in a paper replay.

This is a probability core, not an instruction to place a bet. The second
game's point-win probability must be conditional on the first leg succeeding:
after that leg, its game winner and the next game's server/score are known.
Neither a product of two bookmaker implied probabilities nor a product of
unconditional model forecasts is the ticket's model probability.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from tennis.markov import exact_score_dist, p_hold_from

OUTCOMES = frozenset(exact_score_dist(0.5))


def exact_event_from(p: float, outcome: str,
                     score: tuple[int, int] = (0, 0)) -> float:
    """P(exact outcome | current game score), score from server/returner side.

    Score uses *points won* (0, 1, 2, 3, 4 for 0/15/30/40/advantage).
    The '40' outcome means deuce was reached and that side then won the game.
    Standard advantage scoring is assumed; no-ad games need a separate model.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f'unknown game outcome {outcome!r}')
    p = float(p)
    if not math.isfinite(p) or not 0 < p < 1:
        raise ValueError('point probability must be strictly between 0 and 1')
    a, b = score
    if not (isinstance(a, int) and isinstance(b, int) and 0 <= a <= 4
            and 0 <= b <= 4):
        raise ValueError(f'invalid game score {score!r}')
    side, suffix = outcome.split('_')
    loser_points = {'0': 0, '15': 1, '30': 2, '40': 3}[suffix]

    @lru_cache(None)
    def walk(x: int, y: int) -> float:
        if x >= 3 and y >= 3:
            if loser_points != 3:
                return 0.0
            hold = p_hold_from(p, x, y)
            return hold if side == 'hold' else 1 - hold
        if x >= 4 and x - y >= 2:
            return float(side == 'hold' and y == loser_points)
        if y >= 4 and y - x >= 2:
            return float(side == 'break' and x == loser_points)
        return p * walk(x + 1, y) + (1 - p) * walk(x, y + 1)

    return walk(a, b)


@dataclass(frozen=True)
class ExpressPrice:
    first_probability: float
    second_given_first: float
    joint_probability: float
    offered_odds: float
    break_even_probability: float
    expected_net_per_unit: float


def consecutive_exact_express(*, p_first_server: float,
                              p_second_server_given_first: float,
                              first_outcome: str, second_outcome: str,
                              first_odds: float, second_odds: float,
                              first_score: tuple[int, int] = (0, 0)) -> ExpressPrice:
    """Two legs of one match, with game two conditioned on game one's outcome.

    The caller must derive both serve-point probabilities from a model valid
    for this match segment and time. `p_second_server_given_first` is after
    the *specified* first outcome, not the unconditional next-game estimate.
    """
    odds = float(first_odds) * float(second_odds)
    if not math.isfinite(odds) or first_odds <= 1 or second_odds <= 1:
        raise ValueError('both decimal odds must be finite and > 1')
    first = exact_event_from(p_first_server, first_outcome, first_score)
    second = exact_event_from(p_second_server_given_first, second_outcome)
    joint = first * second
    return ExpressPrice(first, second, joint, odds, 1 / odds,
                        joint * odds - 1)
