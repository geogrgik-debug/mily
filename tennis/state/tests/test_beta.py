"""The live serve state, version 1: a Beta posterior per server.

What these pin, roughly in order of how badly a regression would hurt: the
state after point k is built from points 1..k and nothing later (the leak
test); the arithmetic of the conjugate update; the hold probability is the
Markov game averaged over the posterior, not the game at the posterior mean;
and bad input raises instead of being priced.
"""

import math
import random

import numpy as np
import pytest

from tennis.markov import p_game, p_hold_from
from tennis.state import DEFAULT_N0, N0, MatchState, ServeBelief, hold_prob


# ---- the conjugate update ---------------------------------------------------

def test_from_prior_puts_n0_phantom_points_at_the_prior():
    b = ServeBelief.from_prior(0.6, 50)
    assert b.alpha == pytest.approx(30.0)
    assert b.beta == pytest.approx(20.0)
    assert b.mean == pytest.approx(0.6)


def test_observe_adds_one_point_and_leaves_the_old_belief_alone():
    b0 = ServeBelief.from_prior(0.6, 50)
    b1 = b0.observe(1)
    b2 = b1.observe(0)
    assert (b0.alpha, b0.beta) == pytest.approx((30.0, 20.0))
    assert (b1.alpha, b1.beta) == pytest.approx((31.0, 20.0))
    assert (b2.alpha, b2.beta) == pytest.approx((31.0, 21.0))
    with pytest.raises(Exception):
        b0.alpha = 1.0          # frozen: a belief is never changed in place


def test_observe_accepts_bools():
    b = ServeBelief.from_prior(0.6, 50)
    assert b.observe(True) == b.observe(1)
    assert b.observe(False) == b.observe(0)


def test_mean_and_sd_are_the_beta_moments():
    b = ServeBelief(alpha=31.0, beta=21.0)
    m = 31 / 52
    assert b.mean == pytest.approx(m)
    assert b.sd == pytest.approx(math.sqrt(m * (1 - m) / 53))


def test_evidence_moves_the_mean_with_weight_n_over_n_plus_n0():
    # After n served points, k of them won, the mean is the prior pulled toward
    # k/n with weight n/(n+n0): the "transfer" the audit's split-half measures.
    p, n0 = 0.62, 80
    b = ServeBelief.from_prior(p, n0)
    for won in [1] * 18 + [0] * 7:
        b = b.observe(won)
    w = 25 / (25 + n0)
    assert b.mean == pytest.approx((1 - w) * p + w * 18 / 25)


@pytest.mark.parametrize("p", [0.0, 1.0, -0.1, 1.1, float("nan")])
def test_a_prior_outside_the_open_unit_interval_is_refused(p):
    with pytest.raises(ValueError):
        ServeBelief.from_prior(p, 50)


@pytest.mark.parametrize("n0", [0, -5, float("nan"), float("inf")])
def test_the_prior_strength_must_be_positive_and_finite(n0):
    with pytest.raises(ValueError):
        ServeBelief.from_prior(0.6, n0)


@pytest.mark.parametrize("alpha,beta", [(0.0, 5.0), (5.0, -1.0), (float("nan"), 5.0), (5.0, float("inf"))])
def test_a_belief_needs_positive_finite_counts(alpha, beta):
    with pytest.raises(ValueError):
        ServeBelief(alpha, beta)


@pytest.mark.parametrize("won", [2, -1, 0.5, None, "1", float("nan")])
def test_a_point_is_won_or_lost_and_nothing_else(won):
    with pytest.raises(ValueError):
        ServeBelief.from_prior(0.6, 50).observe(won)


# ---- from the posterior to P(hold) -----------------------------------------

def _mc_hold(alpha, beta, a=0, b=0, n=400_000, seed=7):
    """Monte Carlo of the posterior average, written independently of the grid."""
    draws = np.round(np.random.default_rng(seed).beta(alpha, beta, n), 4)
    values, counts = np.unique(draws, return_counts=True)
    f = np.array([p_hold_from(float(v), a, b) for v in values])
    return float(f @ counts / n)


