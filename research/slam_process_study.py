"""
Experiment B2: do PROCESS features beat OUTCOME features for the next service game?

This is the decisive test for the video/CV branch of the project. Grand Slam point-by-point
data carries the process fields a CV pipeline would have to reconstruct from broadcast video
(serve speed, rally length, return depth, distance run, serve placement). If those fields add
nothing over point outcomes on THIS data, where they are measured by Hawk-Eye rather than
estimated from pixels, then no video pipeline can add anything either.

Data: Sackmann slam_pointbypoint mirror (CC BY-NC-SA 4.0, research use only).
Prior: Elo-anchored p_serve from elo_prior.py, joined by player name.

Usage: python slam_process_study.py <slam_dir> <priors_csv> <atp_dir> [out_json]
"""
import sys, os, re, glob, json, warnings
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss, brier_score_loss, roc_auc_score

warnings.filterwarnings("ignore")
SLAM = sys.argv[1] if len(sys.argv) > 1 else "slam"
PRIORS = sys.argv[2] if len(sys.argv) > 2 else "priors.csv"
ATP = sys.argv[3] if len(sys.argv) > 3 else "atp"
OUT = sys.argv[4] if len(sys.argv) > 4 else "slam_process_results.json"

SLAM_SURFACE = {"ausopen": "Hard", "usopen": "Hard", "wimbledon": "Grass", "frenchopen": "Clay"}
SLAM_MONTH = {"ausopen": 1, "frenchopen": 5, "wimbledon": 7, "usopen": 8}
SCORES = {"0": 0, "15": 1, "30": 2, "40": 3, "AD": 4}
# Some feeds (2020 RG, 2021 AO, 2021 RG) write the last point of a game as "GAME"; treating
# that token as a non-standard score dropped those three tournaments entirely.
SCORE_TOKENS = set(SCORES) | {"GAME"}

# ---------------------------------------------------------------- load points
def load_slam(slam_dir):
    frames = []
    for f in sorted(glob.glob(os.path.join(slam_dir, "*-points.csv"))):
        base = os.path.basename(f)
        year, slam = base.split("-")[0], base.split("-")[1]
        try: d = pd.read_csv(f, low_memory=False)
        except Exception: continue
        d["year"] = int(year); d["slam"] = slam
        mf = f.replace("-points.csv", "-matches.csv")
        if os.path.exists(mf):
            mm = pd.read_csv(mf)[["match_id", "player1", "player2"]]
            d = d.merge(mm, on="match_id", how="left")
        frames.append(d)
    p = pd.concat(frames, ignore_index=True)
    p["surface"] = p["slam"].map(SLAM_SURFACE)
    # approximate date: tournament month, used only for chronological ordering and prior join
    p["date"] = pd.to_datetime(dict(year=p["year"], month=p["slam"].map(SLAM_MONTH), day=1))
    for c in ["SetNo","GameNo","PointNumber","PointServer","PointWinner","Speed_KMH","RallyCount",
              "ServeNumber","P1DistanceRun","P2DistanceRun","P1GamesWon","P2GamesWon",
              "P1Ace","P2Ace","P1DoubleFault","P2DoubleFault","P1UnfErr","P2UnfErr",
              "P1Winner","P2Winner","P1BreakPoint","P2BreakPoint"]:
        if c in p.columns: p[c] = pd.to_numeric(p[c], errors="coerce")
        else: p[c] = np.nan
    p = p[p["PointServer"].isin([1, 2]) & p["PointWinner"].isin([1, 2])]
    # The third segment of match_id encodes the draw: 1xxx men's singles, 2xxx women's.
    # Deriving it from whether an ATP prior joined instead put 762 men's matches in the
    # women's sample, because AO and RG abbreviate names in 2018-2021.
    seg = p["match_id"].astype(str).str.split("-").str[-1].str[0].str.upper()
    p["draw"] = np.where(seg.isin(["1", "M"]), "M", np.where(seg.isin(["2", "W"]), "W", "?"))
    return p

