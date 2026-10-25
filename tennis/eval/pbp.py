"""Jeff Sackmann's `tennis_pointbypoint`: one match per row, the match as a string.

Each point is one character -- S (server won), A (ace), R (returner won),
D (double fault); ';' ends a game, '.' ends a set, and '/' marks a change of
serve inside a tie-break. `server1` serves the first game, and the serve then
alternates game by game, a tie-break counting as one game: the player who
received its first point serves first in the next set.

A match is kept only if every game ends exactly on its last point and the set
scores it adds up to equal the `score` column, which is written from the
match winner's side ('6-4 6-7(5) 7-5'). The author says the data are messy --
duplicates, mis-parses, retirements left out -- so this check is the filter.
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import os
import re
from collections import Counter
from typing import List, Optional, Tuple

from tennis.eval.points import Game, MatchPoints, alternates, valid_game

_WON = {"S": 1, "A": 1, "R": 0, "D": 0}
_LEVEL = {"ATP": "tour", "CH": "chall"}


def parse_pbp(pbp: str) -> Optional[Tuple[Tuple[Game, ...], List[Tuple[int, int]]]]:
    """(games, set scores as (player 1, player 2) games) or None if unparseable."""
    games: List[Game] = []
    sets: List[Tuple[int, int]] = []
    server = 1
    body = pbp.strip().rstrip(".")
    if not body:
        return None
    for set_str in body.split("."):
        g1 = g2 = 0
        for game_str in set_str.split(";"):
            if not game_str or any(ch not in _WON and ch != "/" for ch in game_str):
                return None
            if "/" in game_str:
                pts, srv = [], server
                for seg in game_str.split("/"):
                    pts.extend((srv, _WON[ch]) for ch in seg)
                    srv = 3 - srv
                game = Game(server, tuple(pts), tiebreak=True)
            else:
                game = Game(server, tuple((server, _WON[ch]) for ch in game_str))
            if not valid_game(game):
                return None
            if game.winner() == 1:
                g1 += 1
            else:
                g2 += 1
            games.append(game)
            server = 3 - server
        sets.append((g1, g2))
    return tuple(games), sets


def score_sets(score: str) -> Optional[List[Tuple[int, int]]]:
    """'6-4 7-6(5)' -> [(6, 4), (7, 6)] from the winner's side; None if not a clean score."""
    out = []
    for tok in score.split():
        m = re.fullmatch(r"(\d+)-(\d+)(\(\d+\))?", tok)
        if not m:
            return None
        out.append((int(m.group(1)), int(m.group(2))))
    return out or None


def match_from_row(row: dict) -> Tuple[Optional[MatchPoints], str]:
    """A validated match, or None and the reason it was dropped."""
    level = _LEVEL.get(row.get("tour", ""))
    if level is None:
        return None, "level"
    parsed = parse_pbp(row.get("pbp", ""))
    if parsed is None:
        return None, "bad_game"
    games, sets = parsed
    if not alternates(games):
        return None, "serve_order"
    winner = row.get("winner", "")
    want = score_sets(row.get("score", ""))
    if winner not in ("1", "2") or want is None:
        return None, "bad_score"
    sets_1 = sum(a > b for a, b in sets)
    if ("1" if 2 * sets_1 > len(sets) else "2") != winner:
        return None, "winner_mismatch"
    got = [(a, b) if winner == "1" else (b, a) for a, b in sets]
    if got != want:
        return None, "score_mismatch"
    try:
        day = dt.datetime.strptime(row["date"], "%d %b %y").date()
    except (KeyError, ValueError):
        return None, "bad_date"
    return MatchPoints(
        key=row["pbp_id"], source="pbp", level=level, year=day.year, date=day.isoformat(),
        event=row.get("tny_name", ""), name1=row["server1"], name2=row["server2"],
        games=games,
    ), "ok"


def load_pbp(pbp_dir) -> Tuple[List[MatchPoints], Counter]:
    """Every ATP and Challenger match under `pbp_dir` that passes validation,
    one per `pbp_id`; the Counter says why the rest were dropped."""
    out, why, seen = [], Counter(), set()
    files = sorted(glob.glob(os.path.join(str(pbp_dir), "pbp_matches_*.csv")))
    if not files:
        raise FileNotFoundError(f"no pbp_matches_*.csv under {pbp_dir}; "
                                "python -m tennis.eval download data/sackmann")
    for f in files:
        with open(f, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["pbp_id"] in seen:
                    why["duplicate_id"] += 1
                    continue
                seen.add(row["pbp_id"])
                m, reason = match_from_row(row)
                why[reason] += 1
                if m is not None:
                    out.append(m)
    return out, why