@pytest.mark.parametrize("alpha,beta,a,b", [
    (31, 21, 0, 0), (12, 8, 0, 0), (60, 30, 2, 1), (31, 21, 0, 3), (31, 21, 3, 3),
])
def test_p_hold_is_the_markov_game_averaged_over_the_posterior(alpha, beta, a, b):
    got = ServeBelief(alpha, beta).p_hold(a, b)
    assert got == pytest.approx(_mc_hold(alpha, beta, a, b), abs=1e-3)


def test_averaging_is_not_plugging_in_the_mean():
    # The game is concave in p around 0.65, so averaging over a broad posterior
    # must come out below the game at the mean -- the reason the audit
    # (section 9.3) says integrate, do not plug in.
    b = ServeBelief.from_prior(0.65, 20)
    assert b.p_hold() < p_game(b.mean) - 1e-3
    assert b.p_hold_plugin() == pytest.approx(p_game(b.mean))
    assert b.p_hold_plugin(2, 1) == pytest.approx(p_hold_from(b.mean, 2, 1))


@pytest.mark.parametrize("n0", [1e3, 1e4])
def test_a_strong_prior_prices_the_game_at_the_prior_plus_the_jensen_term(n0):
    # For a narrow posterior the average is p_game(mean) + p_game''(mean) * var / 2:
    # the grid must reproduce that second-order term, not just land near p_game.
    p, h = 0.63, 1e-3
    b = ServeBelief.from_prior(p, n0)
    curvature = (p_game(p + h) - 2 * p_game(p) + p_game(p - h)) / h ** 2
    assert b.p_hold() - p_game(p) == pytest.approx(0.5 * curvature * b.sd ** 2, rel=0.05)


def test_a_posterior_narrower_than_the_grid_is_still_priced():
    assert ServeBelief.from_prior(0.63, 1e8).p_hold() == pytest.approx(p_game(0.63), abs=1e-6)


def test_holding_is_breaking_for_the_mirror_image():
    # p_game(1 - p) = 1 - p_game(p), so Beta(b, a) holds exactly as often as
    # Beta(a, b) is broken.
    for alpha, beta in [(31, 21), (8, 5), (140, 70)]:
        total = ServeBelief(alpha, beta).p_hold() + ServeBelief(beta, alpha).p_hold()
        assert total == pytest.approx(1.0, abs=1e-9)


def test_the_score_inside_a_game_moves_the_hold_probability_the_right_way():
    b = ServeBelief.from_prior(0.64, 60)
    assert b.p_hold(3, 0) > b.p_hold(2, 0) > b.p_hold(0, 0) > b.p_hold(0, 2) > b.p_hold(0, 3)
    assert b.p_hold(0, 0) == pytest.approx(b.p_hold(), abs=1e-12)


def test_a_finished_game_is_settled_and_a_negative_score_refused():
    b = ServeBelief.from_prior(0.64, 60)
    assert b.p_hold(4, 0) == pytest.approx(1.0, abs=1e-12)
    assert b.p_hold(0, 4) == pytest.approx(0.0, abs=1e-12)
    with pytest.raises(ValueError):
        b.p_hold(-1, 0)


def test_the_vectorised_average_equals_the_scalar_one():
    alpha = np.array([31.0, 12.0, 140.0, 55.5])
    beta = np.array([21.0, 8.0, 70.0, 30.25])
    assert hold_prob(alpha, beta) == pytest.approx(
        [ServeBelief(a, b).p_hold() for a, b in zip(alpha, beta)], abs=1e-12)
    assert hold_prob(alpha, beta, 2, 1) == pytest.approx(
        [ServeBelief(a, b).p_hold(2, 1) for a, b in zip(alpha, beta)], abs=1e-12)


