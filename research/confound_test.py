"""Do process features carry signal, or do they stand in for a missing player prior?

Women's games in the slam feed have no ATP prior attached, and that is exactly where process
features looked useful. Test: strip the Elo prior from the MEN's games too. If process features
turn positive there, they are proxying for player identity, not measuring today's state.
"""
import sys, os, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import slam_process_study as S

G = pd.read_pickle('slam/_slam_games.pkl')
G = S.attach_prior(G, 'priors.csv'); G = S.add_features(G); G = G[G["has_prev"] == 1]
men = G[G["p_blend"].notna()].copy()

for label, prior_cols in [("men WITH Elo prior", ["p_prior", "hist_hold", "surf_grass", "surf_clay"]),
                          ("men WITHOUT any prior", ["surf_grass", "surf_clay"]),
                          ("men, crude hist prior only", ["hist_hold", "surf_grass", "surf_clay"])]:
    tr, te = men[men["year"] < 2019], men[men["year"] >= 2019]
    base = prior_cols + S.GROUPS["B_live_cum"]
    _, _, p_base = S.fit_eval(tr, te, base)
    _, _, p_proc = S.fit_eval(tr, te, base + S.GROUPS["D_prev_proc"] + S.GROUPS["F_cum_proc"])
    g = S.boot_gain(te, p_base, p_proc)
    ll = lambda p: float(-(te["hold"]*np.log(np.clip(p,1e-6,1)) + (1-te["hold"])*np.log(np.clip(1-p,1e-6,1))).mean())
    print(f"{label:28s} base LL={ll(p_base):.4f}  +process LL={ll(p_proc):.4f}  "
          f"gain={g['mean']:+.5f} CI [{g['ci95'][0]:+.5f}, {g['ci95'][1]:+.5f}]", flush=True)
