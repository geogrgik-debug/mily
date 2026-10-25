"""How good a prior is, measured the way experiment B1 measured it.

RMSE of the prior serve-points-won fraction against what the player actually
won on serve in that match (Gollub's metric), out of time: fit on years up to
`train_until`, report on the years after. The blend weight is refitted on the
training years over the same grid of 21 values, so the numbers are comparable
with docs/EXPERIMENT_B tables B1 and B1c; the fixed weight from the config is
reported beside it.
"""
from __future__ import annotations

import numpy as np

from tennis.ratings.config import RatingsConfig, SWEEP_BEST
from tennis.ratings.prior import historical_priors


def _rmse(pred, actual) -> float:
    return float(np.sqrt(((pred - actual) ** 2).mean()))


def evaluate(matches, config: RatingsConfig = SWEEP_BEST, train_until: int = 2021,
             min_serve_points: int = 30) -> dict:
    h = historical_priors(matches, config, min_serve_points)
    year, actual = h["year"], h["spw_actual"]
    train, test = year <= train_until, year > train_until
    cols = {"p_const": h["baseline"], "p_raw": h["spw_raw"], "p_bc": h["p_bc"],
            "p_elo": h["p_elo"]}

    best_w, best_r = 0.0, 9e9
    for w in np.linspace(0, 1, 21):
        r = np.sqrt((((w * h["p_elo"][train] + (1 - w) * h["p_bc"][train])
                      - actual[train]) ** 2).mean())
        if r < best_r:
            best_r, best_w = r, w
    cols["p_blend"] = best_w * h["p_elo"] + (1 - best_w) * h["p_bc"]
    cols["p_blend_config"] = h["p_blend"]

    res = {
        "config": config.to_dict(),
        "train_until": train_until,
        "n_matches": int(len(matches)),
        "n_player_matches": int(len(year)),
        "n_test": int(test.sum()),
        "tour_spw": float(h["tour_spw"]),
        "blend_weight_elo": float(best_w),
        "blend_weight_config": config.blend_elo,
        "rmse": {c: {"train": _rmse(v[train], actual[train]),
                     "test": _rmse(v[test], actual[test]),
                     "all": _rmse(v, actual)} for c, v in cols.items()},
        "rmse_by_src": {},
    }
    src = matches.src[h["row"]] if len(h["row"]) else np.array([], dtype=object)
    for s in sorted(set(src[test])):
        sel = test & (src == s)
        res["rmse_by_src"][str(s)] = {"n": int(sel.sum()),
                                     **{c: _rmse(cols[c][sel], actual[sel])
                                        for c in ("p_raw", "p_bc", "p_elo", "p_blend")}}
    match_year = matches.date.astype("datetime64[Y]").astype(np.int64) + 1970
    exp_w = h["elo"][match_year > train_until, 2]
    res["elo_match_accuracy_test"] = float((exp_w > 0.5).mean())
    res["elo_match_logloss_test"] = float(-np.log(np.clip(exp_w, 1e-6, 1)).mean())
    return res


def format_report(res: dict) -> str:
    lines = [f"matches {res['n_matches']}, player-matches {res['n_player_matches']} "
             f"(test {res['n_test']}), tour SPW {res['tour_spw']:.4f}",
             f"blend weight on Elo: fitted {res['blend_weight_elo']:.2f}, "
             f"config {res['blend_weight_config']:.2f}"]
    for c, r in res["rmse"].items():
        lines.append(f"  {c:15s} train={r['train']:.4f}  test={r['test']:.4f}")
    for s, r in res["rmse_by_src"].items():
        lines.append(f"  test {s:10s} n={r['n']:6d}  raw={r['p_raw']:.4f}  bc={r['p_bc']:.4f}  "
                     f"elo={r['p_elo']:.4f}  blend={r['p_blend']:.4f}")
    lines.append(f"  Elo match accuracy (test) = {res['elo_match_accuracy_test']:.4f}, "
                 f"log loss = {res['elo_match_logloss_test']:.4f}")
    return "\n".join(lines)
