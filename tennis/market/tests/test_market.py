"""Tests for reading a bookmaker's board.

The strings and shapes asserted here are not invented: they were extracted
from captured live logs (170 distinct market names, 90 mentioning a game).
Where a test pins a number, the number was measured, and the docstring says on
what.
"""
import math

import pytest

from tennis.markov import exact_score_dist, p_game
from tennis.market import (
    ExactScoreFit,
    MarketRef,
    book_sum,
    fit_point_prob,
    normalize_proportional,
    overround,
    parse_exact_score_outcome,
    parse_market,
    parse_server_score_outcome,
    shin,
)
from tennis.market.measure import AWAY, HOME, GameContext, _to_model_keys


# --------------------------------------------------------------- names


@pytest.mark.parametrize("name,scope,set_no,game_no,kind", [
    ("2-й сет 6-й гейм: Точный счёт", "game", 2, 6, "Точный счёт"),
    ("3-й сет 11-й гейм: Исход", "game", 3, 11, "Исход"),
    ("1-й сет 2-й гейм: Точный счёт подающего или брейк", "game", 1, 2,
     "Точный счёт подающего или брейк"),
    ("1-й сет 1-й гейм: 4-е очко", "game", 1, 1, "4-е очко"),
    ("1-й сет: Гонка до 2 геймов", "set", 1, None, "Гонка до 2 геймов"),
    ("2-й сет: Точный счёт после 6 геймов", "set", 2, None,
     "Точный счёт после 6 геймов"),
    ("Фора по геймам", "match", None, None, "Фора по геймам"),
    ("Исход", "match", None, None, "Исход"),
])
def test_parse_market_shapes_seen_live(name, scope, set_no, game_no, kind):
    ref = parse_market(name)
    assert (ref.scope, ref.set_no, ref.game_no, ref.kind) == (scope, set_no, game_no, kind)
    assert ref.is_game == (scope == "game")


def test_a_set_market_is_not_mistaken_for_a_game_market():
    """'1-й сет: Исход 5 и 6 геймов' mentions games but is not one."""
    ref = parse_market("1-й сет: Исход 5 и 6 геймов")
    assert ref.scope == "set" and ref.game_no is None


def test_unknown_market_never_raises():
    """A recorder that died on a new market name would lose the capture."""
    ref = parse_market("что-то совершенно новое")
    assert ref.scope == "match" and ref.kind == "что-то совершенно новое"
    assert parse_market("").scope == "match"


def test_market_ref_key_groups_prices_of_one_market():
    a = parse_market("2-й сет 6-й гейм: Исход")
    b = parse_market("2-й сет 6-й гейм: Исход")
    c = parse_market("2-й сет 7-й гейм: Исход")
    assert a.key() == b.key() != c.key()


@pytest.mark.parametrize("name,expected", [
    ("П1:0", (1, 0)), ("П1:15", (1, 1)), ("П1:30", (1, 2)), ("П1:40", (1, 3)),
    ("0:П2", (2, 0)), ("15:П2", (2, 1)), ("30:П2", (2, 2)), ("40:П2", (2, 3)),
    ("Х", None), ("Брейк", None), ("", None),
])
def test_parse_exact_score_outcome(name, expected):
    assert parse_exact_score_outcome(name) == expected


@pytest.mark.parametrize("name,expected", [
    ("П:0", (True, 0)), ("П:40", (True, 3)),
    ("Брейк", (False, None)), ("П1:0", None),
])
def test_parse_server_score_outcome(name, expected):
    assert parse_server_score_outcome(name) == expected


# --------------------------------------------------------------- overround


def test_overround_of_a_fair_two_way_book_is_zero():
    assert overround([2.0, 2.0]) == pytest.approx(0.0, abs=1e-12)


def test_overround_matches_the_measured_game_markets():
    """Medians measured on a live capture, 2026-09-22.

    Two-way game winner 11.8%, five-way server-score 15.0%, eight-way exact
    score 20.0%. The eight-way agrees with the 19.9% read off a July
    screenshot, which is what makes both readings credible.
    """
    assert overround([1.37, 2.95]) == pytest.approx(0.0689, abs=1e-4)
    assert book_sum([1.37, 2.95]) == pytest.approx(1.0689, abs=1e-4)


