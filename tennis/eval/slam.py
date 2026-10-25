"""Grand Slam point by point (Sackmann's slam_pointbypoint), men's singles.

One row per point with its server (`PointServer`) and winner (`PointWinner`),
grouped into games by (`SetNo`, `GameNo`). What the files do that a reader must
know, each found by experiment B2 or here:

* rows with `PointServer` 0 (`PointNumber` '0X', '0Y') are markers, not points;
* the draw is the first digit of the match number -- 1 men, 2 women -- and
  not whether an ATP prior joined (that put 762 men's matches among the women,
  START_HERE section 10);
* a tie-break is recognised by its changing server, not by its numeric
  scores, so the 'GAME' token RG 2020, AO 2021 and RG 2021 write on a game's
  last point (which once dropped those three events) needs no special case;
* `GameWinner` on a game's last point, where present, must agree with the points.

A match with any game that does not end exactly on its last point, or whose
serve does not alternate, is dropped and counted.
"""
from __future__ import annotations

import csv
import glob
import os
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

from tennis.eval.points import Game, MatchPoints, alternates, valid_game


def _int(s) -> int:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return -1


def _game(rows: List[dict]) -> Tuple[Game, bool]:
    """(game, GameWinner agrees) from one game's point rows in file order."""
    tiebreak = len({r["PointServer"] for r in rows}) > 1
    points = tuple((int(r["PointServer"]), int(r["PointWinner"] == r["PointServer"])) for r in rows)
    game = Game(points[0][0], points, tiebreak)
    stated = rows[-1].get("GameWinner", "")
    agrees = stated not in ("1", "2") or not valid_game(game) or int(stated) == game.winner()
    return game, agrees


def match_from_rows(key: str, rows: List[dict]) -> Tuple[Tuple[Game, ...], str]:
    """Games of one match, or () and the reason it was dropped."""
    by_game: Dict[Tuple[int, int], List[dict]] = defaultdict(list)
    for r in rows:
        s, g = _int(r.get("SetNo")), _int(r.get("GameNo"))
        if s < 1 or g < 1:
            return (), "bad_game_number"
        by_game[(s, g)].append(r)
    games = []
    for sg in sorted(by_game):
        game, agrees = _game(by_game[sg])
        if not valid_game(game):
            return (), "incomplete_game"
        if not agrees:
            return (), "game_winner_mismatch"
        games.append(game)
    if not games:
        return (), "no_games"
    if not alternates(games):
        return (), "serve_order"
    return tuple(games), "ok"


def load_slam(slam_dir, draw: str = "1") -> Tuple[List[MatchPoints], Counter]:
    """Every men's (`draw`='1') singles match under `slam_dir` that passes
    validation; the Counter says why the rest were dropped."""
    files = sorted(glob.glob(os.path.join(str(slam_dir), "*-points.csv")))
    if not files:
        raise FileNotFoundError(f"no *-points.csv under {slam_dir}; "
                                "python -m tennis.eval download data/sackmann")
    out, why = [], Counter()
    for pf in files:
        year, slam = os.path.basename(pf).split("-")[:2]
        names = {}
        with open(pf.replace("-points.csv", "-matches.csv"), newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                names[r["match_id"]] = (r["player1"], r["player2"])
        points: Dict[str, List[dict]] = defaultdict(list)
        with open(pf, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                mid = r["match_id"]
                if mid.rsplit("-", 1)[-1][:1] != draw:
                    continue
                if r.get("PointServer") in ("1", "2") and r.get("PointWinner") in ("1", "2"):
                    points[mid].append(r)
        for mid, rows in points.items():
            if mid not in names:
                why["no_names"] += 1
                continue
            games, reason = match_from_rows(mid, rows)
            why[reason] += 1
            if games:
                n1, n2 = names[mid]
                out.append(MatchPoints(
                    key=mid, source="slam", level="slam", year=int(year),
                    date="", event=f"{year}-{slam}", name1=n1, name2=n2,
                    games=games,
                ))
    return out, why
