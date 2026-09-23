"""Does the live state earn its keep? n0 fitted on earlier years, the gain measured on later ones.

For every regular service game of a match the rows hold what the live state
would know just before it: the server's prior, his serve points won and lost so
far (tie-break points included), the same for the returner, and the outcome.
From them:

* `predict` -- P(hold) from the Beta posterior (`tennis.state.hold_prob`), or
  from the prior alone, or with the opponent's serve fed in (the swap alarm);
* `fit_n0` -- the prior strength with the lowest hold log loss on training
  games, with a bootstrap interval by match; `fit_n0_points` -- the same by the
  likelihood of single serve points, which is what the Beta models directly;
* `split_half` -- the audit's appendix A.5 test: how much of the deviation in
  the first k service games carries to the rest of the match;
* `serve_link` -- whether the two servers' forms in one match are correlated,
  the one thing points can say about the audit's four-way state;
* `evaluate_segment` -- all of it for one data segment, train/test split by year.

The rows are built the way the live process meets a match -- a game's points
are folded in only after its row is out -- so they hold no future by
construction; `tests/test_live_state.py` poisons later games anyway.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np

from tennis.eval.join import PricedMatch
from tennis.markov import p_game
from tennis.state import hold_prob

N0_GRID = (5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 120, 140, 170, 200,
           250, 300, 400, 500, 700, 1000)
POINT_GRID = tuple(float(x) for x in np.geomspace(5, 2000, 61))
TRANSFER_POINTS = (12, 18, 24, 37)          # the audit's A.5 rows, in serve points
GAME_ROWS_N0 = 40                           # SPW_SHRINK_POINTS in tennis/model/game_rows.py


@dataclass(frozen=True)
class Games:
    """One row per regular service game; every column is an array."""
    match: np.ndarray            # index into the priced matches
    year: np.ndarray
    served_before: np.ndarray    # the server's regular service games so far
    p: np.ndarray                # the server's prior
    p_opp: np.ndarray            # the returner's prior, on his own serve
    won: np.ndarray              # the server's serve points won so far
    lost: np.ndarray
    opp_won: np.ndarray          # the returner's serve points won so far
    opp_lost: np.ndarray
    rated: np.ndarray            # rated matches behind the server's Elo
    hold: np.ndarray             # the target

    def take(self, idx) -> "Games":
        return Games(**{f.name: getattr(self, f.name)[idx] for f in fields(self)})


@dataclass(frozen=True)
class Points:
    """One row per served point, tie-breaks included."""
    match: np.ndarray
    p: np.ndarray                # the server's prior
    won_before: np.ndarray       # his serve points won before this one
    lost_before: np.ndarray
    won: np.ndarray              # 1 if he won this one


@dataclass(frozen=True)
class Serve:
    """Both players' serve in one match: per regular game (won, lost), and totals
    over every serve point including tie-breaks."""
    match: int
    p: Tuple[float, float]
    games: Tuple[np.ndarray, np.ndarray]     # each (k, 2)
    total: Tuple[Tuple[int, int], Tuple[int, int]]


def build(priced: Sequence[PricedMatch]) -> Tuple[Games, Points, List[Serve]]:
    """The rows for a list of priced matches, each game from its past only."""
    g: Dict[str, list] = {f.name: [] for f in fields(Games)}
    pt: Dict[str, list] = {f.name: [] for f in fields(Points)}
    series: List[Serve] = []
    for mi, pm in enumerate(priced):
        prior = {1: pm.p1, 2: pm.p2}
        rated = {1: pm.rated1, 2: pm.rated2}
        won, lost, served = {1: 0, 2: 0}, {1: 0, 2: 0}, {1: 0, 2: 0}
        per_game: Dict[int, list] = {1: [], 2: []}
        for game in pm.match.games:
            if not game.tiebreak:
                s, r = game.server, 3 - game.server
                for k, v in (("match", mi), ("year", pm.match.year), ("served_before", served[s]),
                             ("p", prior[s]), ("p_opp", prior[r]), ("won", won[s]),
                             ("lost", lost[s]), ("opp_won", won[r]), ("opp_lost", lost[r]),
                             ("rated", rated[s]), ("hold", int(game.winner() == s))):
                    g[k].append(v)
                served[s] += 1
                w = sum(x for _, x in game.points)
                per_game[s].append((w, len(game.points) - w))
            # only now does this game exist for the rows after it
            for srv, x in game.points:
                for k, v in (("match", mi), ("p", prior[srv]), ("won_before", won[srv]),
                             ("lost_before", lost[srv]), ("won", x)):
                    pt[k].append(v)
                won[srv] += x
                lost[srv] += 1 - x
        series.append(Serve(
            match=mi, p=(pm.p1, pm.p2),
            games=tuple(np.array(per_game[i], dtype=float).reshape(-1, 2) for i in (1, 2)),
            total=((won[1], lost[1]), (won[2], lost[2]))))
    games = Games(**{k: np.array(v, dtype=float if k in ("p", "p_opp") else np.int64)
                     for k, v in g.items()})
    points = Points(**{k: np.array(v, dtype=float if k == "p" else np.int64)
                       for k, v in pt.items()})
    return games, points, series


# ---- predictions and scores ---------------------------------------------------

def predict(g: Games, n0: float, *, live: bool = True, average: bool = True,
            swap: bool = False) -> np.ndarray:
    """P(hold) for every row. `live=False` -- the prior alone; `swap` -- the
    server's belief fed the returner's serve points instead of his own, which
    must do worse than the prior if the pipeline has the sides right;
    `average=False` -- the game at the posterior mean instead of averaged."""
    a, b = n0 * g.p, n0 * (1.0 - g.p)
    if live:
        a = a + (g.opp_won if swap else g.won)
        b = b + (g.opp_lost if swap else g.lost)
    if average:
        return hold_prob(a, b)
    return pgame(a / (a + b))


def pgame(p: np.ndarray) -> np.ndarray:
    """`markov.p_game` over an array, bypassing its cache (one entry per float)."""
    raw = p_game.__wrapped__
    return np.fromiter((raw(float(x)) for x in p), dtype=float, count=len(p))


def log_loss(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return -(y * np.log(p) + (1 - y) * np.log1p(-p))


def _by_match(values: np.ndarray, match: np.ndarray):
    _, inv = np.unique(match, return_inverse=True)
    return np.bincount(inv, weights=values), np.bincount(inv).astype(float)


def boot_gain(ll_a: np.ndarray, ll_b: np.ndarray, match: np.ndarray, n_boot: int = 1000,
              seed: int = 0) -> dict:
    """Mean log-loss gain of b over a (positive: b is better), with a 95 %
    interval from resampling whole matches -- games of one match are not
    independent."""
    s, c = _by_match(ll_a - ll_b, match)
    rng = np.random.default_rng(seed)
    draws = np.empty(n_boot)
    for i in range(n_boot):
        w = np.bincount(rng.integers(0, len(s), len(s)), minlength=len(s))
        draws[i] = (w @ s) / (w @ c)
    return {"mean": float(s.sum() / c.sum()),
            "ci": [float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))]}


def fit_n0(g: Games, rows: np.ndarray, grid=N0_GRID, n_boot: int = 1000, seed: int = 0) -> dict:
    """The n0 on `grid` with the lowest mean hold log loss over `rows`, and the
    2.5-97.5 % range of that choice when whole matches are resampled."""
    sub = g.take(np.flatnonzero(rows))
    _, inv = np.unique(sub.match, return_inverse=True)
    counts = np.bincount(inv).astype(float)
    sums = np.column_stack([np.bincount(inv, weights=log_loss(predict(sub, n0), sub.hold))
                            for n0 in grid])
    curve = sums.sum(axis=0) / counts.sum()
    rng = np.random.default_rng(seed)
    picks = np.empty(n_boot)
    for i in range(n_boot):
        w = np.bincount(rng.integers(0, len(counts), len(counts)), minlength=len(counts))
        picks[i] = grid[int(np.argmin(w @ sums))]
    return {"n0": float(grid[int(np.argmin(curve))]),
            "ci": [float(np.percentile(picks, 2.5)), float(np.percentile(picks, 97.5))],
            "curve": [[float(n), float(v)] for n, v in zip(grid, curve)]}


def fit_n0_points(pts: Points, rows: np.ndarray, grid=POINT_GRID) -> dict:
    """The n0 that best predicts each serve point from the ones before it: the
    Beta-binomial likelihood of the serve sequence."""
    p, w, l, y = pts.p[rows], pts.won_before[rows], pts.lost_before[rows], pts.won[rows]
    curve = [float(log_loss((n0 * p + w) / (n0 + w + l), y).mean()) for n0 in grid]
    return {"n0": float(grid[int(np.argmin(curve))]),
            "curve": [[float(n), v] for n, v in zip(grid, curve)]}


def platt_fit(p: np.ndarray, y: np.ndarray, iters: int = 100) -> Tuple[float, float]:
    """logit(P') = a + b logit(P) by maximum likelihood (Newton)."""
    x = np.log(np.clip(p, 1e-12, 1 - 1e-12) / np.clip(1 - p, 1e-12, 1))
    a, b = 0.0, 1.0
    for _ in range(iters):
        q = 1.0 / (1.0 + np.exp(-(a + b * x)))
        r, w = q - y, q * (1 - q)
        grad = np.array([r.sum(), (r * x).sum()])
        hess = np.array([[w.sum(), (w * x).sum()], [(w * x).sum(), (w * x * x).sum()]])
        step = np.linalg.solve(hess, grad)
        a, b = a - step[0], b - step[1]
        if np.abs(step).max() < 1e-10:
            break
    return float(a), float(b)


def platt_apply(p: np.ndarray, a: float, b: float) -> np.ndarray:
    x = np.log(np.clip(p, 1e-12, 1 - 1e-12) / np.clip(1 - p, 1e-12, 1))
    return 1.0 / (1.0 + np.exp(-(a + b * x)))


# ---- the audit's split-half, and the link between the two serves --------------

def split_half(series: Sequence[Serve], ks=(2, 3, 4, 6)) -> List[dict]:
    """Regress the serve deviation (from the prior) over the rest of the match
    on the deviation over the first k service games. The slope is the share of
    an early deviation worth carrying forward; the Beta carries n/(n+n0)."""
    out = []
    for k in ks:
        xs, ys, ns = [], [], []
        for s in series:
            for side in (0, 1):
                gm = s.games[side]
                if len(gm) <= k:
                    continue
                w1, n1 = gm[:k, 0].sum(), gm[:k].sum()
                w2, n2 = gm[k:, 0].sum(), gm[k:].sum()
                xs.append(w1 / n1 - s.p[side])
                ys.append(w2 / n2 - s.p[side])
                ns.append(n1)
        x, y = np.array(xs), np.array(ys)
        slope = float(np.cov(x, y)[0, 1] / np.var(x, ddof=1))
        resid = y - y.mean() - slope * (x - x.mean())
        se = float(np.sqrt(resid.var(ddof=2) / (len(x) * x.var())))
        n = float(np.mean(ns))
        out.append({"k": k, "n": len(x), "points": n, "slope": slope, "se": se,
                    "implied_n0": n * (1 - slope) / slope if slope > 0 else float("inf")})
    return out


def serve_link(series: Sequence[Serve], min_points: int = 20, n_boot: int = 1000,
               seed: int = 0) -> dict:
    """Correlation of the two servers' true deviations from their priors in one
    match: the covariance of the observed deviations (their noises are
    independent -- different points) over their variance net of binomial noise."""
    e1, e2, v1, v2 = [], [], [], []
    for s in series:
        (w1, l1), (w2, l2) = s.total
        n1, n2 = w1 + l1, w2 + l2
        if n1 < min_points or n2 < min_points:
            continue
        e1.append(w1 / n1 - s.p[0])
        e2.append(w2 / n2 - s.p[1])
        v1.append(s.p[0] * (1 - s.p[0]) / n1)
        v2.append(s.p[1] * (1 - s.p[1]) / n2)
    e1, e2, v1, v2 = map(np.array, (e1, e2, v1, v2))

    def rho(i):
        a, b = e1[i], e2[i]
        cov = np.mean((a - a.mean()) * (b - b.mean()))
        true_var = 0.5 * (a.var() - v1[i].mean() + b.var() - v2[i].mean())
        return cov / true_var

    everyone = np.arange(len(e1))
    rng = np.random.default_rng(seed)
    draws = [rho(rng.integers(0, len(e1), len(e1))) for _ in range(n_boot)]
    return {"n": len(e1), "rho": float(rho(everyone)),
            "ci": [float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))],
            "raw_corr": float(np.corrcoef(e1, e2)[0, 1])}