def test_overround_rejects_a_suspended_price():
    """The feed emits 0.0 for a suspended outcome; 1/0 would poison the book."""
    with pytest.raises(ValueError):
        overround([1.85, 0.0])
    with pytest.raises(ValueError):
        overround([1.0, 2.0])
    with pytest.raises(ValueError):
        overround([])


def test_proportional_normalisation_sums_to_one():
    probs = normalize_proportional([1.37, 2.95, 12.0, 30.0])
    assert sum(probs) == pytest.approx(1.0, abs=1e-12)
    assert probs[0] > probs[1] > probs[2] > probs[3]


def test_shin_sums_to_one_and_finds_a_sane_insider_fraction():
    probs, z = shin([1.37, 2.95])
    assert sum(probs) == pytest.approx(1.0, abs=1e-9)
    assert 0.0 <= z < 1.0


def test_shin_shortens_the_longshot_relative_to_proportional():
    """The correction must go the way the favourite-longshot bias does.

    Proportional normalisation overstates longshots; Shin pulls them down and
    the favourite up. On a real eight-way game book the two differ by over a
    point of hold probability, which is why the choice is exposed to callers.
    """
    odds = [1.9, 4.5, 11.0, 26.0, 3.4, 9.0, 21.0, 60.0]
    prop = normalize_proportional(odds)
    shin_probs, z = shin(odds)
    assert z > 0.0
    assert shin_probs[0] > prop[0]              # favourite up
    assert shin_probs[-1] < prop[-1]            # longest shot down


def test_shin_reduces_to_proportional_on_a_fair_book():
    odds = [2.0, 2.0]
    probs, z = shin(odds)
    assert z == 0.0
    assert probs == pytest.approx(normalize_proportional(odds))


def test_shin_handles_a_book_that_sums_below_one():
    """An arbitrage happens on a live feed; it is the caller's business."""
    probs, z = shin([2.2, 2.2])
    assert z == 0.0
    assert sum(probs) == pytest.approx(1.0, abs=1e-12)


# --------------------------------------------------------------- fit


@pytest.mark.parametrize("p", [0.50, 0.532, 0.55, 0.60, 0.62, 0.65, 0.70, 0.75])
def test_fit_recovers_the_p_that_generated_the_book(p):
    fit = fit_point_prob(exact_score_dist(p))
    assert fit.p == pytest.approx(p, abs=1e-4)
    assert fit.hold == pytest.approx(p_game(p), abs=1e-4)
    assert fit.max_residual < 1e-6


def test_fit_recovers_an_underdog_server_too():
    """With all eight outcomes the p <-> 1-p ambiguity is gone.

    Three same-side prices determine p only up to the mirror, which once
    produced hold probabilities of 7-25%. The eight-way market does not need
    the p >= 0.5 constraint, and this pins that.
    """
    fit = fit_point_prob(exact_score_dist(0.42))
    assert fit.p == pytest.approx(0.42, abs=1e-4)


def test_fit_reports_the_worst_outcome_not_just_an_average():
    """An RMSE hides one badly priced longshot -- the one an insider takes."""
    book = dict(exact_score_dist(0.62))
    book["break_0"] += 0.05
    fit = fit_point_prob(book)
    assert fit.worst_outcome == "break_0"
    assert fit.max_residual > fit.rmse


def test_fit_needs_every_outcome():
    book = dict(exact_score_dist(0.62))
    del book["hold_40"]
    with pytest.raises(ValueError, match="missing"):
        fit_point_prob(book)


def test_fit_on_a_real_quoted_book():
    """A genuine BetBoom book, captured live 2026-09-22.

    Match 5950804, WTA 125 Porto, set 1 game 5, home serving. These are the
    prices as quoted, mapped from the book's player indexing (П1:0, 0:П2) to
    the model's server indexing. Overround 19.78%, Shin z = 0.029.

    Eight prices really do collapse to one number: p = 0.515 reproduces all
    eight to within 0.005. Across 108 live books the median largest residual
    was 0.01. If this starts failing, the structural claim the project rests
    on -- that a game market is a function of a single point probability --
    has changed, and that is worth knowing loudly.
    """
    odds = {"hold_0": 10.5, "hold_15": 6.25, "hold_30": 5.1, "hold_40": 5.3,
            "break_0": 14.5, "break_15": 7.25, "break_30": 5.7, "break_40": 5.7}
    assert overround(list(odds.values())) == pytest.approx(0.1978, abs=5e-4)

    names = list(odds)
    probs, z = shin([odds[k] for k in names])
    assert z == pytest.approx(0.0285, abs=5e-4)
    fit = fit_point_prob(dict(zip(names, probs)))
    assert fit.p == pytest.approx(0.5147, abs=1e-3)
    assert fit.hold == pytest.approx(0.5367, abs=1e-3)
    assert fit.max_residual == pytest.approx(0.0047, abs=5e-4)


