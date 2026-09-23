"""Tie a point-by-point match to its row in Sackmann's match files, and price its prior.

The point-by-point sources carry player names, the ratings carry Sackmann ids,
so each match is found among Sackmann's rows by its pair of names inside its
own event (Slams) or a date window (tennis_pointbypoint). Names are compared
by first initial plus surname, which absorbs 'N. Djokovic' against 'Novak
Djokovic'; only when that finds nothing, by initial plus any surname word, for
'Albert Ramos-Vinolas' against Sackmann's 'Albert Ramos'. Of two events in the
window, the match belongs to the one that started last: a player is in one
event at a time. A match that still finds no row, or more than one, is
dropped and counted.

The prior is the one the live process would have had: `RatingsSnapshot.prior`
as of the tournament's start date, from every tournament that started strictly
earlier. `stream_priors` replays the match files once and asks for each wanted
prior just before its tournament's first match goes in -- the same snapshot
`RatingsSnapshot.build(as_of=date)` would give, without a rebuild per date;
`tests/test_join.py` pins the two equal.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

import numpy as np

from tennis.eval.points import MatchPoints, name_key, norm_name, same_player
from tennis.ratings.config import RatingsConfig, SWEEP_BEST
from tennis.ratings.prior import Prior
from tennis.ratings.snapshot import RatingsSnapshot

SLAM_CODE = {"ausopen": "580", "frenchopen": "520", "wimbledon": "540", "usopen": "560"}

# tennis_pointbypoint dates the match; Sackmann dates the tournament's start.
# Qualifying is played up to a few days before the start, a two-week event
# runs past it by up to a fortnight.
WINDOW_BEFORE_DAYS = 16
WINDOW_AFTER_DAYS = 4


@dataclass(frozen=True)
class Joined:
    match: MatchPoints
    row: int                 # row in the Sackmann `Matches`
    p1_is_winner: bool


@dataclass(frozen=True)
class PricedMatch:
    match: MatchPoints
    date: str                # the tournament's start date, ISO
    surface: str
    best_of: int
    p1: float                # prior P(player 1 wins a point on his own serve)
    p2: float
    rated1: int              # rated matches behind each player's Elo; 0 = never seen
    rated2: int


def _strict(a: str, b: str) -> bool:
    return norm_name(a) != "" and norm_name(a) == norm_name(b)


def _side(m: MatchPoints, sack, r: int, loose: bool = False):
    """Whether player 1 is the row's winner, or None if the names do not say."""
    same = same_player if loose else _strict
    w, lo = sack.winner_name[r], sack.loser_name[r]
    fwd = same(m.name1, w) and same(m.name2, lo)
    rev = same(m.name1, lo) and same(m.name2, w)
    if fwd == rev:                     # neither fits, or the two cannot be told apart
        return None
    return fwd


def _fit(m: MatchPoints, sack, rows) -> Tuple[List[int], bool]:
    """The rows whose names fit: by the strict test, or by the loose one if the
    strict finds none. Returns (rows, loose)."""
    for loose in (False, True):
        fit = [r for r in rows if _side(m, sack, r, loose) is not None]
        if fit:
            return fit, loose
    return [], False


def join_slam(matches: Iterable[MatchPoints], sack) -> Tuple[List[Joined], Counter]:
    """Each Slam match to its main-draw row: same event, same pair of names."""
    by_event: Dict[str, List[int]] = defaultdict(list)
    for r in np.flatnonzero((sack.tourney_level == "G") & (sack.src == "tour")):
        by_event[sack.tourney_id[r]].append(int(r))
    out, why = [], Counter()
    for m in matches:
        year, slam = m.event.split("-", 1)
        rows, loose = _fit(m, sack, by_event.get(f"{year}-{SLAM_CODE[slam]}", []))
        if len(rows) != 1:
            why["no_row" if not rows else "ambiguous"] += 1
            continue
        out.append(Joined(m, rows[0], bool(_side(m, sack, rows[0], loose))))
        why["ok_loose" if loose else "ok"] += 1
    return out, why


