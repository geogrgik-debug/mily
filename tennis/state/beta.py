"""Live serve state, version 1: one Beta posterior per server, updated point by point.

Track B, step 4 (`HANDOFF.md`). For each player the state holds a belief about
P(he wins a point on his own serve, today, against this opponent): a Beta
centred on the pre-match prior (`tennis.ratings`) with the weight of `n0`
phantom points, and every point he serves adds one real point to it. From the
belief comes the probability that he holds -- `p_hold_next` for a game about to
start, through `markov.p_game`, and `p_hold_now` for a game under way, through
`markov.p_hold_from`.

**Two quantities, not four.** The audit's state vector is (S_A, R_A, S_B, R_B),
serve and return of each player (section 9.4), but point outcomes only ever see
two contrasts -- A's serve against B's return, B's serve against A's -- so a
state driven by points tracks exactly those two. Whether they are linked, i.e.
whether A serving above his prior says anything about B's serve, is measured
by `tennis.eval`; a four-way state is version 2, the Kalman filter.

**The hold probability is averaged over the posterior**, not taken at its mean
(audit, section 9.3). Given p the points of a game are iid, so the predictive
probability of a hold is the Markov game integrated against the belief; where
servers live the game is concave in p, and the game at the mean overstates it.
The integral runs on a fixed grid over [0, 1]; `p_hold_plugin` is the game at
the mean, kept for comparison.

**Leak safety is structural.** A state is a pure function of the points folded
into it, and `after_point` returns a new state instead of changing one, so the
state after point k cannot see point k+1. `tests/test_beta.py` still poisons
every later point and demands the earlier states unchanged.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Dict, Tuple

import numpy as np

from tennis.markov import p_game, p_hold_from

# The prior's weight in phantom serve points: the n0 with the lowest hold log
# loss on the training years of `python -m tennis.eval live-state` -- Grand Slam
# men 2012-2018, tennis_pointbypoint ATP and Challenger 2011-2015. The curve is
# flat near the minimum (+-10 % costs under 1e-4); the likelihood of single
# serve points puts it 10-20 % lower. Numbers and method: README.md.
N0 = {"slam": 140.0, "tour": 100.0, "chall": 90.0}
DEFAULT_N0 = N0["tour"]

# Midpoint grid for the posterior average. A posterior this code will meet has
# sd >= 0.01 (n0 in the hundreds plus a match of serve points), against a grid
# step of 0.001: the rule is exact far below anything a log loss can see. One
# narrower than three steps is priced at its mean, which is then exact to
# O(sd^2) < 1e-4.
_NODES = 1000
_X = (np.arange(_NODES) + 0.5) / _NODES
_LOG_X = np.log(_X)
_LOG_1MX = np.log1p(-_X)
_STEP = 1.0 / _NODES
_CHUNK = 4096                    # rows per block: 4096 x 1000 doubles = 32 MB

_TABLES: Dict[Tuple[int, int], np.ndarray] = {}


def _game_at(p: float, a: int, b: int) -> float:
    return p_game(p) if (a, b) == (0, 0) else p_hold_from(p, a, b)


def _table(a: int, b: int) -> np.ndarray:
    """P(hold from a-b) at every grid node, computed once per score."""
    t = _TABLES.get((a, b))
    if t is None:
        t = np.array([_game_at(float(x), a, b) for x in _X])
        _TABLES[(a, b)] = t
    return t


def hold_prob(alpha, beta, a: int = 0, b: int = 0):
    """P(server holds from game score a-b), averaged over Beta(alpha, beta).

    Vectorised over `alpha` and `beta` (same shape); a scalar in, a float out.
    From 0-0 this is `markov.p_game` averaged, from any other score
    `markov.p_hold_from`.
    """
    al = np.asarray(alpha, dtype=float)
    be = np.asarray(beta, dtype=float)
    if al.shape != be.shape:
        raise ValueError(f"alpha and beta differ in shape: {al.shape} vs {be.shape}")
    if not (np.all(np.isfinite(al)) and np.all(np.isfinite(be))
            and np.all(al > 0) and np.all(be > 0)):
        raise ValueError("alpha and beta must be positive and finite")
    a, b = int(a), int(b)
    f = _table(a, b)

    fa, fb = al.ravel(), be.ravel()
    out = np.empty(fa.size)
    for s in range(0, fa.size, _CHUNK):
        logw = (fa[s:s + _CHUNK, None] - 1.0) * _LOG_X + (fb[s:s + _CHUNK, None] - 1.0) * _LOG_1MX
        logw -= logw.max(axis=1, keepdims=True)
        w = np.exp(logw)
        out[s:s + _CHUNK] = (w @ f) / w.sum(axis=1)

    n = fa + fb
    mean = fa / n
    narrow = np.flatnonzero(np.sqrt(mean * (1.0 - mean) / (n + 1.0)) < 3 * _STEP)
    for i in narrow:
        out[i] = _game_at(float(mean[i]), a, b)

    res = out.reshape(al.shape)
    return float(res) if res.ndim == 0 else res


@dataclass(frozen=True)
class ServeBelief:
    """Beta(alpha, beta) on one player's P(win a point on his own serve) today."""

    alpha: float
    beta: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.alpha) and math.isfinite(self.beta)
                and self.alpha > 0 and self.beta > 0):
            raise ValueError(f"a Beta needs positive finite counts, got ({self.alpha}, {self.beta})")

    @classmethod
    def from_prior(cls, p_prior: float, n0: float) -> "ServeBelief":
        """The belief before the match: `n0` phantom points at the prior's rate."""
        p, n0 = float(p_prior), float(n0)
        if not 0.0 < p < 1.0:
            raise ValueError(f"the prior must lie strictly between 0 and 1, got {p_prior!r}")
        if not (math.isfinite(n0) and n0 > 0):
            raise ValueError(f"the prior strength must be positive and finite, got {n0!r}")
        return cls(n0 * p, n0 * (1.0 - p))

    def observe(self, server_won) -> "ServeBelief":
        """The belief after one more point on this player's serve."""
        if server_won not in (0, 1):
            raise ValueError(f"a point is won (1) or lost (0), not {server_won!r}")
        won = int(server_won == 1)
        return ServeBelief(self.alpha + won, self.beta + (1 - won))

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def sd(self) -> float:
        m = self.mean
        return math.sqrt(m * (1.0 - m) / (self.alpha + self.beta + 1.0))

    def p_hold(self, a: int = 0, b: int = 0) -> float:
        """P(this server holds from game score a-b), averaged over the belief."""
        return hold_prob(self.alpha, self.beta, a, b)

    def p_hold_plugin(self, a: int = 0, b: int = 0) -> float:
        """The same at the posterior mean -- for comparison, not for pricing."""
        return _game_at(self.mean, int(a), int(b))


