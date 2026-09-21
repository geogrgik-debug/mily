"""Do process features carry signal, or do they stand in for a missing player prior?

Strip the prior from the men's games and see whether process features turn positive. The
published version of this test was flawed twice over: the "no prior" condition still carried
the Elo blend through live_spw_dev, and the women's comparison it was explaining rested on a
sample that had 762 men's matches in it.
"""
import sys, os, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import slam_process_study as S

G = pd.read_pickle('slam/_slam_games.pkl')
G = S.attach_prior(G, sys.argv[1] if len(sys.argv) > 1 else 'priors_fixed.csv')
G = S.add_features(G); G = G[G["has_prev"] == 1]
men = G[G["draw"] == "M"].copy()

# live_spw_dev is shrunk toward p_prior and then has p_prior subtracted, so the published
# "no prior" condition still carried the Elo blend through the live block. Build a
# genuinely prior-free version shrunk toward a fixed tour constant instead.
C = 0.62
men["live_spw_free"] = (men["cum_won"] + 40 * C) / (men["cum_pts"] + 40) - C
LIVE_FREE = ["live_spw_free", "cum_pts", "live_hold_rate"]
PROC = S.GROUPS["D_prev_proc"] + S.GROUPS["F_cum_proc"]

tr, te = men[men["year"] < 2019], men[men["year"] >= 2019]
print(f"men's games: train {len(tr)}, test {len(te)} ({te['match_id'].nunique()} matches)\n")
print(f"{'condition':44s} {'base':>8s} {'+process':>9s} {'gain':>10s}  95% CI")
for label, cols in [
        ("with Elo prior",                    S.GROUPS["A_prior"] + S.GROUPS["B_live_cum"]),
        ("with Elo prior and returner side",  S.GROUPS["A_prior"] + S.GROUPS["B_live_cum"] + S.GROUPS["B2_live_opp"]),
        ("crude slam-history prior only",     ["hist_hold", "surf_grass", "surf_clay"] + LIVE_FREE),
        ("no prior at all (prior-free live)", ["surf_grass", "surf_clay"] + LIVE_FREE),
]:
    _, _, pb = S.fit_eval(tr, te, cols)
    _, _, pp = S.fit_eval(tr, te, cols + PROC)
    g = S.boot_gain(te, pb, pp)
    ll = lambda p: float(-(te["hold"] * np.log(np.clip(p, 1e-6, 1)) +
                           (1 - te["hold"]) * np.log(np.clip(1 - p, 1e-6, 1))).mean())
    print(f"{label:44s} {ll(pb):8.4f} {ll(pp):9.4f} {g['mean']:+10.5f}  "
          f"[{g['ci95'][0]:+.5f}, {g['ci95'][1]:+.5f}]")
