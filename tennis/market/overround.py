"""Turn quoted odds into probabilities, and be explicit about how.

A bookmaker's board sums to more than one. The excess -- the overround -- is
the margin, and removing it is a modelling choice, not arithmetic. Two methods
are offered because they disagree exactly where this project cares:

* Proportional normalisation divides every implied probability by the book sum.
  It assumes the margin is spread evenly in proportional terms, which is the
  standard assumption and is wrong in a known direction: books load more margin
  onto longshots (the favourite-longshot bias), so proportional normalisation
  overstates longshot probabilities.
* Shin's method models the margin as protection against insiders and recovers a
  single parameter z, the assumed insider fraction. It corrects the longshot
  bias in the right direction and reduces to proportional as z goes to zero.

On a game market these differ most on the rarest outcomes -- a game won to love
from the returner's side -- which is precisely where an eight-outcome exact
score market puts its longest prices. So which method is used changes the
fitted point probability, and the choice belongs to the caller.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

__all__ = ["book_sum", "overround", "normalize_proportional", "shin"]


def _clean(odds: Iterable[float]) -> list[float]:
    out = []
    for o in odds:
        o = float(o)
        if not math.isfinite(o) or o <= 1.0:
            # An odd of 1.0 or less implies a probability of one or more. The
            # feed does emit 0.0 for a suspended outcome, so this is a normal
            # input, not a corrupt one -- but it cannot be normalised, and a
            # silent 1/0 would poison every downstream probability.
            raise ValueError(f"odds must be finite and > 1, got {o!r}")
        out.append(o)
    if not out:
        raise ValueError("no odds given")
    return out


def book_sum(odds: Sequence[float]) -> float:
    """Sum of implied probabilities. 1.0 is a fair book; above is the margin."""
    return sum(1.0 / o for o in _clean(odds))


def overround(odds: Sequence[float]) -> float:
    """Margin as a fraction: 0.099 for the 9.9% seen on a two-way game market.

    This is `book_sum - 1`, the "excess overround", which is what makes books
    of different outcome counts comparable. Quoting the raw book sum instead
    (1.099) is the common way to accidentally compare a two-way market against
    an eight-way one and conclude the wrong thing.
    """
    return book_sum(odds) - 1.0


def normalize_proportional(odds: Sequence[float]) -> list[float]:
    """Implied probabilities scaled to sum to one."""
    clean = _clean(odds)
    total = sum(1.0 / o for o in clean)
    return [(1.0 / o) / total for o in clean]


def shin(odds: Sequence[float], *, tol: float = 1e-12,
         max_iter: int = 200) -> tuple[list[float], float]:
    """Shin-corrected probabilities and the fitted insider fraction z.

    Solves for z such that the recovered probabilities sum to one, where

        p_i = ( sqrt( z^2 + 4(1-z) * pi_i^2 / B ) - z ) / ( 2(1-z) )

    with pi_i = 1/odds_i and B the book sum. The bracket is monotone decreasing
    in z, so a bisection on [0, 1) is exact and needs no starting guess.

    Returns (probabilities, z). A fair book (B <= 1) is returned normalised with
    z = 0 rather than raising: an arbitrage or a stale quote is a thing that
    happens on a live feed, and it is the caller's business, not an error here.
    """
    clean = _clean(odds)
    pis = [1.0 / o for o in clean]
    b = sum(pis)
    if b <= 1.0 or len(clean) < 2:
        return normalize_proportional(clean), 0.0

    def total(z: float) -> float:
        return sum((math.sqrt(z * z + 4.0 * (1.0 - z) * pi * pi / b) - z)
                   / (2.0 * (1.0 - z)) for pi in pis)

    lo, hi = 0.0, 1.0 - 1e-9
    if total(lo) <= 1.0:                    # already fair at z = 0
        return normalize_proportional(clean), 0.0
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if total(mid) > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    z = 0.5 * (lo + hi)
    probs = [(math.sqrt(z * z + 4.0 * (1.0 - z) * pi * pi / b) - z)
             / (2.0 * (1.0 - z)) for pi in pis]
    s = sum(probs)
    return [p / s for p in probs], z          # renormalise away bisection error
