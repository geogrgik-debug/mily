"""As-of serve and return rates on a rolling window, from `research/elo_prior.py:rolling_priors`.

For a match in a tournament that started on date D, a player's window holds his
matches from tournaments that started in [D - window_days, D): strictly
earlier, so no match feeds its own prior, and nothing of the same tournament.
Sackmann dates every match with its tournament's start date, which is why the
tournament is the smallest unit that can be excluded cleanly.

`strict_overlap` also drops events that had started before D but were still
being played on D -- a Slam's second week overlaps the Challengers starting in
it. Sackmann publishes no end date, so the span is inferred from the level:
14 days for a Slam, 12 for Masters, Finals and Davis Cup, 7 otherwise.

How it drops them is the original's, and it is subtler than its docstring:
walk back from the player's most recent earlier event and stop at the first
one that had already ended by D. Everything behind that one stays, even an
event that was formally still running -- and rightly: a player is in one
event at a time, so if he went on to play a later event that is over, his
matches in the earlier one were over too. A first-round loser at a Slam who
plays a Challenger in its second week keeps his Slam match. Dropping every
event still running on D instead was tried here first; it threw that match
away in 1,532 of 411,124 windows (0.37 %), and the walk-back is what the B1
numbers were measured with.

Order matters to a walk-back, and the original left one case to chance: two
of a player's events starting on the same date. It sorted with an unstable
quicksort, so the walk-back met them in whatever order that produced. Here the
shorter event goes last. Sackmann dates qualifying with its main draw, so a
player's longer and shorter event on one date are, in practice, a Slam or
Masters qualifying played the week before and the event he went on to --
with the shorter one last, the walk-back stops at it and keeps the qualifying,
which was over. Of the deterministic orders this one is also closest to the
original's: 652 of 411,124 windows differ (0.16 %), against 974 for file
order and 985 for the reverse.

Rates are shrunk toward the tour mean by `shrink_points` points:
spw = (won + k * tour_spw) / (played + k), and the same for return toward
1 - tour_spw.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np


def span_days(level: str) -> int:
    if level == "G":
        return 14
    if level in ("M", "F", "D"):
        return 12
    return 7


@dataclass(frozen=True)
class Entry:
    """One player's serve and return totals in one match."""
    date: int        # days since 1970-01-01: the tournament's start date
    end: int         # inferred end of that tournament, same units
    spw_k: float     # serve points won
    spw_n: float     # serve points played
    rpw_k: float     # return points won
    rpw_n: float     # return points played


def _day(d) -> int:
    return int(np.datetime64(d, "D").astype(np.int64))


def match_entries(matches) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[Entry]]:
    """Both players' entries for every match with complete serve stats.

    Returns (pid, row, won, entries) where `row` indexes `matches` and `won` is 1
    for the winner's entry. A match contributes only when both players' serve
    totals are present and positive -- the original's filter.
    """
    date = matches.date.astype("datetime64[D]").astype(np.int64)
    end = date + np.array([span_days(x) for x in matches.tourney_level], dtype=np.int64)
    w_k = matches.w_1stWon + matches.w_2ndWon
    l_k = matches.l_1stWon + matches.l_2ndWon
    pids, rows, won, entries = [], [], [], []
    # winner rows first, then loser rows: the original's concat order
    for side in (1, 0):
        if side:
            pid, sk, sn, ok_, on = matches.winner_id, w_k, matches.w_svpt, l_k, matches.l_svpt
        else:
            pid, sk, sn, ok_, on = matches.loser_id, l_k, matches.l_svpt, w_k, matches.w_svpt
        rk = on - ok_
        good = (np.isfinite(sk) & np.isfinite(sn) & np.isfinite(rk) & np.isfinite(on)
                & (sn > 0) & (on > 0))
        for i in np.flatnonzero(good):
            pids.append(int(pid[i]))
            rows.append(int(i))
            won.append(side)
            entries.append(Entry(int(date[i]), int(end[i]), float(sk[i]), float(sn[i]),
                                 float(rk[i]), float(on[i])))
    return (np.array(pids, dtype=np.int64), np.array(rows, dtype=np.int64),
            np.array(won, dtype=np.int64), entries)


def tour_mean(entries) -> float:
    """Serve points won per serve point played, over every entry."""
    k = sum(e.spw_k for e in entries)
    n = sum(e.spw_n for e in entries)
    return k / n


def shrink(k: float, n: float, prior: float, points: float) -> float:
    return (k + points * prior) / (n + points)


def _order_key(e: Entry):
    # by date; on one date, the longer event first (see the module docstring)
    return (e.date, -e.end)


