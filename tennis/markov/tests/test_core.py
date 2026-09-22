"""Tests for the Markov core.

Three kinds of check, in increasing cost:

1. Identities that must hold exactly by symmetry (equal serve strength -> 0.5).
2. Closed forms cross-checked against the recursion that they short-circuit.
3. An independent point-level Monte Carlo, written as a vectorised simulation
   rather than a second recursion, so a shared logical error in the recursions
   would not cancel out.

The inversion round-trip is the regression guard for the bug that made
`invert` return the baseline for every win probability below 0.5.
"""
import math

import numpy as np
import pytest

from tennis.markov import (
    exact_score_dist,
    invert,
    p_game,
    p_hold_from,
    p_match,
    p_set,
    p_tiebreak,
)

PS = [0.50, 0.532, 0.55, 0.60, 0.62, 0.65, 0.70, 0.75, 0.80]
MC_N = 200_000
MC_TOL = 0.005          # ~4.5 standard errors at n = 200k
SEED = 20260922


# --------------------------------------------------------------- game


@pytest.mark.parametrize("p", PS)
def test_hold_from_zero_agrees_with_closed_form(p):
    assert p_hold_from(p, 0, 0) == pytest.approx(p_game(p), abs=1e-12)


def test_game_endpoints_and_fair_point():
    assert p_game(0.0) == 0.0
    assert p_game(1.0) == 1.0
    assert p_game(0.5) == pytest.approx(0.5, abs=1e-12)


def test_game_strictly_increasing_in_p():
    vals = [p_game(p) for p in np.arange(0.01, 1.0, 0.01)]
    assert all(b > a for a, b in zip(vals, vals[1:]))


@pytest.mark.parametrize("p", PS)
def test_deuce_and_advantage_closed_forms(p):
    q = 1.0 - p
    deuce = p * p / (p * p + q * q)
    assert p_hold_from(p, 3, 3) == pytest.approx(deuce, abs=1e-12)
    assert p_hold_from(p, 4, 4) == pytest.approx(deuce, abs=1e-12)
    assert p_hold_from(p, 4, 3) == pytest.approx(p + q * deuce, abs=1e-12)
    assert p_hold_from(p, 3, 4) == pytest.approx(p * deuce, abs=1e-12)


def test_hold_from_terminal_scores():
    assert p_hold_from(0.62, 4, 0) == 1.0
    assert p_hold_from(0.62, 4, 2) == 1.0
    assert p_hold_from(0.62, 5, 3) == 1.0
    assert p_hold_from(0.62, 0, 4) == 0.0
    assert p_hold_from(0.62, 2, 4) == 0.0


@pytest.mark.parametrize("p", PS)
def test_hold_from_is_a_martingale_over_the_next_point(p):
    """The score-conditional hold probability must be consistent one point ahead."""
    for a, b in [(0, 0), (1, 0), (0, 1), (2, 1), (3, 2), (2, 3), (3, 3), (4, 3)]:
        expected = p * p_hold_from(p, a + 1, b) + (1 - p) * p_hold_from(p, a, b + 1)
        assert p_hold_from(p, a, b) == pytest.approx(expected, abs=1e-12)


def test_hold_from_rejects_bad_input():
    with pytest.raises(ValueError):
        p_hold_from(1.2, 0, 0)
    with pytest.raises(ValueError):
        p_hold_from(0.6, -1, 0)
    with pytest.raises(ValueError):
        p_hold_from(float("nan"), 0, 0)


def test_sensitivity_at_the_audit_reference_points():
    """dP(hold)/dp, the multiplier the audit's derived figures depend on.

    Documented as 1.92 at p = 0.62 and 2.45 at p = 0.532; an earlier revision
    used 2.6, which inflated everything downstream by about a third.
    """
    h = 1e-5
    for p, want in [(0.62, 1.92), (0.532, 2.45)]:
        d = (p_game(p + h) - p_game(p - h)) / (2 * h)
        assert d == pytest.approx(want, abs=0.01)


# --------------------------------------------------------------- exact score


