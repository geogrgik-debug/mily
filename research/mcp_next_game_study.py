"""
Empirical mini-study for the Tennis Live Next-Game audit.

Question: before a service game starts, how much do (a) player priors, (b) in-match
cumulative stats, (c) previous-game OUTCOME patterns, (d) previous-game PROCESS features
add to predicting P(hold)?  Also: how much "day-level form" exists (split-half test).

Data: Tennis Abstract Match Charting Project (CC BY-NC-SA 4.0, research use only).
  charting-m-matches.csv, charting-m-points-2010s.csv, charting-m-points-2020s.csv
Usage: python mcp_next_game_study.py <data_dir> [out_json]
"""
import sys, re, json, warnings
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss, brier_score_loss, roc_auc_score
warnings.filterwarnings("ignore")

DATA = sys.argv[1] if len(sys.argv) > 1 else "."
OUT = sys.argv[2] if len(sys.argv) > 2 else "mcp_study_results.json"
SHOT = set("fbrsvzopuylmhijktq")
STD_PTS = {"0-0","15-0","30-0","40-0","0-15","15-15","30-15","40-15","0-30","15-30","30-30","40-30",
           "0-40","15-40","30-40","40-40","AD-40","40-AD"}
BP_PTS = {"0-40","15-40","30-40","40-AD"}

def parse_point(s1, s2):
    """Return dict of per-point process features from MCP notation."""
    s1 = "" if pd.isna(s1) else str(s1).strip()
    s2 = "" if pd.isna(s2) else str(s2).strip()
    first_in = (s2 == "")
    eff = s1 if first_in else s2
    m = re.match(r"^c?([456])(.*)$", eff)
    if not m:
        return None
    rest = m.group(2)
    ace = bool(re.match(r"^\*", rest))
    svc_winner = bool(re.match(r"^#", rest))
    df = (not first_in) and bool(re.match(r"^[nwdx]", rest)) and not any(ch in SHOT for ch in rest)
    shots = 1 + sum(1 for ch in rest if ch in SHOT)
    end = "other"
    if rest.endswith("*"): end = "winner"
    elif rest.endswith("@"): end = "ue"
    elif rest.endswith("#"): end = "fe"
    if df: end = "df"
    if ace or svc_winner: end = "ace_or_svcw"
    # return depth: first return shot like f27 -> depth digit 7/8/9
    rd = np.nan
    mr = re.match(r"^[a-z][0-9]?([789])", rest)
    if mr: rd = int(mr.group(1))
    # who ended the point: last hitter = server if shots odd
    last_hitter_is_server = (shots % 2 == 1)
    return dict(first_in=first_in, ace=ace, df=df, shots=shots, end=end, ret_depth=rd,
                last_srv=last_hitter_is_server)

def load():
    m = pd.read_csv(f"{DATA}/charting-m-matches.csv")
    m = m[pd.to_numeric(m["Date"], errors="coerce").notna()].copy()
    m["Date"] = pd.to_datetime(m["Date"].astype(int).astype(str), format="%Y%m%d", errors="coerce")
    m = m.dropna(subset=["Date"])
    pts = pd.concat([pd.read_csv(f"{DATA}/charting-m-points-2010s.csv", low_memory=False),
                     pd.read_csv(f"{DATA}/charting-m-points-2020s.csv", low_memory=False)])
    pts = pts[pts["match_id"].isin(m["match_id"])]
    pts["Gm#"] = pd.to_numeric(pts["Gm#"], errors="coerce")
    pts["Svr"] = pd.to_numeric(pts["Svr"], errors="coerce")
    pts["PtWinner"] = pd.to_numeric(pts["PtWinner"], errors="coerce")
    pts = pts.dropna(subset=["Gm#","Svr","PtWinner"])
    return m, pts

