"""Jeff Sackmann's ATP match files, read the way experiment B1 read them.

The original repositories were deleted from GitHub in June-July 2026; the
mirror `Aneeshers/tennis-sackmann-archive` carries tour and qualifying/
Challenger files 2000-2026, last tournament dated 2026-06-01. CC BY-NC-SA 4.0:
research use only, never committed here. Download commands are in
docs/EXPERIMENT_B_elo_prior_and_process.md.

Parsing and ordering follow `research/elo_prior.py:load_matches` exactly,
because the order of matches is part of what an Elo rating is: files in sorted
name order (so every tour file precedes every qualifying/Challenger file), rows
without a date or either player id dropped, then a stable sort on
(tournament date, match number) with a missing match number last. Sackmann
stamps every match of a tournament with the tournament's start date, so
`date` here is that start date, never the day the match was played.

No pandas: the engine runs on numpy alone.
"""
from __future__ import annotations

import csv
import glob
import hashlib
import math
import os
from dataclasses import dataclass, fields
from typing import Iterable, Mapping

import numpy as np

STATS = ("w_svpt", "w_1stWon", "w_2ndWon", "l_svpt", "l_1stWon", "l_2ndWon")

MIRROR = "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main/atp"
YEARS = range(2000, 2027)


@dataclass
class Matches:
    """One row per match, in the order ratings consume them. Columns are arrays."""
    date: np.ndarray            # datetime64[D], tournament start date
    tourney_id: np.ndarray      # str
    tourney_level: np.ndarray   # str: G, M, A, C, D, F, ... ('' when missing)
    match_num: np.ndarray       # float, nan when missing
    winner_id: np.ndarray       # int64
    loser_id: np.ndarray        # int64
    winner_name: np.ndarray     # str
    loser_name: np.ndarray      # str
    surface: np.ndarray         # str, 'Hard' when missing
    best_of: np.ndarray         # int64, 3 when missing
    src: np.ndarray             # 'tour' or 'chall_qual'
    w_svpt: np.ndarray          # float, nan when missing (same for the five below)
    w_1stWon: np.ndarray
    w_2ndWon: np.ndarray
    l_svpt: np.ndarray
    l_1stWon: np.ndarray
    l_2ndWon: np.ndarray
    match_key: np.ndarray       # str, "<tourney_id>#<position>": unique per match

    def __len__(self) -> int:
        return len(self.date)

    def take(self, idx) -> "Matches":
        """A subset, keeping each match's original key and the given order."""
        return Matches(**{f.name: getattr(self, f.name)[idx] for f in fields(self)})

    def before(self, as_of) -> "Matches":
        """Matches of tournaments that started strictly before `as_of`."""
        return self.take(np.flatnonzero(self.date < np.datetime64(as_of, "D")))


# pandas.read_csv's default missing-value tokens. research/elo_prior.py read the
# files with pandas, so a surface spelled "NA" was a missing surface there.
_NA = {"", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan",
       "1.#IND", "1.#QNAN", "<NA>", "N/A", "NA", "NULL", "NaN", "None", "n/a",
       "nan", "null"}


def _str(s) -> str:
    """A text cell, '' when pandas would have read it as missing."""
    if s is None:
        return ""
    s = str(s)
    return "" if s.strip() in _NA else s


def _num(s) -> float:
    """pandas.to_numeric(errors='coerce') for one CSV cell."""
    if s is None:
        return math.nan
    if isinstance(s, (int, float)):
        return float(s)
    s = s.strip()
    if not s:
        return math.nan
    try:
        return float(s)
    except ValueError:
        return math.nan


def _date(s):
    """%Y%m%d, tolerating the '.0' a float column leaves; None when invalid."""
    if s is None:
        return None
    s = str(s).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return np.datetime64(f"{s[:4]}-{s[4:6]}-{s[6:]}", "D")
    except ValueError:
        return None


def _player(s):
    v = _num(s)
    if math.isnan(v):
        return None
    if v != int(v):
        raise ValueError(f"non-integer player id {s!r}")
    return int(v)