@pytest.mark.parametrize("p", PS)
def test_exact_score_dist_is_a_distribution(p):
    d = exact_score_dist(p)
    assert len(d) == 8
    assert all(v >= 0.0 for v in d.values())
    assert sum(d.values()) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("p", PS)
def test_exact_score_holds_sum_to_p_game(p):
    d = exact_score_dist(p)
    holds = d["hold_0"] + d["hold_15"] + d["hold_30"] + d["hold_40"]
    assert holds == pytest.approx(p_game(p), abs=1e-12)


@pytest.mark.parametrize("p", PS)
def test_exact_score_agrees_with_the_recursion(p):
    """Each straight-sets branch against the binomial path count it encodes."""
    d = exact_score_dist(p)
    q = 1.0 - p
    assert d["hold_0"] == pytest.approx(p**4, abs=1e-12)
    assert d["hold_15"] == pytest.approx(4 * p**4 * q, abs=1e-12)
    assert d["hold_30"] == pytest.approx(10 * p**4 * q**2, abs=1e-12)
    assert d["hold_40"] + d["break_40"] == pytest.approx(20 * p**3 * q**3, abs=1e-12)


def test_exact_score_is_symmetric_under_p_to_q():
    """The eight outcomes mirror under p -> 1 - p.

    This is why three same-side prices cannot pin down p: h30/h15 = 2.5q is the
    mirror of b30/b15 = 2.5p, so the fit needs the p >= 0.5 constraint.
    """
    d, m = exact_score_dist(0.62), exact_score_dist(0.38)
    for k in ("0", "15", "30", "40"):
        assert d[f"hold_{k}"] == pytest.approx(m[f"break_{k}"], abs=1e-12)


# --------------------------------------------------------------- tiebreak


@pytest.mark.parametrize("p", PS)
@pytest.mark.parametrize("target", [7, 10])
def test_tiebreak_is_fair_when_serve_strength_is_equal(p, target):
    assert p_tiebreak(p, p, target) == pytest.approx(0.5, abs=1e-12)


def test_tiebreak_increasing_in_own_serve_and_decreasing_in_opponents():
    base = p_tiebreak(0.62, 0.62)
    assert p_tiebreak(0.66, 0.62) > base
    assert p_tiebreak(0.62, 0.66) < base


def test_tiebreak_to_ten_is_less_variable_than_to_seven():
    """A longer race concentrates on the stronger player."""
    assert p_tiebreak(0.68, 0.60, 10) > p_tiebreak(0.68, 0.60, 7) > 0.5


def test_tiebreak_rejects_bad_target():
    with pytest.raises(ValueError):
        p_tiebreak(0.6, 0.6, 0)


# --------------------------------------------------------------- set and match


@pytest.mark.parametrize("p", PS)
def test_set_and_match_are_fair_when_serve_strength_is_equal(p):
    assert p_set(p, p) == pytest.approx(0.5, abs=1e-12)
    assert p_match(p, p, 3) == pytest.approx(0.5, abs=1e-12)
    assert p_match(p, p, 5) == pytest.approx(0.5, abs=1e-12)


def test_best_of_five_favours_the_favourite_more():
    assert p_match(0.66, 0.60, 5) > p_match(0.66, 0.60, 3) > 0.5
    assert p_match(0.60, 0.66, 5) < p_match(0.60, 0.66, 3) < 0.5


def test_match_rejects_other_formats():
    with pytest.raises(ValueError):
        p_match(0.62, 0.62, best_of=4)


def test_edge_amplifies_up_the_hierarchy():
    """A serve edge grows strictly at every level of the hierarchy.

    This is the structural reason the project targets the game and not the match:
    a 4-point edge on serve is already 7 points of hold probability, and by the
    match it is 19. The flip side is that our prior's own 5.5-point error is 10
    points of hold probability, which is what any edge has to clear.
    """
    point = 0.04
    game = p_game(0.66) - p_game(0.62)
    set_ = p_set(0.66, 0.62) - 0.5
    bo3 = p_match(0.66, 0.62, 3) - 0.5
    bo5 = p_match(0.66, 0.62, 5) - 0.5
    assert point < game < set_ < bo3 < bo5
    assert game == pytest.approx(0.0699, abs=5e-4)
    assert bo3 == pytest.approx(0.1911, abs=5e-4)


