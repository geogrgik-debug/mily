"""The evaluation machinery, checked on matches simulated with a known truth.

Each player's form today is his prior plus a draw of known spread; points are
iid given it. Then the right prior strength is known -- n0 = p(1-p)/sd^2 - 1 --
and so is the split-half transfer n/(n+n0), and the link between the two
servers' forms. The tools must find what was put in, and the rows they are
fed must hold only the past.
"""

import numpy as np
import pytest

from tennis.eval.join import PricedMatch
from tennis.eval.live_state import (
    boot_gain, build, fit_n0, fit_n0_points, log_loss, platt_apply, platt_fit, predict,
    serve_link, split_half,
)
from tennis.eval.points import Game, MatchPoints
from tennis.state import MatchState, ServeBelief


def _game(rng, server, p):
    pts, a, b = [], 0, 0
    while not (max(a, b) >= 4 and abs(a - b) >= 2):
        w = int(rng.random() < p)
        pts.append((server, w))
        a, b = a + w, b + 1 - w
    return Game(server, tuple(pts))


def simulate(n_matches, sd, rho=0.0, games_each=12, seed=0):
    rng = np.random.default_rng(seed)
    cov = sd ** 2 * np.array([[1.0, rho], [rho, 1.0]])
    out = []
    for k in range(n_matches):
        p1, p2 = rng.uniform(0.58, 0.70, 2)
        d1, d2 = rng.multivariate_normal([0.0, 0.0], cov) if sd > 0 else (0.0, 0.0)
        today = {1: float(np.clip(p1 + d1, 0.3, 0.95)), 2: float(np.clip(p2 + d2, 0.3, 0.95))}
        games = tuple(_game(rng, 1 + i % 2, today[1 + i % 2]) for i in range(2 * games_each))
        m = MatchPoints(key=str(k), source="sim", level="sim", year=2012 + k % 8, date="",
                        event="", name1="a", name2="b", games=games)
        out.append(PricedMatch(match=m, date="", surface="Hard", best_of=3,
                               p1=float(p1), p2=float(p2), rated1=k % 90, rated2=50))
    return out


def _hand_match():
    g = [Game(1, ((1, 1),) * 4),                                    # P1 holds to love
         Game(2, ((2, 0), (2, 1), (2, 0), (2, 0), (2, 0))),         # P2 broken, 1 point won
         # a 7-2 tie-break: P1 wins all 5 of his serves, P2 wins 2 of 4
         Game(1, ((1, 1), (2, 1), (2, 0), (1, 1), (1, 1), (2, 1), (2, 0), (1, 1), (1, 1)),
              tiebreak=True),
         Game(2, ((2, 1),) * 4)]
    m = MatchPoints(key="h", source="sim", level="sim", year=2015, date="", event="",
                    name1="a", name2="b", games=tuple(g))
    return PricedMatch(match=m, date="", surface="Hard", best_of=3, p1=0.66, p2=0.61,
                       rated1=10, rated2=20)


# ---- the rows: only the past, and the same as the live state ----------------

def test_game_rows_count_only_points_before_the_game():
    games, points, _ = build([_hand_match()])
    assert list(games.served_before) == [0, 0, 1]           # the tie-break is not a target
    assert list(games.won) == [0, 0, 1 + 2]                 # P2: 1 won in game 2, 2 in the tie-break
    assert list(games.lost) == [0, 0, 4 + 2]
    assert list(games.opp_won) == [0, 4, 4 + 5]             # P1: 4 in game 1, 5 in the tie-break
    assert list(games.opp_lost) == [0, 0, 0]
    assert list(games.hold) == [1, 0, 1]
    assert list(games.p) == [0.66, 0.61, 0.61]
    assert list(games.p_opp) == [0.61, 0.66, 0.66]
    assert len(points.won) == 4 + 5 + 9 + 4


def test_poisoning_later_games_leaves_earlier_rows_alone():
    pm = simulate(1, 0.05, seed=4)[0]
    honest, _, _ = build([pm])
    fake = Game(1, ((1, 0),) * 4)
    for k in (1, 5, 12, 20):
        gs = pm.match.games[:k] + tuple(fake if g.server == 1 else Game(2, ((2, 1),) * 4)
                                        for g in pm.match.games[k:])
        m2 = MatchPoints(**{**pm.match.__dict__, "games": gs})
        poisoned, _, _ = build([PricedMatch(**{**pm.__dict__, "match": m2})])
        for col in ("won", "lost", "opp_won", "opp_lost", "served_before", "hold"):
            assert list(getattr(poisoned, col)[:k]) == list(getattr(honest, col)[:k])
        assert predict(poisoned, 80)[:k] == pytest.approx(predict(honest, 80)[:k], abs=1e-15)


