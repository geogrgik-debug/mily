"""Robustness checks for the B2 conclusion that process features add nothing.

Each variant attacks one way the conclusion could be an artefact of the feature construction
rather than a property of the data:

  raw           no winsorizing and no median imputation, so extreme values survive
  ema           exponentially weighted window over past service games instead of last-game
                and whole-match aggregates, half-life 2 and 4 games
  interaction   process deltas interacted with the server's prior level, so a speed drop is
                allowed to matter more for a big server than for a counter-puncher
  resid_score   process deltas residualized on the score state before entering the model
  gbdt          gradient boosting on the process block, which can find interactions a linear
                model cannot

Usage: python b2_robustness.py <slam_dir> <priors_csv> [out_json]
"""
import sys, os, json, warnings
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import slam_process_study as S
warnings.filterwarnings("ignore")

SLAM = sys.argv[1] if len(sys.argv) > 1 else "slam"
PRIORS = sys.argv[2] if len(sys.argv) > 2 else "priors.csv"
OUT = sys.argv[3] if len(sys.argv) > 3 else "b2_robustness.json"
SPLIT = 2019
PROC_CORE = ["speed1_mean", "first_in", "rally_mean", "ret_deep_share",
             "dist_srv_per_pt", "df_r", "ace_r", "sec_per_point"]

def load():
    cache = os.path.join(SLAM, "_slam_games.pkl")
    G = pd.read_pickle(cache) if os.path.exists(cache) else S.build_games(S.load_slam(SLAM))
    G = S.attach_prior(G, PRIORS)
    G = S.add_features(G)
    G = G[(G["has_prev"] == 1) & G["p_blend"].notna()]          # men's draw only
    return G.reset_index(drop=True)

def add_ema(G, halflives=(2.0, 4.0)):
    """Exponentially weighted mean of each process field over the server's earlier games."""
    G = G.sort_values(["match_id", "server", "set_no", "game_no"]).reset_index(drop=True)
    grp = G.groupby(["match_id", "server"], sort=False)
    for hl in halflives:
        a = 1 - 0.5 ** (1.0 / hl)
        for c in PROC_CORE:
            ew = grp[c].transform(lambda s: s.shift(1).ewm(alpha=a, ignore_na=True).mean())
            G[f"ema{int(hl)}_{c}_dev"] = ew - G[f"hist_{c}"]
    return G

def fit(tr, te, cols, kind="lr", winsor=True, impute=True):
    Tr, Te = tr[cols].copy(), te[cols].copy()
    out = list(cols)
    for c in cols:
        if winsor:
            lo, hi = Tr[c].quantile(0.01), Tr[c].quantile(0.99)
            if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
                Tr[c] = Tr[c].clip(lo, hi); Te[c] = Te[c].clip(lo, hi)
        if Tr[c].isna().mean() > 0.01:
            Tr[c + "__na"] = Tr[c].isna().astype(float); Te[c + "__na"] = Te[c].isna().astype(float)
            out.append(c + "__na")
        fill = Tr[c].median() if impute else 0.0
        if not np.isfinite(fill): fill = 0.0
        Tr[c] = Tr[c].fillna(fill); Te[c] = Te[c].fillna(fill)
    Xtr, Xte = Tr[out].replace([np.inf, -np.inf], 0).to_numpy(), Te[out].replace([np.inf, -np.inf], 0).to_numpy()
    clf = (make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=3000)) if kind == "lr"
           else HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05,
                                               max_leaf_nodes=31, l2_regularization=1.0))
    clf.fit(Xtr, tr["hold"])
    return clf.predict_proba(Xte)[:, 1]

def report(name, te, p_base, p_test, res):
    g = S.boot_gain(te, p_base, p_test)
    ll = lambda p: float(log_loss(te["hold"], np.clip(p, 1e-6, 1 - 1e-6)))
    res[name] = dict(base=ll(p_base), with_process=ll(p_test), **g)
    mark = "+" if g["ci95"][0] > 0 else ("-" if g["ci95"][1] < 0 else "0")
    print(f"  [{mark}] {name:34s} base={ll(p_base):.4f} +proc={ll(p_test):.4f} "
          f"gain={g['mean']:+.5f} CI [{g['ci95'][0]:+.5f}, {g['ci95'][1]:+.5f}]", flush=True)

