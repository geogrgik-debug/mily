"""The set score `calibrate.to_plays` rebuilds, checked against the sources' own set marks.

`MatchPoints` keeps games only, so the calibration rebuilds each game's set
from who won the games before it (`calibrate.set_numbers`). The sources mark
sets themselves: tennis_pointbypoint ends a set with '.', Grand Slam PBP
numbers it in `SetNo`. `python -m tennis.eval check-sets` rereads those marks
and counts the matches where the two disagree. On 2026-09-23 that was none of
46 944 tennis_pointbypoint matches and one of 4 699 Slam matches,
2020-ausopen-1156, whose `SetNo` has a one-game "set 3" inside 2-6 2-6 5-7.
"""
from __future__ import annotations

import csv
import glob
import os
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Tuple

from tennis.eval.calibrate import set_numbers
from tennis.eval.points import MatchPoints
from tennis.eval.slam import _int


def pbp_marks(pbp_dir) -> Dict[str, List[int]]:
    """The set of every game, by `pbp_id`, from tennis_pointbypoint's '.' marks.
    The first row of a repeated id wins, as `load_pbp` keeps it."""
    out: Dict[str, List[int]] = {}
    for f in sorted(glob.glob(os.path.join(str(pbp_dir), "pbp_matches_*.csv"))):
        with open(f, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["pbp_id"] in out:
                    continue
                body = row.get("pbp", "").strip().rstrip(".")
                out[row["pbp_id"]] = [i + 1 for i, s in enumerate(body.split(".") if body else [])
                                      for _ in s.split(";")]
    return out


def slam_marks(slam_dir, draw: str = "1") -> Dict[str, List[int]]:
    """The set of every game, by match id, from Grand Slam PBP's `SetNo`, games
    in the (SetNo, GameNo) order `load_slam` reads them in."""
    games: Dict[str, set] = defaultdict(set)
    for pf in sorted(glob.glob(os.path.join(str(slam_dir), "*-points.csv"))):
        with open(pf, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                mid = r["match_id"]
                if mid.rsplit("-", 1)[-1][:1] != draw:
                    continue
                if r.get("PointServer") in ("1", "2") and r.get("PointWinner") in ("1", "2"):
                    games[mid].add((_int(r.get("SetNo")), _int(r.get("GameNo"))))
    return {mid: [s for s, _ in sorted(sg)] for mid, sg in games.items()}


def compare(matches: Iterable[MatchPoints], marks: Dict[str, List[int]]) -> Tuple[Counter, List[str]]:
    """(counts of 'same', 'differ' and 'no_marks', the keys that differ)."""
    counts, differ = Counter(), []
    for m in matches:
        want = marks.get(m.key)
        if want is None:
            counts["no_marks"] += 1
        elif set_numbers(m) == want:
            counts["same"] += 1
        else:
            counts["differ"] += 1
            differ.append(m.key)
    return counts, differ
