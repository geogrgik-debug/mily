"""Exact-game event and two-leg ticket probabilities."""
import math

import pytest

from tennis.markov import exact_score_dist, p_hold_from
from tennis.replay.express import consecutive_exact_express, exact_event_from


@pytest.mark.parametrize('p', [.35, .5, .6, .72])
def test_from_zero_agrees_with_eight_way_model(p):
    for outcome, expected in exact_score_dist(p).items():
        assert math.isclose(exact_event_from(p, outcome), expected,
                            rel_tol=1e-12, abs_tol=1e-12)


def test_in_game_deuce_and_forty_thirty():
    p = .6
    assert math.isclose(exact_event_from(p, 'hold_40', (3, 3)),
                        p_hold_from(p, 3, 3))
    assert exact_event_from(p, 'hold_30', (3, 2)) == p
    assert math.isclose(exact_event_from(p, 'hold_40', (3, 2)),
                        (1 - p) * p_hold_from(p, 3, 3))
    assert exact_event_from(p, 'hold_30', (3, 3)) == 0


def test_ticket_uses_conditional_second_probability():
    result = consecutive_exact_express(
        p_first_server=.60, p_second_server_given_first=.65,
        first_outcome='hold_40', second_outcome='hold_30',
        first_odds=4.7, second_odds=4.6)
    expected = exact_score_dist(.60)['hold_40'] * exact_score_dist(.65)['hold_30']
    assert math.isclose(result.joint_probability, expected)
    assert math.isclose(result.offered_odds, 21.62)
    assert math.isclose(result.break_even_probability, 1 / 21.62)
    assert result.expected_net_per_unit < 0


def test_rejects_unknown_score_or_outcome():
    with pytest.raises(ValueError):
        exact_event_from(.6, 'hold_15', (15, 0))  # points won, not display strings
    with pytest.raises(ValueError):
        exact_event_from(.6, 'hold_45')