# ---- one data segment, start to end -------------------------------------------

def _calibration(p: np.ndarray, y: np.ndarray, bins: int = 10) -> List[list]:
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
    return [[float(p[idx == b].mean()), float(y[idx == b].mean()), int((idx == b).sum())]
            for b in range(bins) if (idx == b).any()]


def evaluate_segment(priced: Sequence[PricedMatch], is_train: Callable[[PricedMatch], bool],
                     is_test: Callable[[PricedMatch], bool], grid=N0_GRID,
                     n_boot: int = 1000, seed: int = 0) -> dict:
    games, points, series = build(priced)
    train_m = np.array([is_train(pm) for pm in priced], bool)
    test_m = np.array([is_test(pm) for pm in priced], bool)
    has_prev = games.served_before >= 1
    tr = train_m[games.match] & has_prev

    fit = fit_n0(games, tr, grid, n_boot, seed)
    n0 = fit["n0"]
    res = {
        "matches": {"train": int(train_m.sum()), "test": int(test_m.sum())},
        "games": {"train": int(tr.sum()), "test_has_prev": int((test_m[games.match] & has_prev).sum()),
                  "test_all": int(test_m[games.match].sum())},
        "hold_rate": {"train": float(games.hold[tr].mean())},
        "n0_games": fit,
        "n0_points": fit_n0_points(points, train_m[points.match]),
        "transfer": [{"points": n, "weight": n / (n + n0)} for n in TRANSFER_POINTS],
        "split_half": split_half([s for s in series if train_m[s.match]]),
        "serve_link": serve_link([s for s in series if train_m[s.match]], n_boot=n_boot, seed=seed),
    }

    def models(sub: Games) -> Dict[str, np.ndarray]:
        return {"prior_plugin": pgame(sub.p), "prior": predict(sub, n0, live=False),
                "live": predict(sub, n0), "live_plugin": predict(sub, n0, average=False),
                "live_swap": predict(sub, n0, swap=True),
                "live_n0_40": predict(sub, GAME_ROWS_N0)}

    comparisons = (("live", "prior"), ("live", "prior_plugin"), ("live_swap", "prior"),
                   ("live", "live_plugin"), ("live", "live_n0_40"))
    res["test"] = {}
    for name, rows in (("has_prev", test_m[games.match] & has_prev), ("all", test_m[games.match])):
        sub = games.take(np.flatnonzero(rows))
        pr = models(sub)
        ll = {k: log_loss(v, sub.hold) for k, v in pr.items()}
        res["test"][name] = {
            "hold_rate": float(sub.hold.mean()),
            "logloss": {k: float(v.mean()) for k, v in ll.items()},
            "gain": {f"{b}->{a}": boot_gain(ll[b], ll[a], sub.match, n_boot, seed)
                     for a, b in comparisons},
        }
        if name == "has_prev":
            test_sub, test_pr, test_ll = sub, pr, ll

    train_sub = games.take(np.flatnonzero(tr))
    cal, cal_ll = {}, {}
    for k in ("prior", "live"):
        a, b = platt_fit(predict(train_sub, n0, live=(k == "live")), train_sub.hold)
        cal[k] = [a, b]
        cal_ll[k] = log_loss(platt_apply(test_pr[k], a, b), test_sub.hold)
    res["recalibrated"] = {"platt": cal,
                           "gain": boot_gain(cal_ll["prior"], cal_ll["live"], test_sub.match,
                                             n_boot, seed)}
    res["calibration"] = {k: _calibration(test_pr[k], test_sub.hold) for k in ("prior", "live")}

    thirds = np.quantile(test_sub.rated, [1 / 3, 2 / 3])
    band = np.searchsorted(thirds, test_sub.rated, side="right")
    res["by_rated"] = []
    for b in range(3):
        i = band == b
        if i.sum() == 0:
            continue
        res["by_rated"].append({
            "rated_matches": [int(test_sub.rated[i].min()), int(test_sub.rated[i].max())],
            "games": int(i.sum()),
            "gain": boot_gain(test_ll["prior"][i], test_ll["live"][i], test_sub.match[i],
                              n_boot, seed)})
    return res