def build_games(m, pts):
    rows = []
    meta = m.set_index("match_id")
    for mid, g in pts.groupby("match_id", sort=False):
        g = g.sort_values("Pt")
        if mid not in meta.index: continue
        md = meta.loc[mid]
        for gm, gg in g.groupby("Gm#", sort=True):
            ptsset = set(gg["Pts"].astype(str))
            if not ptsset.issubset(STD_PTS):   # tiebreak or weird
                continue
            svr = int(gg["Svr"].iloc[0])
            if gg["Svr"].nunique() != 1: continue
            won = (gg["PtWinner"] == svr).astype(int).values
            n = len(won); w = int(won.sum()); l = n - w
            # game must be complete: server wins >=4 with margin 2, or loses
            hold = 1 if (w >= 4 and w - l >= 2) else (0 if (l >= 4 and l - w >= 2) else None)
            if hold is None: continue
            feats = [parse_point(a, b) for a, b in zip(gg["1st"], gg["2nd"])]
            feats = [f for f in feats if f]
            if len(feats) < max(4, n - 1): continue
            fi = np.mean([f["first_in"] for f in feats]); ace = np.mean([f["ace"] for f in feats])
            dfr = np.mean([f["df"] for f in feats]); rl = np.mean([f["shots"] for f in feats])
            # errors by server / winners by server
            srv_ue = np.mean([(f["end"] == "ue" and f["last_srv"]) for f in feats])
            srv_wn = np.mean([(f["end"] == "winner" and f["last_srv"]) for f in feats])
            ret_ue = np.mean([(f["end"] == "ue" and not f["last_srv"]) for f in feats])
            rd = np.nanmean([f["ret_depth"] for f in feats]) if any(not np.isnan(f["ret_depth"]) for f in feats) else np.nan
            long_pts = [(f["shots"] >= 5) for f in feats]
            long_won = np.mean([won[i] for i, x in enumerate(long_pts) if x and i < len(won)]) if any(long_pts) else np.nan
            bp = int(gg["Pts"].astype(str).isin(BP_PTS).sum())
            deuce = int("40-40" in ptsset)
            rows.append(dict(match_id=mid, date=md["Date"], surface=str(md["Surface"]), gm=int(gm),
                             set1=int(pd.to_numeric(gg["Set1"].iloc[0], errors="coerce") or 0), set2=int(pd.to_numeric(gg["Set2"].iloc[0], errors="coerce") or 0),
                             gm1=int(pd.to_numeric(gg["Gm1"].iloc[0], errors="coerce") or 0), gm2=int(pd.to_numeric(gg["Gm2"].iloc[0], errors="coerce") if pd.notna(pd.to_numeric(gg["Gm2"].iloc[0], errors="coerce")) else 0),
                             svr=svr, server=md["Player 1"] if svr == 1 else md["Player 2"],
                             returner=md["Player 2"] if svr == 1 else md["Player 1"],
                             hold=hold, n_pts=n, pts_won=w, pts_lost=l, bp_faced=bp, deuce=deuce,
                             first_in=fi, ace_r=ace, df_r=dfr, rally_len=rl, srv_ue=srv_ue, srv_wn=srv_wn,
                             ret_ue=ret_ue, ret_depth=rd, long_won=long_won))
    return pd.DataFrame(rows)

