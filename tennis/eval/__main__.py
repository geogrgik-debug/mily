"""Command line for the offline evaluation of the live state.

  python -m tennis.eval download data/sackmann
  python -m tennis.eval live-state [--data data/sackmann] [--json out.json] [--n-boot 1000]
  python -m tennis.eval calibrate [--data data/sackmann] [--json out.json] [--params PATH]

`download` fetches the ATP match files (for the prior), Grand Slam point by
point 2012-2024 and tennis_pointbypoint (ATP, Challenger) under `--data`.
`live-state` fits the prior strength n0 on earlier years and measures, on later
ones, what updating the serve belief point by point adds to the prior alone.
Three segments, each fitted and scored on its own:

  slam   Grand Slam men, train 2012-2018, test 2019-2024 (experiment B2's split)
  tour   tennis_pointbypoint ATP main draw and qualifying, train 2011-2015, test 2017
  chall  tennis_pointbypoint Challenger main draw, train 2011-2015, test 2017

`calibrate` fits the context residual and the beta calibration on top of the
live state (`tennis.model.hold`), one model for the three levels, on years
before 2017, scores it on 2017 and later, and writes the parameters the live
process loads (`tennis/model/hold_v1.json`). Slam main-draw matches are taken
from the Slam files only: tennis_pointbypoint carries them too, and one match
in two sources could sit in the fit and the test at once.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from tennis.eval.calibrate import build_rows, evaluate, format_report
from tennis.eval.download import download_pbp, download_slam
from tennis.eval.join import join_pbp, join_slam, price, stream_priors
from tennis.eval.live_state import evaluate_segment
from tennis.eval.pbp import load_pbp
from tennis.eval.slam import load_slam
from tennis.model.hold import PARAMS_PATH
from tennis.ratings.sackmann import download as download_atp, load_matches

SEGMENTS = {
    "slam": (lambda m: m.source == "slam", 2018, 2019),
    "tour": (lambda m: m.source == "pbp" and m.level == "tour", 2015, 2017),
    "chall": (lambda m: m.source == "pbp" and m.level == "chall", 2015, 2017),
}


def _pct(x: float) -> str:
    return f"{100 * x:.0f} %"


def _gain(g: dict) -> str:
    return f"{g['mean']:+.4f} ({g['ci'][0]:+.4f} .. {g['ci'][1]:+.4f})"


def format_segment(name: str, r: dict) -> str:
    fit, pfit = r["n0_games"], r["n0_points"]
    t, a = r["test"]["has_prev"], r["test"]["all"]
    lines = [
        f"== {name}: train {r['matches']['train']} matches, {r['games']['train']} games "
        f"(hold {r['hold_rate']['train']:.3f}); test {r['matches']['test']} matches",
        f"n0 by hold log loss (train): {fit['n0']:g}, 95 % {fit['ci'][0]:g}-{fit['ci'][1]:g}"
        f"   by point likelihood: {pfit['n0']:.0f}",
        "carry-over n/(n+n0): " + ", ".join(f"{x['points']} pts {_pct(x['weight'])}"
                                            for x in r["transfer"]) + "   (audit A.5: 16-27 %)",
        "split-half (train): " + "; ".join(
            f"k={s['k']} ({s['points']:.0f} pts) slope {s['slope']:.2f}+-{s['se']:.2f} "
            f"-> n0 {s['implied_n0']:.0f}" for s in r["split_half"]),
        f"serve link (train, {r['serve_link']['n']} matches): rho {r['serve_link']['rho']:+.3f} "
        f"({r['serve_link']['ci'][0]:+.3f} .. {r['serve_link']['ci'][1]:+.3f}), "
        f"raw corr {r['serve_link']['raw_corr']:+.3f}",
        f"test, server has served before ({r['games']['test_has_prev']} games, hold {t['hold_rate']:.3f}):",
        "  log loss " + "  ".join(f"{k} {v:.4f}" for k, v in t["logloss"].items()),
    ]
    lines += [f"  gain {k}: {_gain(v)}" for k, v in t["gain"].items()]
    lines.append(f"  gain prior->live after Platt on train: {_gain(r['recalibrated']['gain'])}")
    lines.append(f"test, all games ({r['games']['test_all']}): gain prior->live "
                 f"{_gain(a['gain']['prior->live'])}")
    lines.append("  by the server's rated matches: " + "; ".join(
        f"{b['rated_matches'][0]}-{b['rated_matches'][1]}: {_gain(b['gain'])}" for b in r["by_rated"]))
    lines.append("  calibration live (pred/obs): " + " ".join(
        f"{p:.2f}/{o:.2f}" for p, o, _ in r["calibration"]["live"]))
    lines.append("  calibration prior (pred/obs): " + " ".join(
        f"{p:.2f}/{o:.2f}" for p, o, _ in r["calibration"]["prior"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tennis.eval", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="fetch the ATP, Slam and pointbypoint files")
    d.add_argument("data", nargs="?", default="data/sackmann")
    e = sub.add_parser("live-state", help="fit n0 and measure the live state's gain")
    e.add_argument("--data", default="data/sackmann")
    e.add_argument("--json", help="also write every number here")
    e.add_argument("--n-boot", type=int, default=1000)
    e.add_argument("--segments", default=",".join(SEGMENTS))
    c = sub.add_parser("calibrate", help="fit the context residual and the calibration, score them")
    c.add_argument("--data", default="data/sackmann")
    c.add_argument("--json", help="also write every number here")
    c.add_argument("--params", default=PARAMS_PATH, help="where the fitted parameters go")
    c.add_argument("--n-boot", type=int, default=1000)
    c.add_argument("--n-boot-coef", type=int, default=200,
                   help="refits of the residual for the coefficients' intervals")
    args = ap.parse_args(argv)
    t0 = time.monotonic()

    if args.cmd == "download":
        got = download_atp(os.path.join(args.data, "atp"))
        print(f"atp: fetched {len(got)}")
        got, missing = download_slam(os.path.join(args.data, "slam"))
        print(f"slam: fetched {len(got)}, not on the mirror {len(missing)}: {', '.join(missing)}")
        got, missing = download_pbp(os.path.join(args.data, "pointbypoint"))
        print(f"pointbypoint: fetched {len(got)}, missing {len(missing)}")
        return 0

    sack, js, jp, priors, out = _load(args.data, t0)
    if args.cmd == "calibrate":
        return _calibrate(args, sack, js, jp, priors, out, t0)

    priced = price(js + jp, sack, priors)
    out["segments"] = {}
    for name in args.segments.split(","):
        pick, last_train, first_test = SEGMENTS[name]
        seg = [pm for pm in priced if pick(pm.match)]
        r = evaluate_segment(seg, lambda pm: pm.match.year <= last_train,
                             lambda pm: pm.match.year >= first_test, n_boot=args.n_boot)
        out["segments"][name] = r
        print(format_segment(name, r) + f"\n({time.monotonic() - t0:.0f} s)\n", flush=True)
    _write_json(args.json, out)
    return 0


def _load(data: str, t0: float):
    sack = load_matches(os.path.join(data, "atp"))
    slam, why_slam = load_slam(os.path.join(data, "slam"))
    pbp, why_pbp = load_pbp(os.path.join(data, "pointbypoint"))
    js, jwhy_slam = join_slam(slam, sack)
    jp, jwhy_pbp = join_pbp(pbp, sack)
    priors = stream_priors(sack, [j.row for j in js + jp])
    print(f"slam read {dict(why_slam)}, joined {dict(jwhy_slam)}")
    print(f"pointbypoint read {dict(why_pbp)}, joined {dict(jwhy_pbp)}")
    print(f"priors for {len(priors)} matches ({time.monotonic() - t0:.0f} s)\n", flush=True)
    out = {"read": {"slam": dict(why_slam), "pbp": dict(why_pbp)},
           "joined": {"slam": dict(jwhy_slam), "pbp": dict(jwhy_pbp)}}
    return sack, js, jp, priors, out


def _calibrate(args, sack, js, jp, priors, out, t0: float) -> int:
    no_slams = [j for j in jp if sack.tourney_level[j.row] != "G"]
    shared = {j.row for j in js} & {j.row for j in no_slams}
    if shared:
        raise AssertionError(f"{len(shared)} matches are in both sources")
    out["pbp_slams_dropped"] = len(jp) - len(no_slams)
    print(f"pointbypoint: {out['pbp_slams_dropped']} Slam main-draw matches left to the Slam files")
    rows = build_rows(price(js + no_slams, sack, priors))
    print(f"{len(rows.hold)} service games ({time.monotonic() - t0:.0f} s)", flush=True)
    params, report = evaluate(rows, n_boot=args.n_boot, n_boot_coef=args.n_boot_coef)
    out["calibrate"] = report
    print(format_report(report) + f"\n({time.monotonic() - t0:.0f} s)\n")
    params.save(args.params)
    print(f"wrote {args.params}")
    _write_json(args.json, out)
    return 0


def _write_json(path, out) -> None:
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
        print(f"wrote {path}")


if __name__ == "__main__":
    sys.exit(main())
