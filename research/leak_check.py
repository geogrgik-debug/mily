"""Quantify the overlapping-tournament leak in the as-of prior window.

The window keeps matches whose tournament STARTED strictly earlier. A 14-day event that
started earlier is still being played while a later event runs, so its late-round matches
can land in the prior of a match played before them. Measure how often and how far.
"""
import sys, os, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from elo_prior import load_matches

m = load_matches('atp')
# tournament span: Sackmann gives only the start date, so infer the span from the draw size
# and level; Slams and Masters run 12-14 days, ordinary events 7.
span = np.where(m["tourney_level"].isin(["G"]), 14,
        np.where(m["tourney_level"].isin(["M"]) & (m["draw_size"] >= 64), 12, 7))
m = m.assign(span=span)
t = (m.groupby("tourney_id")
       .agg(start=("date", "first"), span=("span", "first"), level=("tourney_level", "first"),
            n=("match_num", "size"))
       .reset_index())
t["end"] = t["start"] + pd.to_timedelta(t["span"], unit="D")
t = t.sort_values("start")

# For every tournament, how many other tournaments started earlier but end later?
starts = t["start"].to_numpy(); ends = t["end"].to_numpy()
overlap_pairs, affected = 0, 0
for i in range(len(t)):
    earlier = starts < starts[i]
    still_running = ends > starts[i]
    k = int((earlier & still_running).sum())
    overlap_pairs += k
    if k: affected += 1
print(f"tournaments: {len(t)}")
print(f"tournaments with at least one earlier-but-still-running event: {affected} "
      f"({affected/len(t):.1%})")
print(f"total overlapping ordered pairs: {overlap_pairs}")

# Which share of MATCHES sits in a tournament whose prior could contain future results,
# and how much of the potentially leaking material is there?
tt = t.set_index("tourney_id")
m2 = m.join(tt[["start","end"]], on="tourney_id")
lvl = m2.groupby("tourney_level")["match_num"].size()
rows = []
for lev, d in m2.groupby("tourney_level"):
    sub = t[t["level"] == lev]
    n_aff = 0
    for s in d["date"].unique():
        n_aff += int(((starts < s) & (ends > s)).sum() > 0) * (d["date"] == s).sum()
    rows.append((lev, len(d), n_aff, n_aff / len(d)))
print(f"\n{'level':>6} {'matches':>9} {'in overlap':>11} {'share':>7}")
for lev, n, na, sh in sorted(rows, key=lambda r: -r[1]):
    print(f"{lev:>6} {n:9d} {na:11d} {sh:7.1%}")

# The quantity that actually matters: for a given match, how many matches in its prior
# window were physically played AFTER it started?
m2 = m2.sort_values("date").reset_index(drop=True)
rng = np.random.default_rng(0)
samp = m2.sample(min(4000, len(m2)), random_state=0)
future_frac = []
for r in samp.itertuples():
    # matches from tournaments that started earlier and were still running at r.date
    cand = m2[(m2["start"] < r.date) & (m2["end"] > r.date)]
    if len(cand) == 0:
        future_frac.append(0.0); continue
    window = m2[(m2["date"] >= r.date - pd.Timedelta(days=366)) & (m2["date"] < r.date)]
    if len(window) == 0:
        future_frac.append(0.0); continue
    # conservative upper bound: half of an overlapping event's matches fall after this date
    leak = len(cand[cand["date"] < r.date]) * 0.5
    future_frac.append(leak / len(window))
ff = np.array(future_frac)
print(f"\nupper bound on the share of a prior window that may be future material:")
print(f"  mean {ff.mean():.4f}, median {np.median(ff):.4f}, p95 {np.percentile(ff,95):.4f}, max {ff.max():.4f}")
