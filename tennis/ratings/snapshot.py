"""A ratings snapshot: everything the live process needs to price a prior, on disk.

Building one replays every match once (about a minute for 270 thousand);
loading one reads a single JSON file. The snapshot holds

* the Elo state after its last match,
* each player's serve/return entries a future window can still reach,
* serve totals per surface and year, for the previous-year baseline,
* the tour mean, taken over its own inputs only,
* the config, the last tournament date and the sha256 of every input file.

`prior(a, b, surface, best_of, as_of)` refuses an `as_of` before the last
tournament date in the snapshot: the Elo state already contains later
results, so a prior "as of" then would be computed from the future. The same
date is allowed, because live the finished rounds of the current tournament
are added as they end and the next round's prior should see them -- as the
original's Elo did, while its serve window skipped the whole tournament. So
the snapshot must never contain the match being priced: live it has not been
played yet, and a backtest builds the snapshot with `as_of`, which reads only
tournaments that started strictly earlier.

`add_result` applies a new match in the same arithmetic the build used, so a
snapshot extended result by result equals one rebuilt from scratch; the tests
pin that.
"""
from __future__ import annotations

import gzip
import json
import math
from typing import Dict, Optional

import numpy as np

from tennis.markov import invert
from tennis.ratings.config import RatingsConfig, SWEEP_BEST
from tennis.ratings.elo import EloState
from tennis.ratings.prior import BaselineTable, Prior, barnett_clarke
from tennis.ratings.serve import Entry, ServeHistory, _day, shrink, span_days

FORMAT = "tennis.ratings.snapshot/1"


def _iso(day: int) -> str:
    return str(np.datetime64(int(day), "D"))


_YEARS: Dict[int, int] = {}


def _year(day: int) -> int:
    y = _YEARS.get(day)
    if y is None:
        y = _YEARS[day] = int(np.datetime64(int(day), "D").astype("datetime64[Y]").astype(np.int64)) + 1970
    return y


