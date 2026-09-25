"""Fetch the point-by-point files the live-state evaluation runs on.

Two sources, both Jeff Sackmann's, CC BY-NC-SA 4.0 -- research use only, and
they land under `data/`, which git ignores:

* Grand Slam point by point, 2012-2024, from the mirror that also carries the
  ATP match files (`Aneeshers/tennis-sackmann-archive`). Experiment B2 ran on
  these, so its +0.0030 is the reference the live state is checked against.
* `tennis_pointbypoint`, ATP and Challenger, main draw and qualifying,
  mostly 2012-2015, from the fork `vinhonrubia/tennis_pointbypoint` (the
  original repository was deleted with the others in June-July 2026). This is
  the segment the project targets: tour and Challenger.

The mirror has no file for a Slam that was not played or not recorded (2020
Wimbledon, for one), so a 404 is recorded as missing rather than raised.
"""
from __future__ import annotations

import os
import urllib.error

SLAM_BASE = "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main/slam_pointbypoint"
PBP_BASE = "https://raw.githubusercontent.com/vinhonrubia/tennis_pointbypoint/master"

SLAMS = ("ausopen", "frenchopen", "wimbledon", "usopen")
SLAM_YEARS = range(2012, 2025)
PBP_FILES = tuple(f"pbp_matches_{tour}_{draw}_{era}.csv"
                  for tour in ("atp", "ch") for draw in ("main", "qual")
                  for era in ("archive", "current"))


def slam_files(years=SLAM_YEARS) -> list[str]:
    return [f"{y}-{s}-{kind}.csv" for y in years for s in SLAMS
            for kind in ("matches", "points")]


def fetch(names, base: str, out_dir, header: bytes, opener=None) -> tuple[list[str], list[str]]:
    """Download `names` from `base` into `out_dir`, skipping files already there.

    Returns (fetched, missing). A file is written only once fully received, so
    an interrupted run leaves no truncated CSV behind; anything that does not
    start with `header` is refused rather than saved.
    """
    import urllib.request
    opener = opener or urllib.request.urlopen
    os.makedirs(str(out_dir), exist_ok=True)
    got, missing = [], []
    for name in names:
        path = os.path.join(str(out_dir), name)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            continue
        try:
            with opener(f"{base}/{name}") as resp:
                data = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                missing.append(name)
                continue
            raise
        if not data.lstrip(b"\xef\xbb\xbf").startswith(header):
            raise ValueError(f"{name}: unexpected content ({data[:40]!r})")
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        got.append(name)
    return got, missing


def download_slam(out_dir, years=SLAM_YEARS, opener=None):
    return fetch(slam_files(years), SLAM_BASE, out_dir, b"match_id", opener)


def download_pbp(out_dir, opener=None):
    return fetch(PBP_FILES, PBP_BASE, out_dir, b"pbp_id", opener)