def from_records(records: Iterable[Mapping]) -> Matches:
    """Build `Matches` from raw rows (CSV dicts or test data), parsed and ordered
    exactly as the files are. Each record needs a `src`; the rest are
    Sackmann's column names."""
    rows = []
    for r in records:
        d = _date(r.get("tourney_date"))
        w = _player(r.get("winner_id"))
        lo = _player(r.get("loser_id"))
        if d is None or w is None or lo is None:
            continue
        surface = _str(r.get("surface")) or "Hard"
        best_of = _num(r.get("best_of"))
        rows.append((
            d, _str(r.get("tourney_id")) or "nan", _str(r.get("tourney_level")),
            _num(r.get("match_num")), w, lo,
            _str(r.get("winner_name")), _str(r.get("loser_name")),
            surface, 3 if math.isnan(best_of) else int(best_of), r["src"],
            *(_num(r.get(c)) for c in STATS),
        ))
    # Stable: equal (date, match_num) keep file order, and a missing match
    # number sorts after every present one on the same date.
    order = sorted(range(len(rows)), key=lambda i: (
        rows[i][0], math.isnan(rows[i][3]), 0.0 if math.isnan(rows[i][3]) else rows[i][3]))
    rows = [rows[i] for i in order]
    cols = list(zip(*rows)) if rows else [()] * 17

    def arr(i, dtype):
        return np.array(cols[i], dtype=dtype) if rows else np.array([], dtype=dtype)

    tid = arr(1, object)
    return Matches(
        date=arr(0, "datetime64[D]"), tourney_id=tid, tourney_level=arr(2, object),
        match_num=arr(3, float), winner_id=arr(4, np.int64), loser_id=arr(5, np.int64),
        winner_name=arr(6, object), loser_name=arr(7, object), surface=arr(8, object),
        best_of=arr(9, np.int64), src=arr(10, object),
        w_svpt=arr(11, float), w_1stWon=arr(12, float), w_2ndWon=arr(13, float),
        l_svpt=arr(14, float), l_1stWon=arr(15, float), l_2ndWon=arr(16, float),
        match_key=np.array([f"{t}#{i}" for i, t in enumerate(tid)], dtype=object),
    )


def match_files(atp_dir) -> list[str]:
    """The files experiment B1 read: atp_matches_*.csv, futures excluded."""
    return [f for f in sorted(glob.glob(os.path.join(str(atp_dir), "atp_matches_*.csv")))
            if "futures" not in f]


def load_matches(atp_dir) -> Matches:
    """Every tour and qualifying/Challenger match under `atp_dir`."""
    files = match_files(atp_dir)
    if not files:
        raise FileNotFoundError(
            f"no atp_matches_*.csv under {atp_dir}; download commands are in "
            "docs/EXPERIMENT_B_elo_prior_and_process.md")

    def records():
        for f in files:
            src = "chall_qual" if "qual_chall" in f else "tour"
            with open(f, newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    r["src"] = src
                    yield r
    return from_records(records())


def mirror_files(years=YEARS) -> list[str]:
    """The files experiment B1 was run on: tour and qualifying/Challenger, 2000-2026."""
    return [name for y in years
            for name in (f"atp_matches_{y}.csv", f"atp_matches_qual_chall_{y}.csv")]


def download(atp_dir, years=YEARS, base: str = MIRROR, opener=None) -> list[str]:
    """Fetch the mirror's files into `atp_dir`, skipping those already there.

    The same files as the bash loop in docs/EXPERIMENT_B, but runnable from
    PowerShell. Returns the names fetched. A file is written only once fully
    received, so an interrupted run leaves no truncated CSV behind.
    """
    import urllib.request
    opener = opener or urllib.request.urlopen
    os.makedirs(str(atp_dir), exist_ok=True)
    got = []
    for name in mirror_files(years):
        path = os.path.join(str(atp_dir), name)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            continue
        with opener(f"{base}/{name}") as resp:
            data = resp.read()
        if not data.startswith(b"tourney_id"):
            raise ValueError(f"{name}: not a Sackmann match file ({data[:40]!r})")
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        got.append(name)
    return got


def fingerprint(atp_dir) -> dict:
    """sha256 of every input file, so a snapshot says exactly what it was built from."""
    out = {}
    for f in match_files(atp_dir):
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        out[os.path.basename(f)] = h.hexdigest()
    return out
