"""Does the Elo prior lose to Barnett-Clarke because the Elo itself is under-tuned?
Sweeps K-shape and surface weight, refits the blend weight on train, reports test RMSE."""
import sys, os, json, itertools, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from elo_prior import load_matches, build_elo, rolling_priors, invert

m0 = load_matches('atp')
print("matches", len(m0), flush=True)
long, R, tour_spw = rolling_priors(m0)
bl = long.groupby([long["date"].dt.year, "surface"]).apply(
    lambda d: d["spw_k"].sum()/d["spw_n"].sum()).rename("baseline").reset_index()
bl.columns = ["year","surface","baseline"]

def player_rows(m):
    cols = ["date","year","surface","best_of","match_key","pid","oid","win_prob","svpt","w1","w2","src"]
    a = m[["date","year","surface","best_of","match_key","winner_id","loser_id","elo_exp_w",
           "w_svpt","w_1stWon","w_2ndWon","src"]].copy(); a.columns = cols
    b = m[["date","year","surface","best_of","match_key","loser_id","winner_id","elo_exp_w",
           "l_svpt","l_1stWon","l_2ndWon","src"]].copy(); b.columns = cols
    b["win_prob"] = 1.0 - b["win_prob"]
    P = pd.concat([a,b], ignore_index=True)
    P["spw_actual"] = (P["w1"]+P["w2"])/P["svpt"]
    P = P.dropna(subset=["spw_actual"]); P = P[P["svpt"] >= 30]
    P = P.merge(bl, on=["year","surface"], how="left"); P["baseline"] = P["baseline"].fillna(tour_spw)
    P = P.merge(R[["pid","match_key","spw_raw","rpw_raw"]], on=["pid","match_key"], how="left")
    P = P.merge(R[["pid","match_key","rpw_raw"]].rename(columns={"pid":"oid","rpw_raw":"opp_rpw_raw"}),
                on=["oid","match_key"], how="left")
    return P.dropna(subset=["spw_raw","opp_rpw_raw"])

rmse = lambda d, c: float(np.sqrt(((d[c]-d["spw_actual"])**2).mean()))
out = []
for K0, Kexp, sw in itertools.product([150., 250., 400.], [0.4, 0.6], [0.3, 0.5, 0.7]):
    m, *_ = build_elo(m0.copy(), K0=K0, Kexp=Kexp, surface_weight=sw)
    P = player_rows(m)
    P["p_elo"] = [invert(w, b_, bo) [0] for w, b_, bo in zip(P["win_prob"], P["baseline"], P["best_of"])]
    P["p_bc"] = P["baseline"] + (P["spw_raw"]-tour_spw) - (P["opp_rpw_raw"]-(1-tour_spw))
    tr, te = P[P["year"] <= 2021], P[P["year"] >= 2022]
    bw, br = 0.0, 9e9
    for wgt in np.linspace(0, 1, 21):
        r = np.sqrt((((wgt*tr["p_elo"]+(1-wgt)*tr["p_bc"]) - tr["spw_actual"])**2).mean())
        if r < br: br, bw = r, wgt
    te = te.copy(); te["p_blend"] = bw*te["p_elo"] + (1-bw)*te["p_bc"]
    mm = m[m["year"] >= 2022]
    rec = dict(K0=K0, Kexp=Kexp, surface_weight=sw,
               elo_acc=float((mm["elo_exp_w"] > 0.5).mean()),
               elo_ll=float(-np.log(np.clip(mm["elo_exp_w"], 1e-6, 1)).mean()),
               rmse_elo=rmse(te,"p_elo"), rmse_bc=rmse(te,"p_bc"),
               rmse_blend=rmse(te,"p_blend"), blend_w=float(bw))
    out.append(rec)
    print(f"K0={K0:5.0f} Kexp={Kexp} sw={sw}: elo_acc={rec['elo_acc']:.4f} elo_ll={rec['elo_ll']:.4f} "
          f"rmse_elo={rec['rmse_elo']:.4f} rmse_bc={rec['rmse_bc']:.4f} "
          f"rmse_blend={rec['rmse_blend']:.4f} (w={bw:.2f})", flush=True)
json.dump(out, open("elo_sweep_results.json","w"), indent=1)
best = min(out, key=lambda r: r["rmse_elo"])
print("\nbest Elo-only config:", best)
