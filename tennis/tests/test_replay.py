"""Score integrity and time-order checks for the live replay bridge."""
import math

from tennis.model.hold import HoldParams
from tennis.replay.core import MatchReplay, Score, next_point


def replay():
    return MatchReplay(1, 101, 202, .61, .57, level='chall', surface='Clay',
                       best_of=3, params=HoldParams.load())


def test_point_transition_and_deuce():
    assert next_point((40, 40), 1) == (50, 40)
    assert next_point((50, 40), 2) == (40, 40)
    assert next_point((50, 40), 1) is None
    assert next_point((40, 30), 1) is None


def test_game_boundary_updates_only_seen_points():
    m = replay()
    m.observe(Score(((0, 0),), (0, 0), 1), 1)
    before = m.forecasts[(1, 1)]
    m.observe(Score(((0, 0),), (15, 0), 1), 2)
    m.observe(Score(((0, 0),), (30, 0), 1), 3)
    m.observe(Score(((0, 0),), (40, 0), 1), 4)
    m.observe(Score(((1, 0),), (0, 0), 2), 5)
    m.observe(Score(((1, 0),), (0, 0), 2), 6)  # duplicate full/newsletter
    assert m.invalid_reason is None
    assert m.points_seen == 4
    assert m.outcomes[(1, 1)] == (1, 1, 5)
    assert m.forecasts[(1, 1)] == before
    assert m.forecasts[(1, 2)].server == 2
    assert m.served == {1: 1, 2: 0}


def test_skips_gap_without_fabricating_points():
    m = replay()
    m.observe(Score(((0, 0),), (0, 0), 1), 1)
    m.observe(Score(((0, 0),), (30, 0), 1), 2)
    assert m.invalid_reason == 'point_gap'
    assert m.points_seen == 0
    assert m.project_next(3)[1] == 'point_gap'


def test_next_game_projection_is_known_before_current_game():
    m = replay()
    m.observe(Score(((0, 0),), (0, 0), 1), 1)
    p, reason = m.project_next(1)
    assert reason == '' and p is not None and 0 < p < 1
    # Both possible game-one winners affect game two's score and just_broke.
    from tennis.features.context import GameContext
    from tennis.model.hold import p_hold
    params = m.params
    current_hold = m.forecasts[(1, 1)].p_hold
    if_home_wins = p_hold(m.state, 202, GameContext(
        'chall', 'Clay', 3, 1, 0, 1, 0, 0, 0, False, False), params)
    if_away_breaks = p_hold(m.state, 202, GameContext(
        'chall', 'Clay', 3, 1, 1, 0, 0, 0, 0, True, False), params)
    expected_home = 1 - (current_hold * if_home_wins +
                         (1 - current_hold) * if_away_breaks)
    assert math.isclose(p, expected_home, abs_tol=1e-12)
    m.observe(Score(((0, 0),), (15, 0), 1), 2)
    assert m.project_next(2) == (None, 'in_game_weight_uncalibrated')
    assert m.forecasts[(1, 1)].ts_received_ns == 1