def join_pbp(matches: Iterable[MatchPoints], sack) -> Tuple[List[Joined], Counter]:
    """Each tennis_pointbypoint match to the row with its pair of names, a
    matching level (Challenger or not) and a tournament start near its date."""
    index: Dict[Tuple[str, str], set] = defaultdict(set)
    for r in range(len(sack)):
        for name in (sack.winner_name[r], sack.loser_name[r]):
            initial, words = name_key(name)
            for w in words:
                index[(initial, w)].add(r)

    def rows_of(name: str) -> set:
        initial, words = name_key(name)
        return set().union(*(index.get((initial, w), set()) for w in words))

    days = sack.date.astype("datetime64[D]").astype(np.int64)
    out, why, taken = [], Counter(), set()
    for m in matches:
        day = (dt.date.fromisoformat(m.date) - dt.date(1970, 1, 1)).days
        chall = m.level == "chall"
        near = [r for r in sorted(rows_of(m.name1) & rows_of(m.name2))
                if (sack.tourney_level[r] == "C") == chall
                and day - WINDOW_BEFORE_DAYS <= days[r] <= day + WINDOW_AFTER_DAYS]
        rows, loose = _fit(m, sack, near)
        if len(rows) > 1:
            last = max(days[r] for r in rows)
            rows = [r for r in rows if days[r] == last]
        if len(rows) != 1:
            why["no_row" if not rows else "ambiguous"] += 1
            continue
        if rows[0] in taken:
            why["duplicate_match"] += 1
            continue
        taken.add(rows[0])
        out.append(Joined(m, rows[0], bool(_side(m, sack, rows[0], loose))))
        why["ok_loose" if loose else "ok"] += 1
    return out, why


def stream_priors(sack, rows: Iterable[int], config: RatingsConfig = SWEEP_BEST) -> Dict[int, Prior]:
    """The prior, winner as A, for each wanted row, as of its tournament's start.

    One pass over the files: before the first match of a date goes into the
    snapshot, every wanted row of that date is priced -- so each prior sees
    exactly the tournaments that started strictly earlier.
    """
    want: Dict[int, List[int]] = defaultdict(list)
    days = sack.date.astype("datetime64[D]").astype(np.int64)
    for r in rows:
        want[int(days[r])].append(int(r))
    pending = sorted(want)
    snap = RatingsSnapshot.build(sack.take(np.array([], dtype=np.int64)), config)
    out: Dict[int, Prior] = {}
    k = 0
    for r in range(len(sack)):
        while k < len(pending) and pending[k] <= days[r]:
            as_of = str(np.datetime64(pending[k], "D"))
            for w in want[pending[k]]:
                out[w] = snap.prior(int(sack.winner_id[w]), int(sack.loser_id[w]),
                                    sack.surface[w], int(sack.best_of[w]), as_of=as_of)
            k += 1
        snap.add_result(
            date=sack.date[r], winner=int(sack.winner_id[r]), loser=int(sack.loser_id[r]),
            surface=sack.surface[r], level=sack.tourney_level[r],
            w_stats=(sack.w_svpt[r], sack.w_1stWon[r], sack.w_2ndWon[r]),
            l_stats=(sack.l_svpt[r], sack.l_1stWon[r], sack.l_2ndWon[r]),
            winner_name=sack.winner_name[r], loser_name=sack.loser_name[r])
    return out


def price(joined: Iterable[Joined], sack, priors: Dict[int, Prior]) -> List[PricedMatch]:
    """Joined matches with their priors, player 1's side first."""
    out = []
    for j in joined:
        pr = priors[j.row]
        if j.p1_is_winner:
            p1, p2, n1, n2 = pr.p_serve_a, pr.p_serve_b, pr.matches_a, pr.matches_b
        else:
            p1, p2, n1, n2 = pr.p_serve_b, pr.p_serve_a, pr.matches_b, pr.matches_a
        out.append(PricedMatch(
            match=j.match, date=str(sack.date[j.row]), surface=sack.surface[j.row],
            best_of=int(sack.best_of[j.row]), p1=float(p1), p2=float(p2),
            rated1=int(n1), rated2=int(n2)))
    return out