def add_features(G):
    G = G.sort_values(["date", "match_id", "gm"]).reset_index(drop=True)
    mu_hold = G["hold"].mean()
    # ---- 1. chronological player priors (strictly earlier matches) ----
    per_match = G.groupby(["match_id", "date", "server"]).agg(h=("hold","sum"), g=("hold","size"),
                                                              w=("pts_won","sum"), n=("n_pts","sum")).reset_index()
    per_match_ret = G.groupby(["match_id","date","returner"]).agg(rb=("hold", lambda s: (1-s).sum()), rg=("hold","size")).reset_index()
    def prior_tables(pm, key, cols, windows):
        pm = pm.sort_values("date")
        out = {}
        for w in windows:
            recs = []
            for p, d in pm.groupby(key):
                d = d.sort_values("date").reset_index(drop=True)
                for i in range(len(d)):
                    past = d.iloc[:i]
                    if w == "all": sel = past
                    elif w.endswith("m"): sel = past[past["date"] >= d.loc[i, "date"] - pd.DateOffset(months=int(w[:-1]))]
                    else: sel = past.tail(int(w))
                    rec = {"match_id": d.loc[i, "match_id"], key: p}
                    for c in cols: rec[f"{c}_{w}"] = sel[c].sum()
                    recs.append(rec)
            out[w] = pd.DataFrame(recs)
        return out
    srv_pri = prior_tables(per_match, "server", ["h","g","w","n"], ["all","12m","3"])
    ret_pri = prior_tables(per_match_ret, "returner", ["rb","rg"], ["all","12m","3"])
    for w, t in srv_pri.items(): G = G.merge(t, on=["match_id","server"], how="left")
    for w, t in ret_pri.items(): G = G.merge(t, on=["match_id","returner"], how="left")
    K = 30.0  # shrinkage games
    for w in ["all","12m","3"]:
        G[f"srv_prior_hold_{w}"] = (G[f"h_{w}"] + K*mu_hold) / (G[f"g_{w}"] + K)
        G[f"ret_prior_break_{w}"] = (G[f"rb_{w}"] + K*(1-mu_hold)) / (G[f"rg_{w}"] + K)
        G[f"srv_prior_games_{w}"] = G[f"g_{w}"]
    G["srv_prior_spw_all"] = (G["w_all"] + 200*0.63) / (G["n_all"] + 200)
    # ---- 2. in-match cumulative BEFORE this game ----
    G["cum_srv_pts"] = G.groupby(["match_id","server"])["n_pts"].cumsum() - G["n_pts"]
    G["cum_srv_won"] = G.groupby(["match_id","server"])["pts_won"].cumsum() - G["pts_won"]
    G["cum_games"] = G.groupby(["match_id","server"]).cumcount()
    G["cum_holds"] = G.groupby(["match_id","server"])["hold"].cumsum() - G["hold"]
    G["live_spw"] = (G["cum_srv_won"] + 40*G["srv_prior_spw_all"]) / (G["cum_srv_pts"] + 40)
    G["live_spw_dev"] = G["live_spw"] - G["srv_prior_spw_all"]
    G["live_hold_rate"] = (G["cum_holds"] + 10*G["srv_prior_hold_all"]) / (G["cum_games"] + 10)
    # returner's own serve so far (how the opponent is doing on serve) -> proxy of opponent day-form
    opp = G[["match_id","server","gm","cum_srv_pts","cum_srv_won"]].rename(columns={"server":"returner","cum_srv_pts":"opp_cum_pts","cum_srv_won":"opp_cum_won"})
    # ---- 3. previous service game outcome (same server) ----
    grp = G.groupby(["match_id","server"])
    for c in ["hold","pts_lost","bp_faced","deuce","first_in","ace_r","df_r","rally_len","srv_ue","srv_wn","ret_ue","ret_depth","long_won"]:
        G[f"prev_{c}"] = grp[c].shift(1)
        G[f"prev2_{c}"] = grp[c].shift(2)
    G["prev_easy_hold"] = ((G["prev_hold"] == 1) & (G["prev_pts_lost"] <= 1)).astype(float)
    G["prev_hard_hold"] = ((G["prev_hold"] == 1) & (G["prev_pts_lost"] >= 3)).astype(float)
    G["prev_broken"] = (G["prev_hold"] == 0).astype(float)
    G["has_prev"] = G["prev_hold"].notna().astype(float)
    # did the server break in the immediately preceding game of the match?
    G["prev_game_in_match_hold"] = G.groupby("match_id")["hold"].shift(1)
    G["prev_game_server_same"] = (G.groupby("match_id")["server"].shift(1) == G["server"])
    G["just_broke"] = ((G["prev_game_in_match_hold"] == 0) & (~G["prev_game_server_same"])).astype(float)
    G["just_broke"] = G["just_broke"].where(G["prev_game_in_match_hold"].notna(), np.nan)
    # ---- 4. process features: deviations from server's own match-so-far / priors ----
    for c in ["first_in","rally_len","srv_ue","df_r","ace_r","ret_depth"]:
        G[f"cum_{c}"] = grp[c].transform(lambda s: s.shift(1).expanding().mean())
        G[f"prev_{c}_dev"] = G[f"prev_{c}"] - G[f"cum_{c}"]
    # ---- 5. score context ----
    def ctx(r):
        sg = r["gm1"] if r["svr"] == 1 else r["gm2"]; rg = r["gm2"] if r["svr"] == 1 else r["gm1"]
        return pd.Series(dict(serving_for_set=int(sg >= 5 and sg - rg >= 1), serving_to_stay=int(rg >= 5 and rg - sg >= 1),
                              game_diff=sg - rg, set_no=r["set1"] + r["set2"] + 1, games_in_set=sg + rg,
                              sets_diff=(r["set1"] - r["set2"]) if r["svr"] == 1 else (r["set2"] - r["set1"])))
    G = pd.concat([G, G.apply(ctx, axis=1)], axis=1)
    G["surf_clay"] = (G["surface"] == "Clay").astype(int); G["surf_grass"] = (G["surface"] == "Grass").astype(int)
    return G