@dataclass(frozen=True)
class MatchState:
    """Both servers' beliefs in one match. Every update returns a new state."""

    player_a: int
    player_b: int
    belief_a: ServeBelief
    belief_b: ServeBelief

    @classmethod
    def start(cls, player_a: int, player_b: int, p_serve_a: float, p_serve_b: float,
              n0: float = DEFAULT_N0) -> "MatchState":
        """Before the first point: each player's prior, `p_serve_*` from
        `RatingsSnapshot.prior(a, b, ...)`, with strength `n0` -- `N0[level]`
        when the level is known, the tour's otherwise."""
        if player_a == player_b:
            raise ValueError(f"a match needs two players, got {player_a!r} twice")
        return cls(player_a, player_b,
                   ServeBelief.from_prior(p_serve_a, n0), ServeBelief.from_prior(p_serve_b, n0))

    def belief(self, player: int) -> ServeBelief:
        if player == self.player_a:
            return self.belief_a
        if player == self.player_b:
            return self.belief_b
        raise ValueError(f"player {player!r} is not in this match "
                         f"({self.player_a} v {self.player_b})")

    def after_point(self, server: int, server_won) -> "MatchState":
        """The state after a point `server` served; only his belief moves."""
        moved = self.belief(server).observe(server_won)
        if server == self.player_a:
            return replace(self, belief_a=moved)
        return replace(self, belief_b=moved)

    def p_hold_next(self, server: int) -> float:
        """P(`server` holds the next game, not yet started) -- `markov.p_game`."""
        return self.belief(server).p_hold()

    def p_hold_now(self, server: int, a: int, b: int) -> float:
        """P(`server` holds the game under way at a-b) -- `markov.p_hold_from`.
        The points of this game already played are in the belief."""
        return self.belief(server).p_hold(a, b)
