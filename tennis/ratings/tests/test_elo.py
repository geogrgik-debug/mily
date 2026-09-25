"""Elo state: the update arithmetic, and a state that survives a process exactly."""
import json

import pytest

from tennis.ratings import RESEARCH_B1, SWEEP_BEST, EloState, RatingsConfig, build_elo, from_records
from tennis.ratings.tests.synthetic import generate


@pytest.fixture(scope="module")
def matches():
    return from_records(generate(years=range(2019, 2022)))


def test_first_match_between_newcomers_follows_the_formula():
    st = EloState(SWEEP_BEST)
    bw, bl, exp_w, nw, nl = st.update(1, 2, "Clay")
    assert (bw, bl, exp_w, nw, nl) == (1500.0, 1500.0, 0.5, 0, 0)
    k = 400.0 / (0 + 5.0) ** 0.4
    assert st.overall[1] == 1500.0 + k * 0.5
    assert st.overall[2] == 1500.0 - k * 0.5
    assert st.surface[(1, "Clay")] == st.overall[1]
    assert st.count == {1: 1, 2: 1}
    # nothing was learned about other surfaces
    assert (1, "Hard") not in st.surface
    assert st.blended(1, "Hard") == 0.7 * st.overall[1] + 0.3 * 1500.0


def test_update_weight_decays_with_experience():
    st = EloState(SWEEP_BEST)
    gains = []
    for i in range(6):
        before = st.overall.get(1, 1500.0)
        st.update(1, 1000 + i, "Hard")      # a fresh opponent each time, same result
        gains.append(st.overall[1] - before)
    assert all(a > b for a, b in zip(gains, gains[1:]))


def test_expected_is_a_probability_and_symmetric(matches):
    _, st = build_elo(matches, SWEEP_BEST)
    ids = sorted(st.count)[:10]
    for a in ids:
        for b in ids:
            for s in ("Hard", "Clay", "Grass"):
                p = st.expected(a, b, s)
                assert 0 < p < 1
                assert p + st.expected(b, a, s) == pytest.approx(1.0, abs=1e-12)


def test_the_surface_weight_is_the_one_configured():
    for cfg in (SWEEP_BEST, RESEARCH_B1):
        st = EloState(cfg)
        st.overall[1], st.surface[(1, "Grass")] = 1600.0, 1800.0
        assert st.blended(1, "Grass") == (1 - cfg.surface_weight) * 1600.0 + cfg.surface_weight * 1800.0


def test_an_unknown_player_is_marked_as_unmeasured():
    st = EloState(SWEEP_BEST)
    assert st.matches(42) == 0
    assert st.blended(42, "Hard") == SWEEP_BEST.initial_rating


def test_state_round_trips_through_json_exactly(matches):
    _, st = build_elo(matches, SWEEP_BEST)
    back = EloState.from_dict(json.loads(json.dumps(st.to_dict())), SWEEP_BEST)
    assert back == st
    # exact, not approximately: every float reads back to the same bits
    assert all(back.overall[p] == r for p, r in st.overall.items())


def test_continuing_a_saved_state_equals_one_pass(matches):
    n = len(matches) // 2
    pre_all, all_at_once = build_elo(matches, SWEEP_BEST)
    pre_a, st = build_elo(matches.take(range(n)), SWEEP_BEST)
    st = EloState.from_dict(json.loads(json.dumps(st.to_dict())), SWEEP_BEST)
    pre_b, st = build_elo(matches.take(range(n, len(matches))), SWEEP_BEST, state=st)
    assert st == all_at_once
    assert (pre_all[:n] == pre_a).all() and (pre_all[n:] == pre_b).all()


def test_a_state_refuses_a_different_config(matches):
    _, st = build_elo(matches.take(range(10)), SWEEP_BEST)
    with pytest.raises(ValueError):
        build_elo(matches.take(range(10, 20)), RESEARCH_B1, state=st)


def test_config_round_trips_and_rejects_unknown_fields():
    assert RatingsConfig.from_dict(SWEEP_BEST.to_dict()) == SWEEP_BEST
    with pytest.raises(ValueError):
        RatingsConfig.from_dict({**SWEEP_BEST.to_dict(), "k1": 3})


def test_the_default_is_the_sweep_winner():
    # docs/EXPERIMENT_B, B1c: K=400, exponent 0.4, surface weight 0.3, 0.6 on Elo
    c = SWEEP_BEST
    assert (c.k0, c.k_exp, c.surface_weight, c.blend_elo) == (400.0, 0.4, 0.3, 0.6)
    assert (c.k_offset, c.window_days, c.shrink_points, c.strict_overlap) == (5.0, 366, 200.0, True)