# --------------------------------------------------------------- inversion


@pytest.mark.parametrize("best_of", [3, 5])
@pytest.mark.parametrize("w", [0.05, 0.12, 0.25, 0.37, 0.45, 0.499, 0.5, 0.501, 0.55, 0.63, 0.75, 0.88, 0.95])
@pytest.mark.parametrize("baseline", [0.58, 0.62, 0.65])
def test_invert_round_trip(w, baseline, best_of):
    """Inverting then re-evaluating must reproduce the win probability.

    Covers underdogs explicitly. Before the symmetry fix every w < 0.5 came back
    as (baseline, baseline), which round-trips to 0.5 and fails here by up to 0.45.
    """
    pa, pb = invert(w, baseline, best_of)
    assert p_match(pa, pb, best_of) == pytest.approx(w, abs=2e-3)


@pytest.mark.parametrize("w", [0.05, 0.25, 0.45])
def test_invert_underdog_mirrors_the_favourite(w):
    pa, pb = invert(w, 0.62)
    qa, qb = invert(1.0 - w, 0.62)
    assert pa == pytest.approx(qb, abs=1e-12)
    assert pb == pytest.approx(qa, abs=1e-12)


@pytest.mark.parametrize("w", [0.05, 0.25, 0.45, 0.55, 0.75, 0.95])
def test_invert_pair_averages_the_baseline(w):
    pa, pb = invert(w, 0.62)
    assert 0.5 * (pa + pb) == pytest.approx(0.62, abs=1e-9)


def test_invert_does_not_collapse_to_the_baseline_for_underdogs():
    """The specific shape of the old bug: a non-trivial w must move p off baseline."""
    pa, pb = invert(0.25, 0.62)
    assert abs(pa - 0.62) > 0.01
    assert pa < 0.62 < pb


def test_invert_is_monotone_in_win_probability():
    vals = [invert(w, 0.62)[0] for w in np.arange(0.05, 0.96, 0.05)]
    assert all(b > a for a, b in zip(vals, vals[1:]))


def test_invert_at_even_money_is_the_baseline():
    pa, pb = invert(0.5, 0.62)
    assert pa == pytest.approx(0.62, abs=2e-3)
    assert pb == pytest.approx(0.62, abs=2e-3)


# --------------------------------------------------------------- Monte Carlo


def _mc_game(p, n, rng, cap=60):
    """Point-level simulation of one service game, vectorised over n trials."""
    pts = rng.random((n, cap)) < p
    a = np.zeros(n, dtype=np.int32)
    b = np.zeros(n, dtype=np.int32)
    done = np.zeros(n, dtype=bool)
    held = np.zeros(n, dtype=bool)
    for k in range(cap):
        live = ~done
        a += (live & pts[:, k]).astype(np.int32)
        b += (live & ~pts[:, k]).astype(np.int32)
        fin_a = live & (a >= 4) & (a - b >= 2)
        fin_b = live & (b >= 4) & (b - a >= 2)
        held |= fin_a
        done |= fin_a | fin_b
        if done.all():
            break
    assert done.all(), "game simulation cap too small"
    return held.mean()


def _mc_tiebreak(pa, pb, n, rng, target=7, cap=120):
    """Point-level simulation of a tiebreak with the 1-2-2 rotation."""
    a = np.zeros(n, dtype=np.int32)
    b = np.zeros(n, dtype=np.int32)
    done = np.zeros(n, dtype=bool)
    won = np.zeros(n, dtype=bool)
    for k in range(1, cap + 1):
        a_serves = (k % 4) in (0, 1)
        p_point_a = pa if a_serves else 1.0 - pb
        win = rng.random(n) < p_point_a
        live = ~done
        a += (live & win).astype(np.int32)
        b += (live & ~win).astype(np.int32)
        fin_a = live & (a >= target) & (a - b >= 2)
        fin_b = live & (b >= target) & (b - a >= 2)
        won |= fin_a
        done |= fin_a | fin_b
        if done.all():
            break
    assert done.all(), "tiebreak simulation cap too small"
    return won.mean()


