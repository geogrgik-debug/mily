"""Recover the one number a game market is really quoting.

The audit's central structural claim is that every outcome of a service game
is a function of a single point-win probability p. If that holds, a book
quoting eight exact-score prices is revealing one number and nothing more, and
the residual of the fit measures how far the book departs from the iid Markov
model -- or how far our reading of its margin does.

It was checked four times against screenshots, with a maximum residual of
0.014 across all eight outcomes. This module is what checks it continuously
against a live feed instead.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from tennis.markov import exact_score_dist, p_game

__all__ = ["ExactScoreFit", "fit_point_prob"]

_KEYS = ("hold_0", "hold_15", "hold_30", "hold_40",
         "break_0", "break_15", "break_30", "break_40")


@dataclass(frozen=True)
class ExactScoreFit:
    """The single p behind an eight-outcome game market, and how well it fits.

    `max_residual` is the largest absolute difference between a book
    probability and the model's, over the eight outcomes. It is the number to
    look at: an RMSE hides a single badly-priced longshot, which is exactly the
    outcome an insider would take.
    """

    p: float
    hold: float
    rmse: float
    max_residual: float
    book: dict
    model: dict

    @property
    def worst_outcome(self) -> str:
        return max(_KEYS, key=lambda k: abs(self.book[k] - self.model[k]))


def fit_point_prob(book: dict, *, lo: float = 0.01, hi: float = 0.99,
                   iters: int = 60) -> ExactScoreFit:
    """Least-squares fit of p to a normalised eight-outcome game market.

    `book` maps the eight keys of `exact_score_dist` to probabilities that
    already have the margin removed; it does not have to sum to exactly one.

    Golden-section search rather than a derivative method: the objective is
    smooth but the whole domain is [0, 1], evaluating it is cheap, and a
    bracketing search cannot run away -- which matters because this runs
    unattended over a live feed.

    Note the symmetry trap. Three outcomes from one side determine p only up to
    p <-> 1-p, because hold_30/hold_15 = 2.5(1-p) mirrors break_30/break_15 =
    2.5p. With all eight present the ambiguity is gone, so this fits over the
    full range rather than constraining p >= 0.5 -- the constraint is only
    needed when the market is one-sided.
    """
    missing = [k for k in _KEYS if k not in book]
    if missing:
        raise ValueError(f"book is missing outcomes: {missing}")

    def loss(p: float) -> float:
        model = exact_score_dist(p)
        return sum((book[k] - model[k]) ** 2 for k in _KEYS)

    phi = 0.5 * (math.sqrt(5.0) - 1.0)
    a, b = lo, hi
    c, d = b - phi * (b - a), a + phi * (b - a)
    fc, fd = loss(c), loss(d)
    for _ in range(iters):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - phi * (b - a)
            fc = loss(c)
        else:
            a, c, fc = c, d, fd
            d = a + phi * (b - a)
            fd = loss(d)
    p = 0.5 * (a + b)
    model = exact_score_dist(p)
    diffs = [abs(book[k] - model[k]) for k in _KEYS]
    return ExactScoreFit(
        p=p,
        hold=p_game(p),
        rmse=math.sqrt(sum(d * d for d in diffs) / len(diffs)),
        max_residual=max(diffs),
        book={k: book[k] for k in _KEYS},
        model=model,
    )
