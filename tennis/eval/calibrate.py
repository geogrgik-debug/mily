"""Track B, step 5 on history: the context residual and the calibration, fitted early, scored late.

The model is `tennis.model.hold`: the live state's P(hold) as an offset, a
logistic residual in the game's context (`tennis.features.context`), a beta
calibration on top. Here it is fitted and measured.

**The years.** One rule: everything fitted is from before 2017, everything
scored is from 2017 on.

    Slams (Grand Slam PBP)          fit 2012-2014, calibrate 2015-2016, test 2019-2024
    tour, Challenger (pointbypoint) fit 2011-2014, calibrate 2015,      test 2017

Slams 2017-2018 are in neither: the live state's n0 was fitted on Slams up to
2018, so they are not unseen. tennis_pointbypoint has no 2016.

**The rows.** One per regular service game. The live state's P(hold) and the
prior's come from `live_state.build`, which is pinned equal to `MatchState`
folded over the same points, tie-breaks included; the context comes from
`tennis.model.build_game_rows`, fed the match game by game with its set score,
and both serves' priors from one `prior_for_pair`. The two are built from the
same games in the same order and checked row by row.

**Leaks.** A row's context is the scoreboard before the game, built from the
games before it only (`tests/test_calibrate.py` poisons later games). The fit
touches rows by role only: poisoning every test row leaves the parameters
unchanged to the last digit (tested). Nothing is fitted per player (audit, U5).
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from tennis.eval.join import PricedMatch
from tennis.eval.live_state import boot_gain, build, log_loss
from tennis.eval.points import MatchPoints
from tennis.features.context import FEATURES, GameContext, matrix
from tennis.model.game_rows import GamePlay, build_game_rows, prior_for_pair
from tennis.model.hold import (
    HoldParams, beta_apply, beta_fit, expit, fit_logit, logit, predict, residual,
)
from tennis.state import N0, hold_prob

SPLITS = {
    "slam": {"fit": (2012, 2014), "cal": (2015, 2016), "test": (2019, 2024)},
    "pbp": {"fit": (2011, 2014), "cal": (2015, 2015), "test": (2017, 2017)},
}
L2_GRID = (0.0, 10.0, 100.0, 1000.0, 10000.0)
ECE_BINS = 15
OPP_SCALE = 10.0            # the returner's serve deviation, per 0.1 of serve probability


def role_of(m: MatchPoints) -> str:
    """'fit', 'cal', 'test' or '' (in no role) for a match, by its source and year."""
    for role, (lo, hi) in SPLITS["slam" if m.source == "slam" else "pbp"].items():
        if lo <= m.year <= hi:
            return role
    return ""


def _walk(m: MatchPoints):
    """Every game of a match with the score before it: (game, set number,
    games in the set, sets won), the last two as {player: count}.

    A set ends on a tie-break, or when a player has six games or more and a
    lead of two -- which also covers the advantage final sets the Slams played
    until 2019-2022. `python -m tennis.eval check-sets` checks this against the
    sources' own set marks.
    """
    games, sets, set_no = {1: 0, 2: 0}, {1: 0, 2: 0}, 1
    for g in m.games:
        yield g, set_no, dict(games), dict(sets)
        won = g.winner()
        games[won] += 1
        if g.tiebreak or (max(games.values()) >= 6 and abs(games[1] - games[2]) >= 2):
            sets[won] += 1
            games, set_no = {1: 0, 2: 0}, set_no + 1


def set_numbers(m: MatchPoints) -> List[int]:
    """The set each game of a match belongs to, tie-breaks included."""
    return [set_no for _, set_no, _, _ in _walk(m)]


def to_plays(m: MatchPoints) -> List[GamePlay]:
    """The regular service games of a match, each with the set score before it."""
    plays: List[GamePlay] = []
    for g, set_no, games, sets in _walk(m):
        if not g.tiebreak:
            s, r = g.server, 3 - g.server
            plays.append(GamePlay(server=s, returner=r, points=tuple(w for _, w in g.points),
                                  set_no=set_no, server_games=games[s], returner_games=games[r],
                                  server_sets=sets[s], returner_sets=sets[r]))
    return plays


@dataclass(frozen=True)
class Rows:
    """One row per regular service game; every column is an array."""
    match: np.ndarray        # index into the priced matches
    level: np.ndarray        # 'slam', 'tour', 'chall'
    role: np.ndarray         # 'fit', 'cal', 'test', ''
    hold: np.ndarray         # the target
    prior: np.ndarray        # P(hold) from the prior alone, averaged over it
    h: np.ndarray            # P(hold) from the live state
    Z: np.ndarray            # the context, columns as FEATURES
    opp_dev: np.ndarray      # the returner's serve belief minus his prior, x OPP_SCALE

    def take(self, idx) -> "Rows":
        return Rows(**{f.name: getattr(self, f.name)[idx] for f in fields(self)})


def build_rows(priced: Sequence[PricedMatch]) -> Rows:
    games, _, _ = build(priced)
    contexts: List[GameContext] = []
    at = 0
    for mi, pm in enumerate(priced):
        m = pm.match
        rows = build_game_rows(m.key, to_plays(m), prior_for_pair(1, 2, pm.p1, pm.p2))
        for r in rows:
            if (games.match[at], games.hold[at], games.served_before[at], games.p[at]) != \
                    (mi, r.hold, r.cum_games, r.prior_p_serve):
                raise AssertionError(f"match {m.key}: game_rows and the live state's rows "
                                     f"disagree at service game {r.game_no}")
            contexts.append(GameContext.from_row(r, m.level, pm.surface, pm.best_of))
            at += 1
    if at != len(games.hold):
        raise AssertionError(f"{at} context rows against {len(games.hold)} live-state rows")

    level = np.array([priced[i].match.level for i in games.match])
    role_by_match = np.array([role_of(pm.match) for pm in priced] or [""])
    n0 = np.array([N0[x] for x in level])
    p, po = games.p, games.p_opp
    opp_n = games.opp_won + games.opp_lost
    return Rows(
        match=games.match, level=level, role=role_by_match[games.match], hold=games.hold,
        prior=hold_prob(n0 * p, n0 * (1 - p)),
        h=hold_prob(n0 * p + games.won, n0 * (1 - p) + games.lost),
        Z=matrix(contexts),
        opp_dev=OPP_SCALE * ((games.opp_won + n0 * po) / (n0 + opp_n) - po),
    )


# ---- fitting -----------------------------------------------------------------

def _design(Z: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(len(Z)), Z])


def match_folds(match: np.ndarray, folds: int, seed: int = 0) -> np.ndarray:
    """A fold for every row, whole matches to one fold, matches dealt at random."""
    _, inv = np.unique(match, return_inverse=True)
    return np.random.default_rng(seed).permutation(inv.max() + 1)[inv] % folds


def choose_l2(h, Z, y, match, grid=L2_GRID, folds: int = 5, seed: int = 0) -> Tuple[float, list]:
    """The L2 strength with the lowest held-out log loss, folds by whole match."""
    fold = match_folds(match, folds, seed)
    X, off = _design(Z), logit(h)
    curve = []
    for l2 in grid:
        ll = np.empty(len(y))
        for f in range(folds):
            out = fold == f
            theta = fit_logit(X[~out], y[~out], offset=off[~out], l2=l2)
            ll[out] = log_loss(expit(off[out] + X[out] @ theta), y[out])
        curve.append([float(l2), float(ll.mean())])
    return curve[int(np.argmin([c[1] for c in curve]))][0], curve


def fit_params(rows: Rows, l2_grid=L2_GRID, seed: int = 0) -> HoldParams:
    """The residual on the fit rows, the beta calibration on the cal rows.
    Rows of any other role are never read."""
    f, c = rows.role == "fit", rows.role == "cal"
    l2, curve = choose_l2(rows.h[f], rows.Z[f], rows.hold[f], rows.match[f], l2_grid, seed=seed)
    theta = fit_logit(_design(rows.Z[f]), rows.hold[f], offset=logit(rows.h[f]), l2=l2)
    raw = HoldParams(features=FEATURES, intercept=float(theta[0]),
                     coef=tuple(float(x) for x in theta[1:]), calibration=(1.0, 1.0, 0.0),
                     n0=dict(N0))
    cal = beta_fit(residual(rows.h[c], rows.Z[c], raw), rows.hold[c])
    counts = {r: {lv: int(((rows.role == r) & (rows.level == lv)).sum()) for lv in N0}
              for r in ("fit", "cal")}
    meta = {"splits": SPLITS, "l2": l2, "l2_curve": curve, "rows": counts,
            "command": "python -m tennis.eval calibrate"}
    return HoldParams(raw.features, raw.intercept, raw.coef, cal, dict(N0), meta)


def boot_coef(rows: Rows, l2: float, n_boot: int = 200, seed: int = 0) -> np.ndarray:
    """The residual refitted on fit rows resampled by whole match: (n_boot, 1 + k)."""
    f = rows.role == "fit"
    X, y, off = _design(rows.Z[f]), rows.hold[f], logit(rows.h[f])
    _, inv = np.unique(rows.match[f], return_inverse=True)
    rng = np.random.default_rng(seed)
    out = np.empty((n_boot, X.shape[1]))
    for i in range(n_boot):
        w = np.bincount(rng.integers(0, inv.max() + 1, inv.max() + 1), minlength=inv.max() + 1)[inv]
        out[i] = fit_logit(X, y, offset=off, l2=l2, weight=w)
    return out


# ---- isotonic, for comparison ------------------------------------------------

def isotonic_fit(p: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Pool-adjacent-violators: (right edge of each block, its rate)."""
    order = np.argsort(p, kind="stable")
    xs, ys = p[order], y[order].astype(float)
    sums, wts, right = [], [], []
    for x, v in zip(xs, ys):
        sums.append(v)
        wts.append(1.0)
        right.append(x)
        while len(sums) > 1 and sums[-2] / wts[-2] >= sums[-1] / wts[-1]:
            s, w, r = sums.pop(), wts.pop(), right.pop()
            sums[-1] += s
            wts[-1] += w
            right[-1] = r
    return np.array(right), np.array(sums) / np.array(wts)


