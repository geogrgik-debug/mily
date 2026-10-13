"""Synthetic match files in Sackmann's format, for tests that cannot ship the real ones.

Players have a latent serve and return skill; weekly events of every level on
every surface; results and serve totals drawn from the skills. Awkward in the
ways the real files are: a Slam overlapping the events of its second week,
matches without stats, a missing surface, match number or best_of, and two
files per year (tour, qualifying/Challenger), so file order matters.

One thing is kept out on purpose: a player in two events starting on the same
date. The original ordered those with an unstable sort, so no port can match
it there; tests/test_serve.py covers that case on its own.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import random

COLUMNS = ["tourney_id", "tourney_name", "surface", "draw_size", "tourney_level",
           "tourney_date", "match_num", "winner_id", "winner_name", "loser_id",
           "loser_name", "score", "best_of", "round", "w_svpt", "w_1stWon", "w_2ndWon",
           "l_svpt", "l_1stWon", "l_2ndWon"]
STATS = ("w_svpt", "w_1stWon", "w_2ndWon", "l_svpt", "l_1stWon", "l_2ndWon")
SURFACES = ["Hard", "Clay", "Grass", "Hard", "Clay", "Hard", "Carpet"]


def _serve(rng, points, p):
    first_in = round(points * 0.6)
    won1 = round(first_in * min(0.95, p + 0.12))
    won2 = round((points - first_in) * max(0.05, p - 0.10))
    return won1, won2


def generate(seed: int = 7, years=range(2019, 2024), n_players: int = 48, weeks: int = 50):
    """Rows as CSV dicts (all values strings, as a file gives them), each with `src`."""
    rng = random.Random(seed)
    skill = {100000 + i: (0.56 + 0.12 * rng.random(), 0.30 + 0.10 * rng.random())
             for i in range(n_players)}
    ids = sorted(skill)
    rows = []
    event = 0
    for year in years:
        jan1 = dt.date(year, 1, 1)
        monday = jan1 + dt.timedelta(days=(7 - jan1.weekday()) % 7)
        for week in range(weeks):
            start = monday + dt.timedelta(weeks=week)
            surface = SURFACES[(week // 4) % len(SURFACES)]
            level_tour = "G" if week % 13 == 0 else ("M" if week % 6 == 0 else "A")
            field = rng.sample(ids, 32)            # disjoint draws: one event per player per date
            for level, src, entrants in ((level_tour, "tour", field[:16]),
                                         ("C", "chall_qual", field[16:])):
                event += 1
                best_of = 5 if level == "G" else 3
                alive, num, rnd = list(entrants), 0, 0
                while len(alive) > 1:
                    rnd += 1
                    nxt = []
                    for a, b in zip(alive[0::2], alive[1::2]):
                        num += 1
                        pa = skill[a][0] - skill[b][1] + 0.35
                        pb = skill[b][0] - skill[a][1] + 0.35
                        w, l = (a, b) if rng.random() < 0.5 + 2.5 * (pa - pb) else (b, a)
                        pw, pl = (pa, pb) if w == a else (pb, pa)
                        wsv, lsv = rng.randint(45, 110), rng.randint(45, 110)
                        w1, w2 = _serve(rng, wsv, pw)
                        l1, l2 = _serve(rng, lsv, pl)
                        row = {
                            "tourney_id": f"{year}-{event:04d}", "tourney_name": f"Event {event}",
                            "surface": surface, "draw_size": "16", "tourney_level": level,
                            "tourney_date": start.strftime("%Y%m%d"), "match_num": str(num),
                            "winner_id": str(w), "winner_name": f"Player {w - 100000:02d}",
                            "loser_id": str(l), "loser_name": f"Player {l - 100000:02d}",
                            "score": "6-4 6-4", "best_of": str(best_of), "round": f"R{rnd}",
                            "w_svpt": str(wsv), "w_1stWon": str(w1), "w_2ndWon": str(w2),
                            "l_svpt": str(lsv), "l_1stWon": str(l1), "l_2ndWon": str(l2),
                            "src": src,
                        }
                        r = rng.random()
                        if r < 0.06:
                            for c in STATS:
                                row[c] = ""
                        elif r < 0.07:
                            row["surface"] = ""
                        elif r < 0.075:
                            row["best_of"] = ""
                        elif r < 0.08:
                            row["match_num"] = ""
                        rows.append(row)
                        nxt.append(w)
                    alive = nxt
    return rows


def write_files(directory, rows) -> None:
    """One tour and one qualifying/Challenger file per year, as the mirror has them."""
    by_file = {}
    for r in rows:
        year = r["tourney_date"][:4]
        name = (f"atp_matches_qual_chall_{year}.csv" if r["src"] == "chall_qual"
                else f"atp_matches_{year}.csv")
        by_file.setdefault(name, []).append(r)
    os.makedirs(directory, exist_ok=True)
    for name, rs in by_file.items():
        with open(os.path.join(directory, name), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rs)
