"""Reading a bookmaker's board: what a price refers to, and what it implies.

Two jobs, kept apart on purpose.

`names` turns the provider's display strings into references. BetBoom addresses
a game market entirely inside `market_name` -- "2-й сет 6-й гейм: Точный счёт",
with `period_name` empty -- so without parsing it there is no way to say which
game a price belongs to, and every downstream join is impossible.

`overround` and `fit` turn prices into probabilities. A quoted book sums to
more than one; how the excess is removed is a modelling choice with real
consequences, so the choices are named and separate rather than folded into a
single "implied probability" helper.
"""
from tennis.market.names import (
    MarketRef,
    OUTCOME_POINTS,
    parse_exact_score_outcome,
    parse_market,
    parse_server_score_outcome,
)
from tennis.market.overround import (
    book_sum,
    normalize_proportional,
    overround,
    shin,
)
from tennis.market.fit import fit_point_prob, ExactScoreFit

__all__ = [
    "MarketRef",
    "OUTCOME_POINTS",
    "parse_market",
    "parse_exact_score_outcome",
    "parse_server_score_outcome",
    "book_sum",
    "overround",
    "normalize_proportional",
    "shin",
    "fit_point_prob",
    "ExactScoreFit",
]