def isotonic_apply(p: np.ndarray, fit: Tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    right, rate = fit
    idx = np.minimum(np.searchsorted(right, p, side="left"), len(rate) - 1)
    return np.clip(rate[idx], 1e-3, 1 - 1e-3)


# ---- scoring -----------------------------------------------------------------

def ece(p: np.ndarray, y: np.ndarray, edges: np.ndarray, w: Optional[np.ndarray] = None) -> float:
    """Expected calibration error over the bins `edges` cut: the bin-share-weighted
    gap between mean forecast and outcome rate."""
    w = np.ones(len(p)) if w is None else w
    b = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)
    wb = np.bincount(b, weights=w, minlength=len(edges) - 1)
    gap = np.bincount(b, weights=w * (p - y), minlength=len(edges) - 1)
    return float(np.abs(gap).sum() / wb.sum())


def equal_count_edges(p: np.ndarray, bins: int = ECE_BINS) -> np.ndarray:
    return np.quantile(p, np.linspace(0, 1, bins + 1))


def calibration_stats(p, y, match, n_boot: int = 1000, seed: int = 0) -> dict:
    """ECE on equal-count bins and the Cox fit logit P(y) = a + b logit p, each
    with a 95 % interval by resampling whole matches (bins fixed on the full set).
    The ECE a perfectly calibrated forecast of these same p would show -- the
    floor set by the number of games -- comes from outcomes drawn from p itself."""
    edges = equal_count_edges(p)
    X = np.column_stack([np.ones(len(p)), logit(p)])
    _, inv = np.unique(match, return_inverse=True)
    rng = np.random.default_rng(seed)
    e, a, b = [], [], []
    for _ in range(n_boot):
        w = np.bincount(rng.integers(0, inv.max() + 1, inv.max() + 1), minlength=inv.max() + 1)[inv]
        e.append(ece(p, y, edges, w))
        ca, cb = fit_logit(X, y, weight=w)
        a.append(ca)
        b.append(cb)
    floor = [ece(p, (rng.random(len(p)) < p).astype(float), edges) for _ in range(200)]
    cox_a, cox_b = fit_logit(X, y)

    def ci(v):
        return [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))]
    return {"ece": ece(p, y, edges), "ece_ci": ci(e), "ece_floor": float(np.mean(floor)),
            "cox_intercept": float(cox_a), "cox_intercept_ci": ci(a),
            "cox_slope": float(cox_b), "cox_slope_ci": ci(b)}


