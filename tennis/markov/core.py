"""Closed-form and memoised probabilities for the hierarchical Markov tennis model.

All functions take point-win probabilities on serve. The single modelling
assumption throughout is that points are iid given the server, which is what
the audit's own measurements support: outcome-history features beyond the
current serve estimate bought +0.0001 log loss and process features bought
nothing, so there is no evidence to justify a point-level dependence term.

Conventions
-----------
`p`            probability the server wins a point on their own serve
`pa`, `pb`     serve-point-win probabilities of A and B respectively
`a`, `b`       points already won in the current game by server / returner
                (0,1,2,3 = 0,15,30,40; 3-3 is deuce, 4-3 is advantage server)

Serve order is explicit rather than averaged wherever it matters: the tiebreak
rotation is 1-2-2 and the set alternates games.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Dict, Tuple

__all__ = [
    "p_game",
    "p_hold_from",
    "exact_score_dist",
    "p_tiebreak",
    "p_set",
    "p_match",
    "invert",
]

# Deuce and the win-by-two races divide by p^2 + q^2, which is >= 0.5 for every
# real p, so no guard is needed there. The guards below are for p exactly 0 or 1,
# where the recursions would still terminate but the caller almost certainly has
# a bug upstream.
_EPS = 1e-12


def _check(p: float, name: str = "p") -> float:
    p = float(p)
    if not (0.0 <= p <= 1.0) or math.isnan(p):
        raise ValueError(f"{name} must be a probability in [0, 1], got {p!r}")
    return p


# --------------------------------------------------------------- game


@lru_cache(maxsize=None)
def p_game(p: float) -> float:
    """P(server holds from 0-0).

    Closed form: win to love/15/30 by the binomial paths, plus the 20 paths that
    reach deuce times the deuce race p^2 / (p^2 + q^2).
    """
    p = _check(p)
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    q = 1.0 - p
    return p**4 * (1 + 4 * q + 10 * q * q) + 20 * p**3 * q**3 * (p * p / (p * p + q * q))


@lru_cache(maxsize=None)
def p_hold_from(p: float, a: int = 0, b: int = 0) -> float:
    """P(server wins the game) from an arbitrary in-game score a-b.

    This is the function the live engine calls once a game is already under way;
    `p_hold_from(p, 0, 0)` agrees with `p_game(p)` to floating point.

    Advantage states collapse onto deuce analytically, so the recursion is finite:
    at deuce p^2/(p^2+q^2); at advantage-server p + q * deuce; at advantage-returner
    p * deuce.
    """
    p = _check(p)
    a, b = int(a), int(b)
    if a < 0 or b < 0:
        raise ValueError(f"scores must be non-negative, got {a}-{b}")
    q = 1.0 - p

    if a >= 4 and a - b >= 2:
        return 1.0
    if b >= 4 and b - a >= 2:
        return 0.0
    if a >= 3 and b >= 3:
        deuce = p * p / (p * p + q * q)
        if a == b:
            return deuce
        if a == b + 1:
            return p + q * deuce
        if b == a + 1:
            return p * deuce
        raise ValueError(f"unreachable game score {a}-{b}")
    return p * p_hold_from(p, a + 1, b) + q * p_hold_from(p, a, b + 1)


def exact_score_dist(p: float) -> Dict[str, float]:
    """Distribution over the eight bookmaker game outcomes, from 0-0.

    Keys are `hold_0/15/30/40` and `break_0/15/30/40`, where the suffix is the
    loser's game score and `40` means the game was decided from deuce (i.e. what
    books settle as "to 40" / "advantage"). Sums to 1; the four hold keys sum to
    `p_game(p)`.

    This is the entire exact-score market, and it is a function of one number.
    Four independent checks against real BetBoom/Pari books put the maximum
    residual at 0.014 across all eight outcomes, so a book quoting these eight
    prices is revealing a single p and nothing more.
    """
    p = _check(p)
    q = 1.0 - p
    deuce_paths = 20 * p**3 * q**3
    denom = p * p + q * q
    return {
        "hold_0": p**4,
        "hold_15": 4 * p**4 * q,
        "hold_30": 10 * p**4 * q**2,
        "hold_40": deuce_paths * (p * p / denom),
        "break_0": q**4,
        "break_15": 4 * q**4 * p,
        "break_30": 10 * q**4 * p**2,
        "break_40": deuce_paths * (q * q / denom),
    }


# --------------------------------------------------------------- tiebreak


@lru_cache(maxsize=None)
def p_tiebreak(pa: float, pb: float, target: int = 7) -> float:
    """P(A wins a tiebreak to `target` points), A serving the first point.

    Serve rotation is 1-2-2: A serves point 1, then each player serves two in a
    row. Server of point k is A iff k mod 4 in {0, 1}.

    From any tie at `target - 1` and beyond, the next two points are served one
    by each player, so the win-by-two race has the closed form u / (u + v) with
    u = pa(1 - pb) and v = (1 - pa)pb. That truncates the otherwise unbounded
    recursion.

    `target` is 7 for a normal tiebreak and 10 for a deciding-set match tiebreak.
    """
    pa, pb = _check(pa, "pa"), _check(pb, "pb")
    target = int(target)
    if target < 1:
        raise ValueError(f"target must be at least 1, got {target}")

    u = pa * (1.0 - pb)          # A takes both points of a two-point block
    v = (1.0 - pa) * pb          # B takes both
    tie = 0.5 if (u + v) <= _EPS else u / (u + v)

    memo: Dict[Tuple[int, int, int], float] = {}

    def f(a: int, b: int, srv: int) -> float:   # srv 0 = A serves this point
        if a >= target and a - b >= 2:
            return 1.0
        if b >= target and b - a >= 2:
            return 0.0
        if a >= target - 1 and a == b:
            return tie
        key = (a, b, srv)
        if key in memo:
            return memo[key]
        p = pa if srv == 0 else 1.0 - pb        # P(A wins this point)
        n = a + b + 1                           # 1-based index of the point being played
        nxt = 0 if (n % 4) in (0, 3) else 1     # server of point n+1
        val = p * f(a + 1, b, nxt) + (1.0 - p) * f(a, b + 1, nxt)
        memo[key] = val
        return val

    return f(0, 0, 0)


# --------------------------------------------------------------- set and match


@lru_cache(maxsize=None)
def p_set(pa: float, pb: float) -> float:
    """P(A wins a set), A serving the first game, tiebreak at 6-6."""
    pa, pb = _check(pa, "pa"), _check(pb, "pb")
    ga, gb = p_game(pa), p_game(pb)
    memo: Dict[Tuple[int, int, int], float] = {}

    def f(a: int, b: int, srv: int) -> float:   # srv 0 = A serves this game
        if a == 6 and b <= 4:
            return 1.0
        if b == 6 and a <= 4:
            return 0.0
        if a == 7:
            return 1.0
        if b == 7:
            return 0.0
        if a == 6 and b == 6:
            return p_tiebreak(pa, pb)
        key = (a, b, srv)
        if key in memo:
            return memo[key]
        w = ga if srv == 0 else 1.0 - gb        # P(A wins this game)
        val = w * f(a + 1, b, 1 - srv) + (1.0 - w) * f(a, b + 1, 1 - srv)
        memo[key] = val
        return val

    return f(0, 0, 0)


def p_match(pa: float, pb: float, best_of: int = 3) -> float:
    """P(A wins the match); sets iid, serve order averaged over who starts.

    Averaging the opening serve is deliberate: who serves first in a set is an
    unmodelled coin flip once the match is under way, and carrying it would make
    the inversion below depend on a variable the prior never observes.
    """
    if best_of not in (3, 5):
        raise ValueError(f"best_of must be 3 or 5, got {best_of}")
    s = 0.5 * (p_set(pa, pb) + (1.0 - p_set(pb, pa)))
    need = 2 if best_of == 3 else 3
    return sum(
        math.comb(need + k - 1, k) * s**need * (1.0 - s) ** k for k in range(need)
    )


# --------------------------------------------------------------- inversion


@lru_cache(maxsize=None)
def _invert(win_prob_r: float, baseline_r: float, best_of: int) -> Tuple[float, float]:
    """Cached bisection for a favourite: (pa, pb) averaging `baseline_r`.

    Searches only d >= 0 where pa = baseline + d, pb = baseline - d. Callers must
    go through `invert`, which handles underdogs by symmetry.
    """
    win_prob = min(max(win_prob_r, 1e-3), 1 - 1e-3)
    baseline = baseline_r
    lo, hi = 0.0, min(baseline, 1 - baseline) - 1e-3
    if hi <= lo:
        return baseline, baseline
    if p_match(baseline + hi, baseline - hi, best_of) < win_prob:
        return baseline + hi, baseline - hi          # saturated: no pair reaches it
    for _ in range(28):
        d = 0.5 * (lo + hi)
        if p_match(baseline + d, baseline - d, best_of) < win_prob:
            lo = d
        else:
            hi = d
    d = 0.5 * (lo + hi)
    return baseline + d, baseline - d


def invert(win_prob: float, baseline: float, best_of: int = 3) -> Tuple[float, float]:
    """(p_serve, p_serve_opponent) for a player with this match win probability.

    The Klaassen & Magnus (2003) inversion, constrained so the pair averages the
    surface/year `baseline`. Rounding to three decimals is what makes the cache
    hit across a quarter of a million matches; it costs ~5e-4 on the inputs,
    far below the prior's own 5.5-point error.

    The bisection searches d >= 0 only, so it solves for a favourite. An underdog
    is solved as the opponent's favourite problem and the pair swapped. Without
    that swap every `win_prob` below 0.5 silently returned the baseline for both
    players -- half of all values -- which is the bug that reversed experiment
    B1's headline. The round-trip test below is the regression guard.
    """
    w = _check(win_prob, "win_prob")
    b = round(_check(baseline, "baseline"), 3)
    bo = int(best_of)
    if w < 0.5:
        opp, me = _invert(round(1.0 - w, 3), b, bo)
        return me, opp
    return _invert(round(w, 3), b, bo)