class ServeHistory:
    """Per-player entries, kept in walk-back order, answering as-of window queries.

    This is what a snapshot stores for the live process: only the entries a
    future window can still reach.
    """

    def __init__(self, window_days: int, strict_overlap: bool = True):
        self.window_days = int(window_days)
        self.strict_overlap = bool(strict_overlap)
        self._by_player: Dict[int, List[Entry]] = {}
        self._keys: Dict[int, List[tuple]] = {}

    def add(self, pid: int, e: Entry) -> None:
        keys = self._keys.setdefault(pid, [])
        ents = self._by_player.setdefault(pid, [])
        k = _order_key(e)
        i = bisect.bisect_right(keys, k)        # after equal keys: stable
        keys.insert(i, k)
        ents.insert(i, e)

    def window(self, pid: int, as_of) -> Tuple[float, float, float, float, int]:
        """(spw_k, spw_n, rpw_k, rpw_n, n_entries) as of the tournament date `as_of`."""
        return self.window_day(pid, _day(as_of))

    def window_day(self, pid: int, day: int):
        keys = self._keys.get(pid)
        if not keys:
            return 0.0, 0.0, 0.0, 0.0, 0
        ents = self._by_player[pid]
        # a 1-tuple sorts before every 2-tuple with the same date
        lo = bisect.bisect_left(keys, (day - self.window_days,))
        hi = bisect.bisect_left(keys, (day,))
        if self.strict_overlap:
            # walk back past any earlier event that was still running on this date
            while hi > lo and ents[hi - 1].end > day:
                hi -= 1
        sk = sn = rk = rn = 0.0
        n = 0
        for e in ents[lo:hi]:
            sk += e.spw_k
            sn += e.spw_n
            rk += e.rpw_k
            rn += e.rpw_n
            n += 1
        return sk, sn, rk, rn, n

    def trim(self, keep_from: int) -> None:
        """Forget entries dated before day `keep_from`: no window from then on reaches them."""
        for pid in list(self._keys):
            i = bisect.bisect_left(self._keys[pid], (keep_from,))
            if i:
                del self._keys[pid][:i]
                del self._by_player[pid][:i]
            if not self._keys[pid]:
                del self._keys[pid]
                del self._by_player[pid]

    def players(self):
        return self._by_player.keys()

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_player.values())

    def to_dict(self) -> dict:
        return {
            "window_days": self.window_days,
            "strict_overlap": self.strict_overlap,
            # [date, end, spw_k, spw_n, rpw_k, rpw_n] per entry, dates in days since 1970-01-01
            "entries": {str(p): [[e.date, e.end, e.spw_k, e.spw_n, e.rpw_k, e.rpw_n] for e in v]
                        for p, v in self._by_player.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ServeHistory":
        h = cls(d["window_days"], d["strict_overlap"])
        for p, rows in d["entries"].items():
            for r in rows:
                h.add(int(p), Entry(int(r[0]), int(r[1]), float(r[2]), float(r[3]),
                                    float(r[4]), float(r[5])))
        return h

    def __eq__(self, other) -> bool:
        return (isinstance(other, ServeHistory) and self.window_days == other.window_days
                and self.strict_overlap == other.strict_overlap
                and self._by_player == other._by_player)


def rolling_windows(pids: np.ndarray, entries: List[Entry], window_days: int,
                    strict_overlap: bool = True) -> np.ndarray:
    """Window totals for every entry, as of that entry's own tournament date.

    Returns an (n, 5) array aligned with `entries`: spw_k, spw_n, rpw_k, rpw_n
    inside the window, and the original's n_prev_matches -- the player's
    entries before the window's end, all-time. The batch twin of
    `ServeHistory.window`, with cumulative sums over each player's history;
    tests pin the two against each other.

    Order within a player is `ServeHistory`'s: by date, the longer event first
    on a shared date, then entry order.
    """
    n = len(entries)
    out = np.zeros((n, 5))
    if n == 0:
        return out
    date = np.array([e.date for e in entries], dtype=np.int64)
    end = np.array([e.end for e in entries], dtype=np.int64)
    vals = np.array([[e.spw_k, e.spw_n, e.rpw_k, e.rpw_n] for e in entries])
    order = np.lexsort((np.arange(n), -end, date, pids))
    p_sorted = pids[order]
    starts = np.flatnonzero(np.r_[True, p_sorted[1:] != p_sorted[:-1]])
    bounds = np.r_[starts, n]
    for g in range(len(starts)):
        idx = order[bounds[g]:bounds[g + 1]]
        d, e_, v = date[idx], end[idx], vals[idx]
        cs = np.vstack([np.zeros(4), np.cumsum(v, axis=0)])
        lo = np.searchsorted(d, d - window_days, side="left")
        hi = np.searchsorted(d, d, side="left")
        if strict_overlap:
            for i in np.flatnonzero(hi > lo):
                e = hi[i]
                while e > lo[i] and e_[e - 1] > d[i]:
                    e -= 1
                hi[i] = e
        out[idx, :4] = cs[hi] - cs[lo]
        out[idx, 4] = hi
    return out