# What each group of the context adds: the residual refitted without it.
GROUPS = {
    "level_surface": ("slam", "chall", "clay", "grass"),
    "scoreboard": ("first_service_game", "service_games_so_far", "later_set", "games_in_set",
                   "serving_for_set", "serving_to_stay", "sets_ahead", "sets_behind",
                   "deciding_set"),
    "last_games": ("just_broke", "was_broken"),
}
MODELS = ("prior", "live", "live_cal", "resid", "final", "final_iso", "final_opp",
          "resid_level_only") + tuple(f"resid_no_{g}" for g in GROUPS)
GAINS = (("prior", "live"), ("live", "resid"), ("resid", "final"), ("live", "live_cal"),
         ("live_cal", "final"), ("final", "final_opp"), ("final", "final_iso"),
         ("live", "resid_level_only")) + tuple((f"resid_no_{g}", "resid") for g in GROUPS)
CALIBRATION_OF = ("prior", "live", "resid", "final")


def _refit(rows: Rows, cols: Sequence[str], l2: float) -> np.ndarray:
    """The residual, uncalibrated, refitted on fit rows with only `cols` of the context."""
    keep = [FEATURES.index(c) for c in cols]
    f = rows.role == "fit"
    X = _design(rows.Z[:, keep])
    th = fit_logit(X[f], rows.hold[f], offset=logit(rows.h[f]), l2=l2)
    return expit(logit(rows.h) + X @ th)