# ---------------------------------------------------------------- service games
def build_games(p):
    """One row per completed, non-tiebreak service game, built with whole-column operations.

    A per-game Python loop over this data costs millions of small pandas calls and runs for
    tens of minutes; every quantity here is a groupby aggregation instead.
    """
    p = p.copy()
    srv = p["PointServer"].to_numpy()
    s1 = p["P1Score"].astype(str).to_numpy()
    s2 = p["P2Score"].astype(str).to_numpy()
    ok = np.isin(s1, list(SCORE_TOKENS)) & np.isin(s2, list(SCORE_TOKENS))
    p["bad_score"] = ~ok                                   # tiebreak games score 0,1,2,...
    p["srv_won"] = (p["PointWinner"].to_numpy() == srv).astype(float)
    is_p1 = (srv == 1)
    def by_server(c1, c2):
        a = pd.to_numeric(p[c1], errors="coerce").to_numpy()
        b = pd.to_numeric(p[c2], errors="coerce").to_numpy()
        return np.where(is_p1, a, b), np.where(is_p1, b, a)
    p["srv_ace"], _ = by_server("P1Ace", "P2Ace")
    p["srv_df"], _ = by_server("P1DoubleFault", "P2DoubleFault")
    p["srv_ue"], p["ret_ue"] = by_server("P1UnfErr", "P2UnfErr")
    p["srv_win_shot"], _ = by_server("P1Winner", "P2Winner")
    _, p["bp"] = by_server("P1BreakPoint", "P2BreakPoint")   # break point belongs to the returner
    p["dist_srv"], p["dist_ret"] = by_server("P1DistanceRun", "P2DistanceRun")
    for c in ["srv_ace","srv_df","srv_ue","ret_ue","srv_win_shot","bp"]:
        p[c] = (np.nan_to_num(p[c].to_numpy()) > 0).astype(float)
    for c in ["dist_srv","dist_ret"]:
        p[c] = pd.to_numeric(p[c], errors="coerce").replace(0, np.nan)
    speed = pd.to_numeric(p["Speed_KMH"], errors="coerce").replace(0, np.nan)
    sn = pd.to_numeric(p["ServeNumber"], errors="coerce")
    p["speed1"] = speed.where(sn == 1); p["speed2"] = speed.where(sn == 2)
    p["speed"] = speed
    p["is_first"] = (sn == 1).astype(float).where(sn.notna())
    rally = pd.to_numeric(p["RallyCount"], errors="coerce").replace(0, np.nan)
    p["rally"] = rally; p["rally_long"] = (rally >= 5).astype(float).where(rally.notna())
    rd = p["ReturnDepth"].astype(str)
    p["ret_deep"] = np.where(rd == "D", 1.0, np.where(rd == "ND", 0.0, np.nan))
    p["deuce_pt"] = ((s1 == "40") & (s2 == "40")).astype(float)
    # ElapsedTime is usually H:MM:SS but the feed also carries malformed values; anything that
    # does not parse to a plausible match clock becomes NaN rather than a huge number.
    t = p["ElapsedTime"].astype(str).str.extract(r"^(\d{1,2}):(\d{2}):(\d{2})$")
    tsec = (pd.to_numeric(t[0], errors="coerce") * 3600
            + pd.to_numeric(t[1], errors="coerce") * 60
            + pd.to_numeric(t[2], errors="coerce"))
    p["tsec"] = tsec.where((tsec >= 0) & (tsec <= 8 * 3600))
    p["server_name"] = np.where(is_p1, p["player1"], p["player2"])
    p["returner_name"] = np.where(is_p1, p["player2"], p["player1"])

    g = p.groupby(["match_id", "SetNo", "GameNo"], sort=False)
    A = g.agg(
        year=("year", "first"), slam=("slam", "first"), surface=("surface", "first"),
        draw=("draw", "first"),
        date=("date", "first"), srv=("PointServer", "first"), n_srv=("PointServer", "nunique"),
        server=("server_name", "first"), returner=("returner_name", "first"),
        p1_games=("P1GamesWon", "first"), p2_games=("P2GamesWon", "first"),
        first_pt=("PointNumber", "first"),
        n_pts=("srv_won", "size"), pts_won=("srv_won", "sum"),
        bad=("bad_score", "max"), deuce=("deuce_pt", "max"), bp_faced=("bp", "sum"),
        speed1_mean=("speed1", "mean"), speed1_n=("speed1", "count"),
        speed2_mean=("speed2", "mean"), speed_max=("speed", "max"),
        first_in=("is_first", "mean"),
        rally_mean=("rally", "mean"), rally_long_share=("rally_long", "mean"),
        ret_deep_share=("ret_deep", "mean"), ret_depth_n=("ret_deep", "count"),
        dist_srv_per_pt=("dist_srv", "mean"), dist_ret_per_pt=("dist_ret", "mean"),
        ace_r=("srv_ace", "mean"), df_r=("srv_df", "mean"),
        srv_ue_r=("srv_ue", "mean"), ret_ue_r=("ret_ue", "mean"), srv_win_r=("srv_win_shot", "mean"),
        t_start=("tsec", "first"), t_end=("tsec", "last"),
    ).reset_index()
    A["pts_lost"] = A["n_pts"] - A["pts_won"]
    spp = (A["t_end"] - A["t_start"]) / A["n_pts"].sub(1).clip(lower=1)
    A["sec_per_point"] = spp.where((spp >= 5) & (spp <= 180))   # outside this is a delay or a bad clock
    hold = np.where((A["pts_won"] >= 4) & (A["pts_won"] - A["pts_lost"] >= 2), 1.0,
           np.where((A["pts_lost"] >= 4) & (A["pts_lost"] - A["pts_won"] >= 2), 0.0, np.nan))
    A["hold"] = hold
    A = A[(A["bad"] == 0) & (A["n_srv"] == 1) & (A["n_pts"] >= 4) & A["hold"].notna()]
    A = A.rename(columns={"SetNo": "set_no", "GameNo": "game_no"})
    A["hold"] = A["hold"].astype(int)
    A["deuce"] = A["deuce"].astype(int)
    keep = [c for c in A.columns if c not in ("bad", "n_srv")]
    return A[keep].sort_values(["date", "match_id", "set_no", "game_no"]).reset_index(drop=True)