def test_the_two_markets_on_one_game_do_not_quite_agree():
    """Both books for match 5950804, set 1 game 5, captured live 2026-09-22.

    The same game, priced two ways by the same bookmaker at the same instant.
    They imply P(hold) of 0.531 and 0.537 -- close, but not equal, and the
    eight-way is the higher one in essentially every paired observation.

    Measured across 108 such pairs: median disagreement 1.09 points of hold
    probability under Shin, 1.38 under proportional normalisation, tail past 3.
    Shin being the smaller is the expected direction, since proportional
    normalisation overstates longshots and an eight-way book is mostly
    longshots. That the gap does not close to zero matters: against a prior
    error of 5.5 points, the bookmaker's own number is itself only defined to
    about a point, depending on which market you read it from.
    """
    two_way = {"П1": 1.70, "П2": 1.90}                     # П1 is serving
    eight_way = {"hold_0": 10.5, "hold_15": 6.25, "hold_30": 5.1, "hold_40": 5.3,
                 "break_0": 14.5, "break_15": 7.25, "break_30": 5.7, "break_40": 5.7}

    assert overround(list(two_way.values())) == pytest.approx(0.1146, abs=5e-4)
    assert overround(list(eight_way.values())) == pytest.approx(0.1978, abs=5e-4)

    tw_names = list(two_way)
    tw_shin, _ = shin([two_way[n] for n in tw_names])
    hold_two_way = tw_shin[tw_names.index("П1")]
    assert hold_two_way == pytest.approx(0.5310, abs=1e-3)

    ew_names = list(eight_way)
    ew_shin, _ = shin([eight_way[k] for k in ew_names])
    fit = fit_point_prob(dict(zip(ew_names, ew_shin)))
    assert fit.hold == pytest.approx(0.5367, abs=1e-3)

    assert abs(fit.hold - hold_two_way) == pytest.approx(0.0058, abs=1e-3)

    # proportional normalisation puts them further apart, as theory says
    tw_prop = normalize_proportional([two_way[n] for n in tw_names])
    assert abs(fit.hold - tw_prop[tw_names.index("П1")]) > abs(fit.hold - hold_two_way)


# --------------------------------------------------------------- measure


def test_server_alternates_to_the_next_game():
    """The measured fact this rests on: the book prices the NEXT game.

    Reading the scoreboard's serving side for a next-game market mirrors every
    probability to its complement -- a silent error that still looks plausible.
    """
    ctx = GameContext(set_no=1, game_no=9, server=AWAY)
    assert ctx.server_of(9) == AWAY
    assert ctx.server_of(10) == HOME
    assert ctx.server_of(11) == AWAY


def test_no_server_is_guessed_for_a_tiebreak():
    """At 6-6 the rotation is 1-2-2, not alternation."""
    ctx = GameContext(set_no=1, game_no=12, server=HOME)
    assert ctx.server_of(13) is None
    assert ctx.server_of(0) is None


def test_player_indexed_prices_map_onto_server_indexed_keys():
    book = {"П1:0": 5.0, "П1:15": 4.0, "П1:30": 6.0, "П1:40": 5.5,
            "0:П2": 26.0, "15:П2": 15.0, "30:П2": 13.5, "40:П2": 11.0}
    when_home_serves = _to_model_keys(book, HOME)
    when_away_serves = _to_model_keys(book, AWAY)
    assert when_home_serves["hold_0"] == 5.0
    assert when_home_serves["break_0"] == 26.0
    # the same prices, with the other player serving, are the mirror image
    assert when_away_serves["break_0"] == 5.0
    assert when_away_serves["hold_0"] == 26.0


def test_mapping_rejects_a_book_with_a_stray_outcome():
    book = {"П1:0": 5.0, "Х": 3.0}
    assert _to_model_keys(book, HOME) is None