def main():
    G = load()
    G = add_ema(G)
    # residualize the process deltas on score state, as the fatigue literature requires
    ctx = ["game_diff", "games_in_set", "set_no", "serving_for_set", "serving_to_stay"]
    from sklearn.linear_model import LinearRegression
    tr_mask = G["year"] < SPLIT
    for c in S.GROUPS["D_prev_proc"] + S.GROUPS["F_cum_proc"]:
        v = G[c]; ok = v.notna() & G[ctx].notna().all(axis=1)
        if ok.sum() < 1000: G[c + "_r"] = v; continue
        lr = LinearRegression().fit(G.loc[ok & tr_mask, ctx], G.loc[ok & tr_mask, c])
        G[c + "_r"] = np.where(ok, v - lr.predict(G[ctx].fillna(0)), np.nan)
    tr, te = G[G["year"] < SPLIT], G[G["year"] >= SPLIT]
    print(f"men's games: train {len(tr)}, test {len(te)} ({te['match_id'].nunique()} matches)", flush=True)

    base_cols = S.GROUPS["A_prior"] + S.GROUPS["B_live_cum"] + S.GROUPS["E_context"]
    proc = S.GROUPS["D_prev_proc"] + S.GROUPS["F_cum_proc"]
    ema = [f"ema{h}_{c}_dev" for h in (2, 4) for c in PROC_CORE]
    res = {"n_train": int(len(tr)), "n_test": int(len(te))}

    p_base = fit(tr, te, base_cols)
    print("\nDoes any construction of the process block beat the same model without it?", flush=True)
    report("as_published", te, p_base, fit(tr, te, base_cols + proc), res)
    report("raw_no_winsor_no_impute", te, p_base,
           fit(tr, te, base_cols + proc, winsor=False, impute=False), res)
    report("ema_halflife_2_and_4", te, p_base, fit(tr, te, base_cols + ema), res)
    report("ema_plus_published", te, p_base, fit(tr, te, base_cols + proc + ema), res)
    report("residualized_on_score", te, p_base, fit(tr, te, base_cols + [c + "_r" for c in proc]), res)

    # interaction with the server's own level: a speed drop should hurt a big server more
    Gi = G.copy()
    lvl = Gi["p_prior"] - Gi["p_prior"].mean()
    inter = []
    for c in ["prev_speed1_mean_dev", "cum_speed1_mean_dev", "prev_ace_r_dev", "cum_ace_r_dev"]:
        Gi[c + "_x_lvl"] = Gi[c] * lvl; inter.append(c + "_x_lvl")
    tri, tei = Gi[Gi["year"] < SPLIT], Gi[Gi["year"] >= SPLIT]
    report("interaction_with_server_level", tei, fit(tri, tei, base_cols),
           fit(tri, tei, base_cols + proc + inter), res)

    p_base_gb = fit(tr, te, base_cols, kind="gb")
    report("gbdt_can_find_interactions", te, p_base_gb, fit(tr, te, base_cols + proc + ema, kind="gb"), res)

    # top servers only, where a speed drop should bite hardest
    hi = G["p_prior"] >= G["p_prior"].quantile(0.75)
    trh, teh = G[hi & (G["year"] < SPLIT)], G[hi & (G["year"] >= SPLIT)]
    print(f"\ntop-quartile servers: train {len(trh)}, test {len(teh)}", flush=True)
    report("top_quartile_servers", teh, fit(trh, teh, base_cols), fit(trh, teh, base_cols + proc + ema), res)

    with open(OUT, "w") as f: json.dump(res, f, indent=1)
    print("\nwrote", OUT, flush=True)

if __name__ == "__main__":
    main()
