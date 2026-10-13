"""Surface-blended Elo, lifted from `research/elo_prior.py:build_elo`.

The arithmetic is the original's, statement for statement, and
tests/test_equivalence.py pins it bit for bit: the B1 and B1c numbers were
produced by that code, and a rating that differs in the last bit is a rating
nobody has measured.

What is new is the state being an object that survives a process: `EloState`
serialises to plain JSON and round-trips exactly (Python writes the shortest
repr that reads back to the same float), so the live process loads ratings
instead of replaying 270 thousand matches, and `update` takes new results one
at a time in the same arithmetic the batch used.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np

from tennis.ratings.config import RatingsConfig, SWEEP_BEST

PRE_MATCH = ("elo_blend_w", "elo_blend_l", "elo_exp_w", "n_matches_w", "n_matches_l")


class EloState:
    """Overall and per-surface ratings plus match counts, keyed by Sackmann id."""

    def __init__(self, config: RatingsConfig = SWEEP_BEST):
        self.config = config
        self.overall: Dict[int, float] = {}
        self.surface: Dict[Tuple[int, str], float] = {}
        self.count: Dict[int, int] = {}

    # ------------------------------------------------------------ reading
    def blended(self, pid: int, surface: str) -> float:
        """The rating a match on `surface` is predicted from."""
        c = self.config
        e = self.overall.get(pid, c.initial_rating)
        s = self.surface.get((pid, surface), c.initial_rating)
        return (1 - c.surface_weight) * e + c.surface_weight * s

    def expected(self, a: int, b: int, surface: str) -> float:
        """P(a beats b) on `surface`."""
        return 1.0 / (1 + 10 ** ((self.blended(b, surface) - self.blended(a, surface)) / 400.0))

    def matches(self, pid: int) -> int:
        """Rated matches so far; 0 means the rating is the initial one, not a measurement."""
        return self.count.get(pid, 0)

    # ------------------------------------------------------------ writing
    def update(self, winner: int, loser: int, surface: str) -> Tuple[float, float, float, int, int]:
        """Apply one result. Returns the pre-match (blend_w, blend_l, exp_w, n_w, n_l).

        Statement for statement the body of the loop in research build_elo,
        including evaluation order, so the floats come out identical.
        """
        c = self.config
        overall, surf, cnt = self.overall, self.surface, self.count
        w, l, s = winner, loser, surface
        ew, el = overall.get(w, c.initial_rating), overall.get(l, c.initial_rating)
        sw, sl = surf.get((w, s), c.initial_rating), surf.get((l, s), c.initial_rating)
        bw = (1 - c.surface_weight) * ew + c.surface_weight * sw
        bl = (1 - c.surface_weight) * el + c.surface_weight * sl
        exp_w = 1.0 / (1 + 10 ** ((bl - bw) / 400.0))
        nw, nl = cnt.get(w, 0), cnt.get(l, 0)
        kw = c.k0 / (nw + c.k_offset) ** c.k_exp
        kl = c.k0 / (nl + c.k_offset) ** c.k_exp
        overall[w] = ew + kw * (1 - exp_w)
        overall[l] = el - kl * (1 - exp_w)
        surf[(w, s)] = sw + kw * (1 - exp_w)
        surf[(l, s)] = sl - kl * (1 - exp_w)
        cnt[w] = nw + 1
        cnt[l] = nl + 1
        return bw, bl, exp_w, nw, nl

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        by_player: Dict[str, Dict[str, float]] = {}
        for (pid, s), r in self.surface.items():
            by_player.setdefault(str(pid), {})[s] = r
        return {
            "overall": {str(p): r for p, r in self.overall.items()},
            "surface": by_player,
            "count": {str(p): n for p, n in self.count.items()},
        }

    @classmethod
    def from_dict(cls, d: dict, config: RatingsConfig) -> "EloState":
        st = cls(config)
        st.overall = {int(p): float(r) for p, r in d["overall"].items()}
        st.surface = {(int(p), s): float(r)
                      for p, by in d["surface"].items() for s, r in by.items()}
        st.count = {int(p): int(n) for p, n in d["count"].items()}
        return st

    def __eq__(self, other) -> bool:
        return (isinstance(other, EloState) and self.config == other.config
                and self.overall == other.overall and self.surface == other.surface
                and self.count == other.count)


def build_elo(matches, config: RatingsConfig = SWEEP_BEST,
              state: Optional[EloState] = None) -> Tuple[np.ndarray, EloState]:
    """Run `matches` through the rating in order.

    Returns an (n, 5) array of pre-match values, columns as in `PRE_MATCH`
    (the winner's win expectancy is column 2), and the final state. Passing a
    `state` continues it instead of starting from nothing.
    """
    st = state if state is not None else EloState(config)
    if st.config != config:
        raise ValueError("state was built with a different config")
    wid = matches.winner_id.tolist()
    lid = matches.loser_id.tolist()
    sf = matches.surface.tolist()
    out = np.empty((len(wid), 5))
    for i in range(len(wid)):
        out[i] = st.update(wid[i], lid[i], sf[i])
    return out, st