def forecasts(rows: Rows, params: HoldParams, l2: float) -> Tuple[Dict[str, np.ndarray], np.ndarray]:
    """Every model's P(hold) on every row, all fitted on fit and cal rows only;
    and the residual refitted with the returner's serve, which is measured
    apart and kept out of the parameters."""
    f, c = rows.role == "fit", rows.role == "cal"
    q = residual(rows.h, rows.Z, params)
    Zo = np.column_stack([rows.Z, rows.opp_dev])
    th = fit_logit(_design(Zo[f]), rows.hold[f], offset=logit(rows.h[f]), l2=l2)
    q_opp = expit(logit(rows.h) + _design(Zo) @ th)
    return {
        "prior": rows.prior, "live": rows.h,
        "live_cal": beta_apply(rows.h, beta_fit(rows.h[c], rows.hold[c])),
        "resid": q, "final": predict(rows.h, rows.Z, params),
        "final_iso": isotonic_apply(q, isotonic_fit(q[c], rows.hold[c])),
        "final_opp": beta_apply(q_opp, beta_fit(q_opp[c], rows.hold[c])),
        "resid_level_only": _refit(rows, GROUPS["level_surface"], l2),
        **{f"resid_no_{g}": _refit(rows, [x for x in FEATURES if x not in cols], l2)
           for g, cols in GROUPS.items()},
    }, th