GROUPS = {
    "A_prior": ["srv_prior_hold_all","ret_prior_break_all","srv_prior_spw_all","surf_clay","surf_grass"],
    "B_live_cum": ["live_spw_dev","cum_srv_pts","live_hold_rate"],
    "C_prev_outcome": ["prev_easy_hold","prev_hard_hold","prev_broken","prev_bp_faced","prev_deuce","has_prev","just_broke"],
    "D_prev_process": ["prev_first_in_dev","prev_rally_len_dev","prev_srv_ue_dev","prev_df_r_dev","prev_ace_r_dev","prev_ret_depth_dev","prev_long_won"],
    "E_context": ["serving_for_set","serving_to_stay","game_diff","set_no","games_in_set","sets_diff"],
}

def fit_eval(tr, te, cols, model="lr"):
    Xtr = tr[cols].fillna(0).values; Xte = te[cols].fillna(0).values
    if model == "lr":
        clf = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
    else:
        clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0)
    clf.fit(Xtr, tr["hold"]); p = clf.predict_proba(Xte)[:, 1]
    return dict(logloss=log_loss(te["hold"], p), brier=brier_score_loss(te["hold"], p), auc=roc_auc_score(te["hold"], p)), clf, p

def main():
    import os
    cache = f"{DATA}/_games_cache.pkl"
    if os.path.exists(cache):
        G = pd.read_pickle(cache)
    else:
        m, pts = load()
        G = build_games(m, pts)
        G.to_pickle(cache)
    print("service games:", len(G), "matches:", G["match_id"].nunique(), "hold rate:", round(G["hold"].mean(), 4))
    import time; t0=time.time()
    G = add_features(G)
    print("features built in", round(time.time()-t0,1), "s", flush=True)
    G = G[G["has_prev"] == 1]  # only games where the server already served at least once in this match
    split = pd.Timestamp("2023-01-01")
    tr, te = G[G["date"] < split], G[G["date"] >= split]
    print("train games", len(tr), "test games", len(te), "test matches", te["match_id"].nunique())
    res = {"n_games_total": int(len(G)), "n_train": int(len(tr)), "n_test": int(len(te)),
           "test_matches": int(te["match_id"].nunique()), "hold_rate_test": float(te["hold"].mean()), "models": {}}
    # baselines
    p0 = np.full(len(te), tr["hold"].mean())
    res["models"]["0_constant"] = dict(logloss=log_loss(te["hold"], p0), brier=brier_score_loss(te["hold"], p0), auc=0.5)
    # nested feature groups
    order = ["A_prior","B_live_cum","C_prev_outcome","D_prev_process","E_context"]
    cols = []
    for gname in order:
        cols = cols + GROUPS[gname]
        r, clf, _ = fit_eval(tr, te, cols); res["models"][f"lr_cum_{gname}"] = r
        print(f"LR +{gname:15s} logloss={r['logloss']:.4f} brier={r['brier']:.4f} auc={r['auc']:.3f}")
    # ablations: full minus one group
    allcols = sum([GROUPS[g] for g in order], [])
    for gname in order:
        c = [x for x in allcols if x not in GROUPS[gname]]
        r, _, _ = fit_eval(tr, te, c); res["models"][f"lr_full_minus_{gname}"] = r
        print(f"LR full - {gname:15s} logloss={r['logloss']:.4f} brier={r['brier']:.4f}")
    r, clf, pfull = fit_eval(tr, te, allcols); res["models"]["lr_full"] = r
    r2, _, pgb = fit_eval(tr, te, allcols, "gb"); res["models"]["gbdt_full"] = r2
    print("GBDT full", r2)
    # coefficients of the full LR (standardized)
    lr = clf.named_steps["logisticregression"]
    coefs = dict(zip(allcols, [float(x) for x in lr.coef_[0]]))
    res["lr_full_std_coefs"] = coefs
    for k, v in sorted(coefs.items(), key=lambda kv: -abs(kv[1])): print(f"  {k:24s} {v:+.3f}")
    # window comparison for priors: all vs 12m vs last3 (prior-only models)
    for w in ["all","12m","3"]:
        c = [f"srv_prior_hold_{w}", f"ret_prior_break_{w}"]
        r, _, _ = fit_eval(tr, te, c); res["models"][f"prior_window_{w}"] = r
        print(f"prior window {w}: logloss={r['logloss']:.4f}")
    r, _, _ = fit_eval(tr, te, ["srv_prior_hold_all","ret_prior_break_all","srv_prior_hold_3","ret_prior_break_3"]); res["models"]["prior_all_plus_last3"] = r
    print("prior all + last3:", r)
    # --- raw conditional hold rates (test+train, descriptive) ---
    desc = {}
    for name, mask in [("after_easy_hold", G["prev_easy_hold"] == 1), ("after_hard_hold", G["prev_hard_hold"] == 1),
                       ("after_broken", G["prev_broken"] == 1), ("just_broke_opponent", G["just_broke"] == 1),
                       ("did_not_just_break", G["just_broke"] == 0)]:
        desc[name] = dict(n=int(mask.sum()), hold_rate=float(G.loc[mask, "hold"].mean()))
    # same, controlling for live_spw_dev tercile
    G["spw_dev_bin"] = pd.qcut(G["live_spw_dev"], 3, labels=["low","mid","high"])
    ctab = G.groupby(["spw_dev_bin", "prev_broken"], observed=True)["hold"].agg(["mean","size"]).reset_index()
    desc["hold_by_spwdev_and_prev_broken"] = ctab.to_dict(orient="records")
    res["descriptive"] = desc
    print(json.dumps(desc, indent=1, default=str))
    # --- day-level form: split-half test at match level ---
    # residual SPW in first k service games vs remainder, relative to prior
    out = {}
    for k in [2, 3, 4, 6]:
        recs = []
        for (mid, s), d in G.groupby(["match_id","server"]):
            d = d.sort_values("gm")
            if len(d) < k + 4: continue
            pri = d["srv_prior_spw_all"].iloc[0]
            a, b = d.iloc[:k], d.iloc[k:]
            recs.append((a["pts_won"].sum()/a["n_pts"].sum() - pri, b["pts_won"].sum()/b["n_pts"].sum() - pri, a["n_pts"].sum(), b["n_pts"].sum()))
        R = np.array(recs)
        x, y, n1 = R[:,0], R[:,1], R[:,2]
        slope = np.polyfit(x, y, 1)[0]; corr = np.corrcoef(x, y)[0,1]
        # expected binomial noise variance in x: p(1-p)/n1 with p~0.63
        noise = np.mean(0.63*0.37/n1); var_x = x.var()
        day_var = max(var_x - noise, 0.0)
        out[f"k{k}"] = dict(n=int(len(R)), slope=float(slope), corr=float(corr), var_x=float(var_x), binom_noise=float(noise),
                            implied_day_form_sd=float(np.sqrt(day_var)), mean_points_in_first_k=float(n1.mean()))
        print(f"split-half k={k}: n={len(R)} slope={slope:.3f} corr={corr:.3f} implied day-form sd={np.sqrt(day_var):.3f}")
    res["day_form_split_half"] = out
    # match-level excess variance of SPW residual (all games)
    pm = G.groupby(["match_id","server"]).agg(w=("pts_won","sum"), n=("n_pts","sum"), pri=("srv_prior_spw_all","first")).reset_index()
    pm = pm[pm["n"] >= 40]
    resid = pm["w"]/pm["n"] - pm["pri"]; noise = (0.63*0.37/pm["n"]).mean()
    res["match_level_resid_var"] = dict(var=float(resid.var()), binom_noise=float(noise), excess_sd=float(np.sqrt(max(resid.var()-noise,0))), n=int(len(pm)))
    print("match-level SPW residual var", res["match_level_resid_var"])
    # bootstrap over matches: logloss difference full vs prior-only
    rng = np.random.default_rng(0)
    _, _, pA = fit_eval(tr, te, GROUPS["A_prior"])
    codes, um = pd.factorize(te["match_id"]); y = te["hold"].values.astype(float)
    def ll_vec(p): p = np.clip(p, 1e-6, 1-1e-6); return -(y*np.log(p) + (1-y)*np.log(1-p))
    d = ll_vec(pA) - ll_vec(pfull)
    per_match_sum = np.bincount(codes, weights=d); per_match_n = np.bincount(codes)
    diffs = []
    for _ in range(1000):
        sm = rng.integers(0, len(um), len(um))
        diffs.append(per_match_sum[sm].sum() / per_match_n[sm].sum())
    res["bootstrap_logloss_gain_full_vs_prior"] = dict(mean=float(np.mean(diffs)), ci95=[float(np.percentile(diffs,2.5)), float(np.percentile(diffs,97.5))])
    print("bootstrap gain full vs prior (per-game logloss):", res["bootstrap_logloss_gain_full_vs_prior"])
    # same for B_live_cum increment over prior, and for outcome/process increments over prior+live
    _, _, pAB = fit_eval(tr, te, GROUPS["A_prior"]+GROUPS["B_live_cum"])
    _, _, pABC = fit_eval(tr, te, GROUPS["A_prior"]+GROUPS["B_live_cum"]+GROUPS["C_prev_outcome"])
    _, _, pABD = fit_eval(tr, te, GROUPS["A_prior"]+GROUPS["B_live_cum"]+GROUPS["D_prev_process"])
    for name, pa, pb in [("live_cum_over_prior", pA, pAB), ("prev_outcome_over_prior_live", pAB, pABC), ("prev_process_over_prior_live", pAB, pABD)]:
        d = ll_vec(pa) - ll_vec(pb); pms = np.bincount(codes, weights=d)
        diffs = [pms[sm].sum()/per_match_n[sm].sum() for sm in (rng.integers(0, len(um), len(um)) for _ in range(1000))]
        res[f"bootstrap_gain_{name}"] = dict(mean=float(np.mean(diffs)), ci95=[float(np.percentile(diffs,2.5)), float(np.percentile(diffs,97.5))])
        print(f"bootstrap gain {name}:", res[f"bootstrap_gain_{name}"])
    with open(OUT, "w") as f: json.dump(res, f, indent=1, default=str)

if __name__ == "__main__":
    main()