# ---------------------------------------------------------------- features
def norm_name(x):
    """First initial plus surname, so "Novak Djokovic" and "N. Djokovic" agree.

    AO and RG abbreviate given names in 2018-2021; matching on the full string silently
    dropped those eight tournaments from the men's sample.
    """
    x = str(x).strip().lower()
    x = re.sub(r"[^a-z ]", " ", x)
    parts = [t for t in re.split(r"\s+", x) if t]
    if not parts: return ""
    if len(parts) == 1: return parts[0]
    return parts[0][0] + " " + parts[-1]

def attach_prior(G, priors_csv):
    """Join the Elo-anchored p_serve prior by player name and nearest earlier slam date."""
    pr = pd.read_csv(priors_csv, parse_dates=["date"])
    pr = pr[pr["best_of"] == 5]                      # slams only, so the inversion matches format
    pr["pn"] = pr["pname"].map(norm_name); pr["on"] = pr["oname"].map(norm_name)
    G["pn"] = G["server"].map(norm_name); G["on"] = G["returner"].map(norm_name)
    # match on (server, returner, +-45 days) -> exact match of the same slam match
    key = pr[["pn","on","date","p_elo","p_bc","p_blend","p_raw","baseline","spw_n_w","n_prev_matches","surface"]]
    G = G.merge(key, on=["pn","on"], how="left", suffixes=("", "_pr"))
    G["dgap"] = (G["date"] - G["date_pr"]).dt.days.abs()
    G = G.sort_values(["match_id","set_no","game_no","dgap"])
    G = G[(G["dgap"] <= 60) | G["dgap"].isna()]
    G = G.drop_duplicates(subset=["match_id","set_no","game_no"], keep="first")
    return G.sort_values(["date","match_id","set_no","game_no"]).reset_index(drop=True)

PROC = ["speed1_mean","speed2_mean","speed_max","first_in","rally_mean","rally_long_share",
        "ret_deep_share","dist_srv_per_pt","dist_ret_per_pt","sec_per_point",
        "ace_r","df_r","srv_ue_r","ret_ue_r","srv_win_r"]