def evaluate(rows: Rows, n_boot: int = 1000, n_boot_coef: int = 200, seed: int = 0,
             l2_grid=L2_GRID) -> Tuple[HoldParams, dict]:
    params = fit_params(rows, l2_grid, seed)
    l2 = params.meta["l2"]
    fc, opp_theta = forecasts(rows, params, l2)
    draws = boot_coef(rows, l2, n_boot_coef, seed) if n_boot_coef else None
    names = ("intercept",) + FEATURES
    theta = (params.intercept,) + params.coef
    report = {
        "l2": l2, "l2_curve": params.meta["l2_curve"], "rows": params.meta["rows"],
        "coef": {n: {"value": float(v),
                     "ci": ([float(np.percentile(draws[:, i], 2.5)),
                             float(np.percentile(draws[:, i], 97.5))] if draws is not None else None)}
                 for i, (n, v) in enumerate(zip(names, theta))},
        "opp_coef": float(opp_theta[-1]),
        "calibration": dict(zip(("a", "b", "d"), params.calibration)),
        "test": {},
    }
    test = rows.role == "test"
    for lv in ("slam", "tour", "chall", "all"):
        i = test & ((rows.level == lv) if lv != "all" else True)
        if not i.any():
            continue
        y, mt = rows.hold[i], rows.match[i]
        ll = {k: log_loss(fc[k][i], y) for k in MODELS}
        report["test"][lv] = {
            "matches": int(len(np.unique(mt))), "games": int(i.sum()), "hold_rate": float(y.mean()),
            "logloss": {k: float(v.mean()) for k, v in ll.items()},
            "gain": {f"{a}->{b}": boot_gain(ll[a], ll[b], mt, n_boot, seed) for a, b in GAINS},
            "calibration": {k: calibration_stats(fc[k][i], y, mt, n_boot, seed)
                            for k in CALIBRATION_OF},
        }
    return params, report


# ---- the report ----------------------------------------------------------------

LABEL = {"prior": "prior alone", "live": "live state", "live_cal": "live + calibration",
         "resid": "live + residual", "final": "live + residual + calibration",
         "final_iso": "  same, isotonic instead of beta", "final_opp": "  same, + returner's serve",
         "resid_level_only": "live + residual on level and surface only",
         **{f"resid_no_{g}": f"live + residual without {g}" for g in GROUPS}}


def _ci(v, ci, fmt="+.4f") -> str:
    return f"{v:{fmt}} ({ci[0]:{fmt}} .. {ci[1]:{fmt}})"


def format_report(r: dict) -> str:
    out = [f"L2 chosen by 5-fold CV by match on the fit years: {r['l2']:g}",
           "fit rows " + ", ".join(f"{k} {v}" for k, v in r["rows"]["fit"].items())
           + "; calibration rows " + ", ".join(f"{k} {v}" for k, v in r["rows"]["cal"].items()),
           "", "residual coefficients (logit, fit years; 95 % by match):"]
    for n, c in r["coef"].items():
        out.append(f"  {n:22s} " + (_ci(c["value"], c["ci"], "+.3f") if c["ci"]
                                   else f"{c['value']:+.3f}"))
    out.append(f"  returner's serve, x0.1 (measured apart, not in the parameters): {r['opp_coef']:+.3f}")
    cal = r["calibration"]
    out.append(f"beta calibration: a {cal['a']:.3f}, b {cal['b']:.3f}, d {cal['d']:+.3f}")
    for lv, t in r["test"].items():
        out += ["", f"== test, {lv}: {t['matches']} matches, {t['games']} games, hold {t['hold_rate']:.3f}"]
        out.append("  log loss: " + "  ".join(f"{k} {v:.4f}" for k, v in t["logloss"].items()))
        for k, g in t["gain"].items():
            a, b = k.split("->")
            out.append(f"  gain {LABEL[a].strip()} -> {LABEL[b].strip()}: {_ci(g['mean'], g['ci'])}")
        for k, c in t["calibration"].items():
            out.append(f"  {LABEL[k].strip():32s} ECE {_ci(c['ece'], c['ece_ci'], '.4f')}"
                       f" [floor {c['ece_floor']:.4f}]  Cox intercept "
                       f"{_ci(c['cox_intercept'], c['cox_intercept_ci'], '+.3f')}, slope "
                       f"{_ci(c['cox_slope'], c['cox_slope_ci'], '.3f')}")
    return "\n".join(out)