def _mc_set(pa, pb, n, rng):
    """Game-level simulation of a set: tests the set bookkeeping, not the game model."""
    ga, gb, tb = p_game(pa), p_game(pb), p_tiebreak(pa, pb)
    a = np.zeros(n, dtype=np.int32)
    b = np.zeros(n, dtype=np.int32)
    done = np.zeros(n, dtype=bool)
    won = np.zeros(n, dtype=bool)
    for g in range(12):
        pw = ga if g % 2 == 0 else 1.0 - gb      # A serves the even-indexed games
        win = rng.random(n) < pw
        live = ~done
        a += (live & win).astype(np.int32)
        b += (live & ~win).astype(np.int32)
        fin_a = live & (((a == 6) & (b <= 4)) | (a == 7))
        fin_b = live & (((b == 6) & (a <= 4)) | (b == 7))
        won |= fin_a
        done |= fin_a | fin_b
    live = ~done
    assert np.all((a[live] == 6) & (b[live] == 6)), "undecided sets must be 6-6"
    won |= live & (rng.random(n) < tb)
    return won.mean()


@pytest.mark.parametrize("p", [0.55, 0.62, 0.70])
def test_game_matches_point_level_monte_carlo(p):
    rng = np.random.default_rng(SEED)
    assert _mc_game(p, MC_N, rng) == pytest.approx(p_game(p), abs=MC_TOL)


@pytest.mark.parametrize("pa,pb", [(0.62, 0.62), (0.70, 0.55), (0.55, 0.70), (0.66, 0.60)])
def test_tiebreak_matches_point_level_monte_carlo(pa, pb):
    rng = np.random.default_rng(SEED)
    assert _mc_tiebreak(pa, pb, MC_N, rng) == pytest.approx(p_tiebreak(pa, pb), abs=MC_TOL)


@pytest.mark.parametrize("pa,pb", [(0.70, 0.55), (0.66, 0.60)])
def test_tiebreak_to_ten_matches_point_level_monte_carlo(pa, pb):
    rng = np.random.default_rng(SEED)
    got = _mc_tiebreak(pa, pb, MC_N, rng, target=10)
    assert got == pytest.approx(p_tiebreak(pa, pb, 10), abs=MC_TOL)


@pytest.mark.parametrize("pa,pb", [(0.62, 0.62), (0.70, 0.55), (0.66, 0.60)])
def test_set_matches_game_level_monte_carlo(pa, pb):
    rng = np.random.default_rng(SEED)
    assert _mc_set(pa, pb, MC_N, rng) == pytest.approx(p_set(pa, pb), abs=MC_TOL)


@pytest.mark.parametrize("p", [0.55, 0.62, 0.70])
def test_exact_score_dist_matches_point_level_monte_carlo(p):
    """The eight-outcome distribution against simulated game endings."""
    rng = np.random.default_rng(SEED)
    n, cap = MC_N, 60
    pts = rng.random((n, cap)) < p
    a = np.zeros(n, dtype=np.int32)
    b = np.zeros(n, dtype=np.int32)
    done = np.zeros(n, dtype=bool)
    label = np.zeros(n, dtype=np.int8)           # 1..4 hold 0/15/30/40, 5..8 break
    for k in range(cap):
        live = ~done
        a += (live & pts[:, k]).astype(np.int32)
        b += (live & ~pts[:, k]).astype(np.int32)
        fin_a = live & (a >= 4) & (a - b >= 2)
        fin_b = live & (b >= 4) & (b - a >= 2)
        # a game decided with the loser on 3+ points went through deuce
        label[fin_a] = np.where(b[fin_a] >= 3, 4, b[fin_a] + 1)
        label[fin_b] = np.where(a[fin_b] >= 3, 8, a[fin_b] + 5)
        done |= fin_a | fin_b
        if done.all():
            break
    assert done.all()
    keys = ["hold_0", "hold_15", "hold_30", "hold_40",
            "break_0", "break_15", "break_30", "break_40"]
    want = exact_score_dist(p)
    for i, key in enumerate(keys, start=1):
        got = (label == i).mean()
        assert got == pytest.approx(want[key], abs=MC_TOL), key