def add_features(G):
    G = G.sort_values(["date","match_id","set_no","game_no"]).reset_index(drop=True)
    mu = G["hold"].mean()
    # ---- player-level historical baselines for process fields, from strictly earlier tournaments
    G["tkey"] = G["year"].astype(str) + "-" + G["slam"].astype(str)
    order = {t: i for i, t in enumerate(sorted(G["tkey"].unique(), key=lambda t: (int(t[:4]), SLAM_MONTH[t[5:]])))}
    G["tord"] = G["tkey"].map(order)
    base_cols = {}
    for c in PROC + ["hold"]:
        agg = G.groupby(["server","tord"])[c].agg(["sum","count"]).reset_index()
        agg = agg.sort_values(["server","tord"])
        agg["csum"] = agg.groupby("server")["sum"].cumsum() - agg["sum"]
        agg["ccnt"] = agg.groupby("server")["count"].cumsum() - agg["count"]
        base_cols[c] = agg[["server","tord","csum","ccnt"]].rename(
            columns={"csum": f"hist_{c}_sum", "ccnt": f"hist_{c}_n"})
        G = G.merge(base_cols[c], on=["server","tord"], how="left")
        gm = G[c].mean()
        G[f"hist_{c}"] = (G[f"hist_{c}_sum"] + 20*gm) / (G[f"hist_{c}_n"] + 20)
    # ---- in-match cumulative, strictly before this game
    grp = G.groupby(["match_id","server"], sort=False)
    G["cum_pts"] = grp["n_pts"].cumsum() - G["n_pts"]
    G["cum_won"] = grp["pts_won"].cumsum() - G["pts_won"]
    G["cum_games"] = grp.cumcount()
    G["cum_holds"] = grp["hold"].cumsum() - G["hold"]
    G["p_prior"] = G["p_blend"].fillna(G["p_elo"]).fillna(G["baseline"]).fillna(0.64)
    G["live_spw"] = (G["cum_won"] + 40*G["p_prior"]) / (G["cum_pts"] + 40)
    G["live_spw_dev"] = G["live_spw"] - G["p_prior"]
    G["live_hold_rate"] = (G["cum_holds"] + 6*mu) / (G["cum_games"] + 6)
    # The returner's own service games so far. This was a stub set to zero, which meant the
    # comparison model never saw how the other player was serving in this same match.
    G["g_ord"] = G.groupby("match_id").cumcount()
    side = G[["match_id", "server", "g_ord", "n_pts", "pts_won", "hold"]].copy()
    side = side.sort_values(["match_id", "server", "g_ord"])
    side["opp_cum_pts"] = side.groupby(["match_id", "server"])["n_pts"].cumsum() - side["n_pts"]
    side["opp_cum_won"] = side.groupby(["match_id", "server"])["pts_won"].cumsum() - side["pts_won"]
    side["opp_cum_holds"] = side.groupby(["match_id", "server"])["hold"].cumsum() - side["hold"]
    side["opp_cum_games"] = side.groupby(["match_id", "server"]).cumcount()
    side = side.rename(columns={"server": "returner"})[
        ["match_id", "returner", "g_ord", "opp_cum_pts", "opp_cum_won",
         "opp_cum_holds", "opp_cum_games"]].sort_values("g_ord")
    G = pd.merge_asof(G.sort_values("g_ord"), side, on="g_ord",
                      by=["match_id", "returner"], direction="backward")
    for c in ["opp_cum_pts", "opp_cum_won", "opp_cum_holds", "opp_cum_games"]:
        G[c] = G[c].fillna(0.0)
    G["opp_live_spw"] = (G["opp_cum_won"] + 40 * 0.62) / (G["opp_cum_pts"] + 40)
    G["opp_live_hold_rate"] = (G["opp_cum_holds"] + 6 * mu) / (G["opp_cum_games"] + 6)
    # ---- previous service game: outcome and process, plus in-match running process means
    for c in ["hold","pts_lost","bp_faced","deuce","n_pts"] + PROC:
        G[f"prev_{c}"] = grp[c].shift(1)
    for c in PROC:
        G[f"cum_{c}"] = grp[c].transform(lambda s: s.shift(1).expanding().mean())
        G[f"prev_{c}_dev"] = G[f"prev_{c}"] - G[f"hist_{c}"]      # vs player's own history
        G[f"cum_{c}_dev"] = G[f"cum_{c}"] - G[f"hist_{c}"]        # match so far vs history
        G[f"prev_{c}_trend"] = G[f"prev_{c}"] - G[f"cum_{c}"]     # last game vs match so far
    G["prev_easy_hold"] = ((G["prev_hold"] == 1) & (G["prev_pts_lost"] <= 1)).astype(float)
    G["prev_hard_hold"] = ((G["prev_hold"] == 1) & (G["prev_pts_lost"] >= 3)).astype(float)
    G["prev_broken"] = (G["prev_hold"] == 0).astype(float)
    G["has_prev"] = G["prev_hold"].notna().astype(float)
    # just broke, and whether a changeover separates that break from this game (Meier et al. 2020)
    G["prev_game_hold"] = G.groupby("match_id")["hold"].shift(1)
    G["prev_game_srv"] = G.groupby("match_id")["server"].shift(1)
    G["just_broke"] = ((G["prev_game_hold"] == 0) & (G["prev_game_srv"] != G["server"])).astype(float)
    G["games_before"] = G["p1_games"] + G["p2_games"]
    G["changeover_before"] = (G["games_before"] % 2 == 1).astype(float)   # changeover after odd games
    G["just_broke_no_changeover"] = G["just_broke"] * (1 - G["changeover_before"])
    G["just_broke_changeover"] = G["just_broke"] * G["changeover_before"]
    # ---- context
    sg = np.where(G["srv"] == 1, G["p1_games"], G["p2_games"])
    rg = np.where(G["srv"] == 1, G["p2_games"], G["p1_games"])
    G["game_diff"] = sg - rg
    G["serving_for_set"] = ((sg >= 5) & (sg - rg >= 1)).astype(int)
    G["serving_to_stay"] = ((rg >= 5) & (rg - sg >= 1)).astype(int)
    G["games_in_set"] = sg + rg
    G["surf_grass"] = (G["surface"] == "Grass").astype(int)
    G["surf_clay"] = (G["surface"] == "Clay").astype(int)
    return G