def test_the_live_prediction_is_the_match_state_folded_over_earlier_points():
    pm = simulate(1, 0.05, seed=5)[0]
    games, _, _ = build([pm])
    got = predict(games, 70)
    state = MatchState.start(1, 2, pm.p1, pm.p2, 70)
    row = 0
    for g in pm.match.games:
        if not g.tiebreak:
            assert got[row] == pytest.approx(state.p_hold_next(g.server), abs=1e-12)
            row += 1
        for srv, won in g.points:
            state = state.after_point(srv, won)


def test_without_updates_the_prediction_is_the_prior_and_swap_feeds_the_other_serve():
    games, _, _ = build([_hand_match()])
    prior = predict(games, 70, live=False)
    assert predict(games, 70)[0] == prior[0]                  # nothing seen yet
    swapped = predict(games, 70, swap=True)
    fed = ServeBelief.from_prior(0.61, 70)                     # P2's prior, fed P1's 9 won serves
    for _ in range(9):
        fed = fed.observe(1)
    assert swapped[2] == pytest.approx(fed.p_hold(), abs=1e-12)


# ---- fitting n0: the tools find what was put in ------------------------------

def test_point_likelihood_recovers_the_prior_strength():
    sd = 0.05
    priced = simulate(1500, sd, seed=1)
    _, points, _ = build(priced)
    fit = fit_n0_points(points, np.ones(len(points.won), bool), np.geomspace(10, 1000, 41))
    p = np.array([x.p1 for x in priced] + [x.p2 for x in priced])
    truth = float(np.mean(p * (1 - p))) / sd ** 2 - 1
    assert truth * 0.7 < fit["n0"] < truth * 1.4


def test_game_log_loss_prefers_weak_priors_for_wild_form_and_strong_for_none():
    grid = (10, 20, 40, 80, 160, 320, 640, 1280)
    wild, _, _ = build(simulate(700, 0.09, seed=2))
    fit = fit_n0(wild, wild.served_before >= 1, grid, n_boot=50)
    assert fit["n0"] <= 80
    calm, _, _ = build(simulate(700, 0.0, seed=3))
    fit = fit_n0(calm, calm.served_before >= 1, grid, n_boot=50)
    assert fit["n0"] >= 320
    assert fit["ci"][0] <= fit["n0"] <= fit["ci"][1]


def test_split_half_slope_is_the_beta_transfer():
    sd = 0.05
    priced = simulate(3000, sd, seed=6)
    _, _, series = build(priced)
    p = np.array([x.p1 for x in priced] + [x.p2 for x in priced])
    n0 = float(np.mean(p * (1 - p))) / sd ** 2 - 1
    for row in split_half(series, ks=(4,)):
        assert row["slope"] == pytest.approx(row["points"] / (row["points"] + n0), abs=0.06)


@pytest.mark.parametrize("rho", [0.0, -0.6, 0.6])
def test_serve_link_finds_the_correlation_of_the_two_forms(rho):
    _, _, series = build(simulate(3000, 0.06, rho=rho, seed=7))
    link = serve_link(series, n_boot=200)
    assert link["rho"] == pytest.approx(rho, abs=0.15)
    assert link["ci"][0] < link["rho"] < link["ci"][1]


# ---- the arithmetic of the comparison ----------------------------------------

def test_log_loss_is_the_bernoulli_negative_log_likelihood():
    assert log_loss(np.array([0.8, 0.8]), np.array([1, 0])) == pytest.approx(
        [-np.log(0.8), -np.log(0.2)])


def test_boot_gain_of_a_constant_difference_is_that_constant():
    ll_a = np.full(300, 0.5)
    ll_b = np.full(300, 0.49)
    g = boot_gain(ll_a, ll_b, np.arange(300) // 3, n_boot=100)
    assert g["mean"] == pytest.approx(0.01)
    assert g["ci"][0] == pytest.approx(0.01) and g["ci"][1] == pytest.approx(0.01)


def test_platt_recovers_a_known_miscalibration():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.55, 0.95, 200_000)
    z = 0.3 + 0.8 * np.log(p / (1 - p))
    y = (rng.random(p.size) < 1 / (1 + np.exp(-z))).astype(int)
    a, b = platt_fit(p, y)
    assert a == pytest.approx(0.3, abs=0.05) and b == pytest.approx(0.8, abs=0.05)
    assert platt_apply(np.array([0.5]), 0.0, 1.0)[0] == pytest.approx(0.5)