def test_a_long_batch_equals_its_pieces():
    rng = np.random.default_rng(3)
    alpha = rng.uniform(5, 300, 20_000)
    beta = rng.uniform(5, 200, 20_000)
    full = hold_prob(alpha, beta)
    idx = [0, 1, 4_095, 4_096, 19_999]
    assert full[idx] == pytest.approx(hold_prob(alpha[idx], beta[idx]), abs=1e-12)


def test_hold_prob_refuses_non_positive_counts():
    with pytest.raises(ValueError):
        hold_prob(np.array([3.0, 0.0]), np.array([2.0, 2.0]))
    with pytest.raises(ValueError):
        hold_prob(np.array([3.0]), np.array([2.0, 2.0]))


# ---- the match: two servers, each with their own belief ---------------------

A, B = 101, 202


def _state(n0=60):
    return MatchState.start(A, B, p_serve_a=0.66, p_serve_b=0.60, n0=n0)


def test_a_point_updates_only_the_server_who_played_it():
    s0 = _state()
    s1 = s0.after_point(A, 1)
    assert s1.belief(A) == s0.belief(A).observe(1)
    assert s1.belief(B) == s0.belief(B)
    s2 = s1.after_point(B, 0)
    assert s2.belief(B) == s0.belief(B).observe(0)
    assert s2.belief(A) == s1.belief(A)
    assert s0 == _state()           # the start state was not touched


def test_p_hold_next_uses_the_game_and_p_hold_now_the_score():
    s = _state()
    assert s.p_hold_next(A) == pytest.approx(s.belief(A).p_hold(), abs=1e-12)
    assert s.p_hold_now(A, 0, 0) == pytest.approx(s.p_hold_next(A), abs=1e-12)
    assert s.p_hold_now(B, 1, 2) == pytest.approx(s.belief(B).p_hold(1, 2), abs=1e-12)


def test_a_player_not_in_the_match_is_refused():
    s = _state()
    with pytest.raises(ValueError):
        s.after_point(999, 1)
    with pytest.raises(ValueError):
        s.p_hold_next(999)


def test_the_fitted_strength_is_the_default_and_there_is_one_per_level():
    assert set(N0) == {"slam", "tour", "chall"}
    assert DEFAULT_N0 == N0["tour"]
    assert MatchState.start(A, B, 0.66, 0.60) == MatchState.start(A, B, 0.66, 0.60, DEFAULT_N0)


def test_the_two_players_must_differ():
    with pytest.raises(ValueError):
        MatchState.start(A, A, 0.6, 0.6, 60)


# ---- no leak: the state after point k sees points 1..k and nothing else ------

def _stream(n_points, seed):
    """A plausible point stream: servers alternate by game, games of 4-8 points."""
    rng = random.Random(seed)
    pts, server = [], A
    while len(pts) < n_points:
        for _ in range(rng.randint(4, 8)):
            pts.append((server, int(rng.random() < 0.63)))
        server = B if server == A else A
    return pts[:n_points]


def _fold(state, points):
    out = [state]
    for server, won in points:
        out.append(out[-1].after_point(server, won))
    return out


@pytest.mark.parametrize("k", [0, 1, 7, 30, 59])
def test_the_state_after_point_k_ignores_every_later_point(k):
    pts = _stream(60, seed=k)
    honest = _fold(_state(), pts)
    flipped = [(B if s == A else A, 1 - w) for s, w in pts[k:]]
    poisoned = _fold(_state(), pts[:k] + flipped)
    assert poisoned[: k + 1] == honest[: k + 1]
    assert poisoned[-1] != honest[-1]          # the poison is real
    for i in range(k + 1):
        assert poisoned[i].p_hold_next(A) == honest[i].p_hold_next(A)
        assert poisoned[i].p_hold_next(B) == honest[i].p_hold_next(B)