# ---------------------------------------------------------------- evaluation
GROUPS = {
    "A_prior":      ["p_prior", "hist_hold", "surf_grass", "surf_clay"],
    "B_live_cum":   ["live_spw_dev", "cum_pts", "live_hold_rate"],
    "B2_live_opp":  ["opp_live_spw", "opp_live_hold_rate", "opp_cum_pts"],
    "C_prev_out":   ["prev_easy_hold", "prev_hard_hold", "prev_broken", "prev_bp_faced",
                     "prev_deuce", "prev_n_pts", "just_broke_no_changeover", "just_broke_changeover"],
    "D_prev_proc":  ["prev_speed1_mean_dev", "prev_speed1_mean_trend", "prev_first_in_dev",
                     "prev_rally_mean_dev", "prev_ret_deep_share_dev", "prev_dist_srv_per_pt_dev",
                     "prev_df_r_dev", "prev_ace_r_dev", "prev_srv_ue_r_dev", "prev_sec_per_point_dev"],
    "F_cum_proc":   ["cum_speed1_mean_dev", "cum_first_in_dev", "cum_rally_mean_dev",
                     "cum_ret_deep_share_dev", "cum_dist_srv_per_pt_dev", "cum_df_r_dev",
                     "cum_ace_r_dev", "cum_sec_per_point_dev"],
    "E_context":    ["game_diff", "serving_for_set", "serving_to_stay", "games_in_set", "set_no"],
}

def prepare(tr, te, cols):
    """Winsorize at train 1/99 percentiles and add a missingness flag per partly-missing column.

    Filling a missing deviation with zero silently asserts "no deviation"; the flag lets the model
    separate that from "not measured", which on this data tracks which court the match was on.
    """
    Tr, Te = tr[cols].copy(), te[cols].copy()
    out = list(cols)
    for c in cols:
        lo, hi = Tr[c].quantile(0.01), Tr[c].quantile(0.99)
        if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
            Tr[c] = Tr[c].clip(lo, hi); Te[c] = Te[c].clip(lo, hi)
        miss = Tr[c].isna().mean()
        if miss > 0.01:
            Tr[c + "__na"] = Tr[c].isna().astype(float); Te[c + "__na"] = Te[c].isna().astype(float)
            out.append(c + "__na")
        med = Tr[c].median()
        Tr[c] = Tr[c].fillna(med); Te[c] = Te[c].fillna(med)
    return Tr[out].to_numpy(), Te[out].to_numpy(), out

