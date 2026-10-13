"""The serve prior for one match: Elo inverted into a serve pair, Barnett-Clarke,
and their blend. The pieces of `research/elo_prior.py:evaluate`, without the
evaluation around them.

Per player X against opponent Y, as of the match's tournament date:

* p_elo -- the Elo win probability inverted through the Markov match model
  (`tennis.markov.invert`) into X's serve-point probability, the pair
  constrained to average the surface baseline;
* p_bc  -- Barnett-Clarke: baseline + (X's serve rate - tour mean)
  - (Y's return rate - (1 - tour mean)), both rates from the rolling window;
* p     -- blend_elo * p_elo + (1 - blend_elo) * p_bc.

The baseline is the surface's serve-points-won rate over the previous year that
has data for it, never the current one: the current year contains the match.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from tennis.markov import invert
from tennis.ratings.config import RatingsConfig, SWEEP_BEST
from tennis.ratings.elo import build_elo
from tennis.ratings.serve import match_entries, rolling_windows, shrink, tour_mean


class BaselineTable:
    """Serve points won and played per (surface, year)."""

    def __init__(self, totals: Optional[Dict[str, Dict[int, list]]] = None):
        self.totals: Dict[str, Dict[int, list]] = totals or {}

    def add(self, surface: str, year: int, k: float, n: float) -> None:
        t = self.totals.setdefault(surface, {}).setdefault(int(year), [0.0, 0.0])
        t[0] += k
        t[1] += n

    def baseline(self, year: int, surface: str) -> Optional[float]:
        """The most recent year before `year` with data on `surface`, or None."""
        years = [y for y in self.totals.get(surface, {}) if y < year]
        if not years:
            return None
        k, n = self.totals[surface][max(years)]
        return k / n

    def surfaces(self):
        return sorted(self.totals)

    def to_dict(self) -> dict:
        return {s: {str(y): t for y, t in sorted(by.items())} for s, by in self.totals.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "BaselineTable":
        return cls({s: {int(y): [float(t[0]), float(t[1])] for y, t in by.items()}
                    for s, by in d.items()})

    def __eq__(self, other) -> bool:
        return isinstance(other, BaselineTable) and self.totals == other.totals


def barnett_clarke(baseline: float, spw_self: float, rpw_opp: float, tour_spw: float) -> float:
    return baseline + (spw_self - tour_spw) - (rpw_opp - (1 - tour_spw))


@dataclass(frozen=True)
class Prior:
    """Everything the prior for one match is made of, A's side first."""
    p_serve_a: float        # the prior: P(A wins a point on A's serve)
    p_serve_b: float
    p_elo_a: float
    p_elo_b: float
    p_bc_a: float
    p_bc_b: float
    win_prob_a: float       # Elo P(A wins the match)
    baseline: float
    elo_a: float            # blended ratings on this surface
    elo_b: float
    matches_a: int          # rated matches behind each rating; 0 = never seen
    matches_b: int
    window_points_a: float  # serve points inside each player's window
    window_points_b: float


def historical_priors(matches, config: RatingsConfig = SWEEP_BEST, min_serve_points: int = 30) -> dict:
    """The prior for both players of every match, as of each match.

    Rows follow the original's P table and its filters: winner rows then loser
    rows; kept only when the player served at least `min_serve_points` and both
    his serve window and the opponent's return window exist (the match had full
    stats). Returns a dict of equal-length arrays, plus 'tour_spw' and the
    per-match Elo output under 'elo'.

    The tour mean here is taken over the whole input, as the original took it --
    including test years. That keeps the numbers comparable with B1; a
    snapshot, which is what the live process uses, takes it over its own
    inputs only.
    """
    n = len(matches)
    elo, _ = build_elo(matches, config)
    pids, rows, won, entries = match_entries(matches)
    tour = tour_mean(entries)
    win = rolling_windows(pids, entries, config.window_days, config.strict_overlap)
    spw_raw = (win[:, 0] + config.shrink_points * tour) / (win[:, 1] + config.shrink_points)
    rpw_raw = (win[:, 2] + config.shrink_points * (1 - tour)) / (win[:, 3] + config.shrink_points)

    # (row, side) -> position in entries; side 1 = winner
    pos = {(int(r), int(s)): i for i, (r, s) in enumerate(zip(rows, won))}

    table = BaselineTable()
    year_of = matches.date.astype("datetime64[Y]").astype(np.int64) + 1970
    for i, (r, e) in enumerate(zip(rows, entries)):
        table.add(matches.surface[r], year_of[r], e.spw_k, e.spw_n)

    out = {k: [] for k in ("row", "won", "pid", "oid", "year", "win_prob", "baseline",
                           "spw_raw", "rpw_raw", "opp_rpw_raw", "window_points",
                           "n_prev_matches", "spw_actual", "svpt")}
    base_cache = {}
    for side in (1, 0):
        for r in range(n):
            if side:
                pid, oid, sv, w1, w2 = (matches.winner_id[r], matches.loser_id[r],
                                        matches.w_svpt[r], matches.w_1stWon[r], matches.w_2ndWon[r])
                wp = elo[r, 2]
            else:
                pid, oid, sv, w1, w2 = (matches.loser_id[r], matches.winner_id[r],
                                        matches.l_svpt[r], matches.l_1stWon[r], matches.l_2ndWon[r])
                wp = 1.0 - elo[r, 2]
            actual = (w1 + w2) / sv if sv else np.nan
            if not (np.isfinite(actual) and np.isfinite(sv)) or sv < min_serve_points:
                continue
            me, opp = pos.get((r, side)), pos.get((r, 1 - side))
            if me is None or opp is None:
                continue
            key = (int(year_of[r]), matches.surface[r])
            if key not in base_cache:
                b = table.baseline(*key)
                base_cache[key] = tour if b is None else b
            out["row"].append(r)
            out["won"].append(side)
            out["pid"].append(int(pid))
            out["oid"].append(int(oid))
            out["year"].append(key[0])
            out["win_prob"].append(wp)
            out["baseline"].append(base_cache[key])
            out["spw_raw"].append(spw_raw[me])
            out["rpw_raw"].append(rpw_raw[me])
            out["opp_rpw_raw"].append(rpw_raw[opp])
            out["window_points"].append(win[me, 1])
            out["n_prev_matches"].append(int(win[me, 4]))
            out["spw_actual"].append(actual)
            out["svpt"].append(sv)
    res = {k: np.array(v) for k, v in out.items()}
    rows_ = res["row"]
    res["best_of"] = matches.best_of[rows_] if len(rows_) else np.array([], dtype=np.int64)
    res["p_elo"] = np.array([invert(w, b, bo)[0] for w, b, bo in
                             zip(res["win_prob"], res["baseline"], res["best_of"])])
    res["p_bc"] = barnett_clarke(res["baseline"], res["spw_raw"], res["opp_rpw_raw"], tour)
    res["p_blend"] = config.blend_elo * res["p_elo"] + (1 - config.blend_elo) * res["p_bc"]
    res["tour_spw"] = tour
    res["elo"] = elo
    return res
