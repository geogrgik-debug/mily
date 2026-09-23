"""The calibrated hold: the fits find what was planted, the live call equals the offline one."""

import numpy as np
import pytest

from tennis.features import FEATURES, GameContext, vector
from tennis.model.hold import (
    HoldParams, beta_apply, beta_fit, expit, fit_logit, logit, p_hold, predict, residual,
    start_state,
)
from tennis.state import MatchState


def _identity(**kw):
    base = dict(features=FEATURES, intercept=0.0, coef=(0.0,) * len(FEATURES),
                calibration=(1.0, 1.0, 0.0), n0={"slam": 140.0, "tour": 100.0, "chall": 90.0})
    return HoldParams(**{**base, **kw})


def _ctx(**kw):
    base = dict(level="tour", surface="Hard", best_of=3, set_no=1, server_games=4,
                returner_games=5, server_sets=0, returner_sets=0, served_before=4,
                just_broke=False, was_broken=True)
    return GameContext(**{**base, **kw})


def test_the_residual_fit_finds_a_planted_effect_over_the_offset():
    rng = np.random.default_rng(0)
    n = 200_000
    h = rng.uniform(0.6, 0.95, n)
    z = rng.integers(0, 2, (n, 2)).astype(float)
    truth = np.array([0.05, -0.2, 0.0])          # intercept, effect, nothing
    y = (rng.random(n) < expit(logit(h) + truth[0] + z @ truth[1:])).astype(float)
    X = np.column_stack([np.ones(n), z])
    theta = fit_logit(X, y, offset=logit(h))
    assert theta == pytest.approx(truth, abs=0.03)


def test_l2_shrinks_the_effects_but_not_the_intercept():
    rng = np.random.default_rng(1)
    n = 2_000
    z = rng.integers(0, 2, n).astype(float)
    y = (rng.random(n) < expit(1.0 + 0.5 * z)).astype(float)
    X = np.column_stack([np.ones(n), z])
    free, tight = fit_logit(X, y), fit_logit(X, y, l2=1e6)
    assert abs(tight[1]) < 0.01 < abs(free[1])
    # with the effect gone the intercept takes the overall rate
    assert expit(tight[0]) == pytest.approx(y.mean(), abs=0.01)


def test_weights_count_rows():
    rng = np.random.default_rng(2)
    X = np.column_stack([np.ones(500), rng.normal(size=500)])
    y = (rng.random(500) < 0.7).astype(float)
    w = rng.integers(0, 3, 500)
    rep = np.repeat(np.arange(500), w)
    assert fit_logit(X, y, weight=w) == pytest.approx(fit_logit(X[rep], y[rep]), abs=1e-8)


def test_beta_calibration_is_the_identity_on_calibrated_forecasts():
    rng = np.random.default_rng(3)
    p = rng.uniform(0.5, 0.97, 300_000)
    y = (rng.random(p.size) < p).astype(float)
    grid = np.linspace(0.5, 0.97, 50)
    assert np.abs(beta_apply(grid, beta_fit(p, y)) - grid).max() < 0.01
    assert beta_apply(np.array([0.3, 0.8]), (1.0, 1.0, 0.0)) == pytest.approx([0.3, 0.8])


def test_beta_calibration_undoes_overconfidence():
    rng = np.random.default_rng(4)
    true = rng.uniform(0.55, 0.95, 300_000)
    shown = expit(1.4 * logit(true))             # too sure of itself
    y = (rng.random(true.size) < true).astype(float)
    fixed = beta_apply(shown, beta_fit(shown, y))
    assert np.abs(fixed - true).max() < 0.02


def test_a_negative_beta_parameter_is_pinned_at_zero():
    # truth has a = 0: logit P = 1.2 * -ln(1 - p) - 1.6; noise pushes the free
    # fit of a below zero about half the time, and this seed does
    rng = np.random.default_rng(2)
    p = rng.uniform(0.6, 0.95, 20_000)
    y = (rng.random(p.size) < expit(-1.2 * np.log1p(-p) - 1.6)).astype(float)
    a, b, d = beta_fit(p, y)
    assert a >= 0 and b > 0
    truth = expit(-1.2 * np.log1p(-p) - 1.6)
    assert np.abs(beta_apply(p, (a, b, d)) - truth).max() < 0.02


def test_params_survive_a_round_trip(tmp_path):
    p = _identity(intercept=0.1, coef=tuple(np.linspace(-0.2, 0.2, len(FEATURES))),
                  calibration=(0.9, 1.1, 0.05), meta={"fit_years": {"slam": [2012, 2014]}})
    path = tmp_path / "hold.json"
    p.save(str(path))
    assert HoldParams.load(str(path)) == p


def test_params_for_other_features_are_refused():
    with pytest.raises(ValueError):
        _identity(features=FEATURES[:-1], coef=(0.0,) * (len(FEATURES) - 1))


def test_with_identity_params_the_live_call_is_the_live_state():
    s = MatchState.start(1, 2, 0.66, 0.62, n0=100.0)
    for won in (1, 0, 1, 1, 0, 0, 1):
        s = s.after_point(1, won)
    assert p_hold(s, 1, _ctx(), _identity()) == pytest.approx(s.p_hold_next(1), abs=1e-12)


def test_the_live_call_is_the_offline_prediction_on_that_row():
    params = _identity(intercept=0.12, coef=tuple(np.linspace(-0.3, 0.3, len(FEATURES))),
                       calibration=(1.05, 0.95, -0.02))
    s = start_state(params, "chall", 1, 2, 0.63, 0.60)
    for won in (0, 0, 1, 0):
        s = s.after_point(2, won)
    ctx = _ctx(level="chall", surface="Clay", just_broke=True)
    offline = predict(np.array([s.p_hold_next(2)]), vector(ctx)[None, :], params)[0]
    assert p_hold(s, 2, ctx, params) == offline
    # and the residual moves it by exactly c + beta.z in the logit
    r = residual(np.array([0.8]), vector(ctx)[None, :], params)[0]
    assert logit(r) - logit(0.8) == pytest.approx(0.12 + vector(ctx) @ np.array(params.coef))


def test_start_state_uses_the_fitted_prior_strength():
    s = start_state(_identity(), "slam", 1, 2, 0.65, 0.60)
    b = s.belief(1)
    assert b.alpha + b.beta == pytest.approx(140.0)