def fit_eval(tr, te, cols, kind="lr"):
    Xtr, Xte, _ = prepare(tr, te, cols)
    if kind == "lr":
        clf = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=3000))
    else:
        clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05,
                                             max_leaf_nodes=15, l2_regularization=1.0)
    clf.fit(Xtr, tr["hold"])
    p = clf.predict_proba(Xte)[:, 1]
    return dict(logloss=float(log_loss(te["hold"], p)),
                brier=float(brier_score_loss(te["hold"], p)),
                auc=float(roc_auc_score(te["hold"], p))), clf, p

def boot_gain(te, pa, pb, n=1000, seed=0):
    """Cluster bootstrap by match of the per-game log-loss gain of pb over pa."""
    rng = np.random.default_rng(seed)
    y = te["hold"].to_numpy().astype(float)
    ll = lambda p: -(y*np.log(np.clip(p, 1e-6, 1-1e-6)) + (1-y)*np.log(np.clip(1-p, 1e-6, 1-1e-6)))
    d = ll(pa) - ll(pb)
    codes, uniq = pd.factorize(te["match_id"])
    s = np.bincount(codes, weights=d); c = np.bincount(codes)
    draws = [(lambda idx: s[idx].sum()/c[idx].sum())(rng.integers(0, len(uniq), len(uniq))) for _ in range(n)]
    return dict(mean=float(np.mean(draws)),
                ci95=[float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))])

def run(G, tag, split_year, res, do_gbdt=True):
    tr, te = G[G["year"] < split_year], G[G["year"] >= split_year]
    out = {"n_train": int(len(tr)), "n_test": int(len(te)),
           "test_matches": int(te["match_id"].nunique()), "hold_rate_test": float(te["hold"].mean())}
    print(f"\n=== {tag}: train {len(tr)}  test {len(te)} ({te['match_id'].nunique()} matches), "
          f"hold {te['hold'].mean():.4f}", flush=True)
    p0 = np.full(len(te), tr["hold"].mean())
    out["models"] = {"0_constant": dict(logloss=float(log_loss(te["hold"], p0)),
                                        brier=float(brier_score_loss(te["hold"], p0)), auc=0.5)}
    print(f"  constant            logloss={out['models']['0_constant']['logloss']:.4f}", flush=True)
    order = ["A_prior", "B_live_cum", "B2_live_opp", "C_prev_out", "D_prev_proc", "F_cum_proc", "E_context"]
    cols, preds = [], {}
    for g in order:
        cols = cols + GROUPS[g]
        r, _, p = fit_eval(tr, te, cols)
        out["models"][f"lr_cum_{g}"] = r; preds[g] = p
        print(f"  +{g:12s}      logloss={r['logloss']:.4f} brier={r['brier']:.4f} auc={r['auc']:.3f}", flush=True)
    allc = sum([GROUPS[g] for g in order], [])
    for g in order:
        r, _, _ = fit_eval(tr, te, [c for c in allc if c not in GROUPS[g]])
        out["models"][f"lr_full_minus_{g}"] = r
        print(f"  full - {g:12s} logloss={r['logloss']:.4f}", flush=True)
    r_full, clf, p_full = fit_eval(tr, te, allc)
    out["models"]["lr_full"] = r_full
    _, _, names = prepare(tr, te, allc)
    out["lr_full_std_coefs"] = dict(zip(names, [float(x) for x in clf.named_steps["logisticregression"].coef_[0]]))
    if do_gbdt:
        r_gb, _, _ = fit_eval(tr, te, allc, "gb"); out["models"]["gbdt_full"] = r_gb
        print(f"  GBDT full           logloss={r_gb['logloss']:.4f}", flush=True)
    # incremental bootstraps that answer the project's questions
    _, _, pA = fit_eval(tr, te, GROUPS["A_prior"])
    _, _, pAB = fit_eval(tr, te, GROUPS["A_prior"] + GROUPS["B_live_cum"])
    _, _, pABo = fit_eval(tr, te, GROUPS["A_prior"] + GROUPS["B_live_cum"] + GROUPS["B2_live_opp"])
    _, _, pABC = fit_eval(tr, te, GROUPS["A_prior"] + GROUPS["B_live_cum"] + GROUPS["C_prev_out"])
    _, _, pABD = fit_eval(tr, te, GROUPS["A_prior"] + GROUPS["B_live_cum"] + GROUPS["D_prev_proc"])
    _, _, pABF = fit_eval(tr, te, GROUPS["A_prior"] + GROUPS["B_live_cum"] + GROUPS["F_cum_proc"])
    _, _, pABDF = fit_eval(tr, te, GROUPS["A_prior"] + GROUPS["B_live_cum"] + GROUPS["D_prev_proc"] + GROUPS["F_cum_proc"])
    # crude prior (no Elo) for the head-to-head the audit asked about
    _, _, pCrude = fit_eval(tr, te, ["hist_hold", "surf_grass", "surf_clay"])
    for name, a, b in [("live_over_prior", pA, pAB),
                       ("returner_side_over_server_side", pAB, pABo),
                       ("prev_outcome_over_prior_live", pAB, pABC),
                       ("process_over_prior_live_and_opp", pABo,
                        fit_eval(tr, te, GROUPS["A_prior"] + GROUPS["B_live_cum"] + GROUPS["B2_live_opp"]
                                 + GROUPS["D_prev_proc"] + GROUPS["F_cum_proc"])[2]),
                       ("prev_process_over_prior_live", pAB, pABD),
                       ("cum_process_over_prior_live", pAB, pABF),
                       ("all_process_over_prior_live", pAB, pABDF),
                       ("elo_prior_over_crude_prior", pCrude, pA),
                       ("full_over_prior", pA, p_full)]:
        out[f"boot_{name}"] = boot_gain(te, a, b)
        g = out[f"boot_{name}"]
        print(f"  gain {name:32s} {g['mean']:+.5f}  CI [{g['ci95'][0]:+.5f}, {g['ci95'][1]:+.5f}]", flush=True)
    res[tag] = out
    return out

