"""Command line for the ratings job.

  python -m tennis.ratings download data/sackmann/atp
  python -m tennis.ratings build data/sackmann/atp --out data/ratings/atp.json.gz
  python -m tennis.ratings evaluate data/sackmann/atp [--config research-b1]
  python -m tennis.ratings prior data/ratings/atp.json.gz "Sinner" "Alcaraz" \\
      --surface Hard --best-of 5 --as-of 2026-09-22
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import numpy as np

from tennis.ratings.config import CONFIGS
from tennis.ratings.evaluate import evaluate, format_report
from tennis.ratings.sackmann import download, fingerprint, load_matches
from tennis.ratings.snapshot import RatingsSnapshot


# Older than this, a prior is flagged: a month of tennis moves ratings.
STALE_DAYS = 30


def _player(snap, name):
    if name.isdigit():
        return int(name)
    ids = snap.find(name)
    if len(ids) != 1:
        found = ", ".join(f"{snap.names[p]} ({p})" for p in ids[:10]) or "nobody"
        raise SystemExit(f"{name!r} matches {len(ids)} players: {found}")
    return ids[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tennis.ratings", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("download", help="fetch the Sackmann files from the mirror")
    d.add_argument("atp_dir")

    b = sub.add_parser("build", help="replay the match files into a snapshot")
    b.add_argument("atp_dir")
    b.add_argument("--out", required=True, help=".json or .json.gz")
    b.add_argument("--config", choices=sorted(CONFIGS), default="sweep")
    b.add_argument("--as-of", help="only tournaments that started before this date")

    e = sub.add_parser("evaluate", help="RMSE of the prior, as in experiment B1")
    e.add_argument("atp_dir")
    e.add_argument("--config", choices=sorted(CONFIGS), default="sweep")
    e.add_argument("--train-until", type=int, default=2021)
    e.add_argument("--json", help="also write the full result here")

    p = sub.add_parser("prior", help="the prior for one match from a snapshot")
    p.add_argument("snapshot")
    p.add_argument("a", help="player A: Sackmann id or (part of) a name")
    p.add_argument("b", help="player B")
    p.add_argument("--surface", required=True)
    p.add_argument("--best-of", type=int, default=3)
    p.add_argument("--as-of", required=True, help="the tournament's start date")

    args = ap.parse_args(argv)
    t0 = time.monotonic()

    if args.cmd == "download":
        got = download(args.atp_dir)
        print(f"fetched {len(got)} files into {args.atp_dir} "
              f"({time.monotonic() - t0:.0f} s); the rest were already there")
    elif args.cmd == "build":
        m = load_matches(args.atp_dir)
        snap = RatingsSnapshot.build(m, CONFIGS[args.config], as_of=args.as_of,
                                     sources=fingerprint(args.atp_dir))
        snap.save(args.out)
        print(f"{snap.n_matches} matches through {snap.last_date}; "
              f"{len(snap.elo.count)} rated players, {len(snap.serve)} window entries kept; "
              f"wrote {args.out} in {time.monotonic() - t0:.0f} s")
    elif args.cmd == "evaluate":
        m = load_matches(args.atp_dir)
        res = evaluate(m, CONFIGS[args.config], train_until=args.train_until)
        print(format_report(res))
        if args.json:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump(res, fh, indent=1)
        print(f"({time.monotonic() - t0:.0f} s)")
    else:
        snap = RatingsSnapshot.load(args.snapshot)
        a, b_ = _player(snap, args.a), _player(snap, args.b)
        pr = snap.prior(a, b_, args.surface, args.best_of, args.as_of)
        na, nb = snap.names.get(a, str(a)), snap.names.get(b_, str(b_))
        print(f"{na} vs {nb}, {args.surface}, best of {args.best_of}, as of {args.as_of} "
              f"(snapshot through {snap.last_date})")
        print(f"  Elo: {pr.elo_a:.0f} vs {pr.elo_b:.0f} ({pr.matches_a} and {pr.matches_b} "
              f"rated matches), P({na} wins) = {pr.win_prob_a:.3f}")
        print(f"  serve prior: {na} {pr.p_serve_a:.4f}, {nb} {pr.p_serve_b:.4f} "
              f"(Elo {pr.p_elo_a:.4f}/{pr.p_elo_b:.4f}, Barnett-Clarke "
              f"{pr.p_bc_a:.4f}/{pr.p_bc_b:.4f}, baseline {pr.baseline:.4f})")
        print(f"  serve points in the window: {pr.window_points_a:.0f} and {pr.window_points_b:.0f}")
        stale = int((np.datetime64(args.as_of, "D") - np.datetime64(snap.last_date, "D"))
                    / np.timedelta64(1, "D"))
        if stale > STALE_DAYS:
            print(f"  WARNING: the snapshot is {stale} days older than the match: every result "
                  f"since {snap.last_date} is missing from the ratings and the windows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