class RatingsSnapshot:
    def __init__(self, config: RatingsConfig, elo: EloState, serve: ServeHistory,
                 baselines: BaselineTable, tour_spw: float, last_day: Optional[int],
                 names: Dict[int, str], n_matches: int, sources: Optional[dict] = None,
                 tour_totals=(0.0, 0.0)):
        self.config = config
        self.elo = elo
        self.serve = serve
        self.baselines = baselines
        self.tour_spw = tour_spw
        self.last_day = last_day          # last tournament date included, days since epoch
        self.names = names
        self.n_matches = n_matches
        self.sources = sources or {}
        self._tour_totals = list(tour_totals)

    # ------------------------------------------------------------ building
    @classmethod
    def build(cls, matches, config: RatingsConfig = SWEEP_BEST, *, as_of=None,
              sources: Optional[dict] = None) -> "RatingsSnapshot":
        """Replay `matches` (tournaments before `as_of`, if given) into a snapshot."""
        if as_of is not None:
            matches = matches.before(as_of)
        snap = cls(config, EloState(config),
                   ServeHistory(config.window_days, config.strict_overlap),
                   BaselineTable(), float("nan"), None, {}, 0, sources)
        for r in range(len(matches)):
            snap._apply(
                day=int(matches.date[r].astype(np.int64)), level=matches.tourney_level[r],
                winner=int(matches.winner_id[r]), loser=int(matches.loser_id[r]),
                surface=matches.surface[r],
                w_stats=(matches.w_svpt[r], matches.w_1stWon[r], matches.w_2ndWon[r]),
                l_stats=(matches.l_svpt[r], matches.l_1stWon[r], matches.l_2ndWon[r]),
                winner_name=matches.winner_name[r], loser_name=matches.loser_name[r],
                trim=False)
        snap._trim()
        return snap

    def add_result(self, *, date, winner: int, loser: int, surface: str, level: str = "",
                   w_stats=(np.nan, np.nan, np.nan), l_stats=(np.nan, np.nan, np.nan),
                   winner_name: str = "", loser_name: str = "") -> None:
        """One more finished match. `date` is its tournament's start date;
        stats are (serve points, first-serve points won, second-serve points won)."""
        day = _day(date)
        if self.last_day is not None and day < self.last_day:
            raise ValueError(f"result dated {date} is older than the snapshot's last "
                             f"tournament {_iso(self.last_day)}; ratings are order-dependent")
        self._apply(day=day, level=level, winner=int(winner), loser=int(loser),
                    surface=surface, w_stats=w_stats, l_stats=l_stats,
                    winner_name=winner_name, loser_name=loser_name, trim=True)

    def _apply(self, *, day, level, winner, loser, surface, w_stats, l_stats,
               winner_name, loser_name, trim):
        self.elo.update(winner, loser, surface)
        self.n_matches += 1
        if winner_name:
            self.names[winner] = winner_name
        if loser_name:
            self.names[loser] = loser_name
        year = _year(day)
        end = day + span_days(level)
        ws, w1, w2 = (float(x) for x in w_stats)
        ls, l1, l2 = (float(x) for x in l_stats)
        wk, lk = w1 + w2, l1 + l2
        if all(map(math.isfinite, (ws, wk, ls, lk))) and ws > 0 and ls > 0:
            for pid, e in ((winner, Entry(day, end, wk, ws, ls - lk, ls)),
                           (loser, Entry(day, end, lk, ls, ws - wk, ws))):
                self.serve.add(pid, e)
                self.baselines.add(surface, year, e.spw_k, e.spw_n)
                self._tour_totals[0] += e.spw_k
                self._tour_totals[1] += e.spw_n
            self.tour_spw = self._tour_totals[0] / self._tour_totals[1]
        moved = self.last_day is None or day > self.last_day
        self.last_day = day if self.last_day is None else max(self.last_day, day)
        if trim and moved:
            self._trim()

    def _trim(self):
        # Every query is dated on or after last_day, so its window starts on or
        # after last_day - window_days; nothing older can ever count again.
        if self.last_day is not None:
            self.serve.trim(self.last_day - self.config.window_days)

    # ------------------------------------------------------------ reading
    def prior(self, a: int, b: int, surface: str, best_of: int = 3, as_of=None) -> Prior:
        """The prior for A against B, for a match in a tournament starting `as_of`."""
        if as_of is None:
            raise ValueError("as_of is required: the tournament's start date")
        day = _day(as_of)
        if self.last_day is not None and day < self.last_day:
            raise ValueError(
                f"as_of {as_of} is before the snapshot's last tournament "
                f"{_iso(self.last_day)}: its ratings already contain later results")
        if surface not in self.baselines.surfaces():
            raise ValueError(f"unknown surface {surface!r}; known: {self.baselines.surfaces()}")
        if best_of not in (3, 5):
            raise ValueError(f"best_of must be 3 or 5, not {best_of}")
        c = self.config
        base = self.baselines.baseline(_year(day), surface)
        base = self.tour_spw if base is None else base
        wp = self.elo.expected(a, b, surface)
        wa = self.serve.window_day(a, day)
        wb = self.serve.window_day(b, day)
        t = self.tour_spw
        spw_a, rpw_a = (shrink(wa[0], wa[1], t, c.shrink_points),
                        shrink(wa[2], wa[3], 1 - t, c.shrink_points))
        spw_b, rpw_b = (shrink(wb[0], wb[1], t, c.shrink_points),
                        shrink(wb[2], wb[3], 1 - t, c.shrink_points))
        # Each side inverted from its own win probability, as the original did
        # row by row; invert(1 - w)[0] and invert(w)[1] can differ in the last
        # rounding step.
        p_elo_a = invert(wp, base, best_of)[0]
        p_elo_b = invert(1.0 - wp, base, best_of)[0]
        p_bc_a = barnett_clarke(base, spw_a, rpw_b, t)
        p_bc_b = barnett_clarke(base, spw_b, rpw_a, t)
        w = c.blend_elo
        return Prior(
            p_serve_a=w * p_elo_a + (1 - w) * p_bc_a,
            p_serve_b=w * p_elo_b + (1 - w) * p_bc_b,
            p_elo_a=p_elo_a, p_elo_b=p_elo_b, p_bc_a=p_bc_a, p_bc_b=p_bc_b,
            win_prob_a=wp, baseline=base,
            elo_a=self.elo.blended(a, surface), elo_b=self.elo.blended(b, surface),
            matches_a=self.elo.matches(a), matches_b=self.elo.matches(b),
            window_points_a=wa[1], window_points_b=wb[1],
        )

    def find(self, name: str):
        """Player ids whose name matches: exact (case-insensitive) first, else substring."""
        q = name.strip().lower()
        exact = [p for p, n in self.names.items() if n.lower() == q]
        if exact:
            return exact
        return [p for p, n in self.names.items() if q in n.lower()]

    @property
    def last_date(self) -> Optional[str]:
        return None if self.last_day is None else _iso(self.last_day)

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return {
            "format": FORMAT,
            "config": self.config.to_dict(),
            "last_date": self.last_date,
            "n_matches": self.n_matches,
            "tour_spw": self.tour_spw,
            "tour_totals": self._tour_totals,
            "sources": self.sources,
            "elo": self.elo.to_dict(),
            "serve": self.serve.to_dict(),
            "baselines": self.baselines.to_dict(),
            "names": {str(p): n for p, n in self.names.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RatingsSnapshot":
        if d.get("format") != FORMAT:
            raise ValueError(f"not a ratings snapshot of format {FORMAT}: {d.get('format')!r}")
        config = RatingsConfig.from_dict(d["config"])
        last = d["last_date"]
        return cls(config, EloState.from_dict(d["elo"], config),
                   ServeHistory.from_dict(d["serve"]), BaselineTable.from_dict(d["baselines"]),
                   float(d["tour_spw"]), None if last is None else _day(last),
                   {int(p): n for p, n in d["names"].items()}, int(d["n_matches"]),
                   d.get("sources") or {}, d["tour_totals"])

    def save(self, path) -> None:
        path = str(path)
        data = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "wt", encoding="utf-8") as fh:
            fh.write(data)

    @classmethod
    def load(cls, path) -> "RatingsSnapshot":
        path = str(path)
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def __eq__(self, other) -> bool:
        if not isinstance(other, RatingsSnapshot):
            return NotImplemented
        a, b = self.to_dict(), other.to_dict()
        # nan != nan, and an empty snapshot has a nan tour mean
        if a["n_matches"] == 0 and b["n_matches"] == 0:
            a["tour_spw"] = b["tour_spw"] = None
        return a == b