def main():
    import time
    cache = os.path.join(SLAM, "_slam_games.pkl")
    if os.path.exists(cache):
        G = pd.read_pickle(cache)
    else:
        t0 = time.time(); p = load_slam(SLAM)
        print(f"points: {len(p)} loaded in {time.time()-t0:.0f}s", flush=True)
        t0 = time.time(); G = build_games(p)
        print(f"service games: {len(G)} built in {time.time()-t0:.0f}s", flush=True)
        G.to_pickle(cache)
    print(f"games {len(G)}, matches {G['match_id'].nunique()}, hold {G['hold'].mean():.4f}", flush=True)
    G = attach_prior(G, PRIORS)
    print(f"prior joined for {G['p_blend'].notna().mean():.3f} of games", flush=True)
    G = add_features(G)
    G = G[G["has_prev"] == 1]
    res = {"n_games": int(len(G)), "prior_join_rate": float(G["p_blend"].notna().mean()),
           "years": [int(G['year'].min()), int(G['year'].max())]}
    # coverage of the process fields
    res["process_coverage"] = {c: float(G[f"prev_{c}"].notna().mean()) for c in PROC}
    print("process coverage:", {k: round(v, 3) for k, v in res["process_coverage"].items()}, flush=True)
    # The slam feed carries both draws. Hold rates differ by roughly ten points and the ATP prior
    # only joins to the men's draw, so the men's games are the primary population here.
    men = G[G["draw"] == "M"].copy()
    women = G[G["draw"] == "W"].copy()
    res["n_men"], res["n_women"] = int(len(men)), int(len(women))
    print(f"\nmen {len(men)} (hold {men['hold'].mean():.4f}), "
          f"women {len(women)} (hold {women['hold'].mean():.4f})", flush=True)
    run(men, "men_all", 2019, res)
    tracked = men[men["prev_speed1_mean"].notna() & men["prev_rally_mean"].notna()
                  & men["cum_speed1_mean"].notna()]
    print(f"\nmen, tracked subset: {len(tracked)} games", flush=True)
    run(tracked, "men_tracked", 2019, res)
    run(women, "women_all", 2019, res, do_gbdt=False)
    with open(OUT, "w") as f: json.dump(res, f, indent=1)
    print("\nwrote", OUT, flush=True)

if __name__ == "__main__":
    main()
